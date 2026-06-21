"""
Module 4 – Kafka Consumer
Consumes paper.extracted.v1 from papers.{validated,review,failed}.
Stores papers + validation issues in PostgreSQL via async repository.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from confluent_kafka import Consumer, KafkaError, KafkaException
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.database import AsyncSessionFactory, set_rls_context
from app.models.schemas import (
    Author,
    Attachment,
    KafkaPayload,
    PaperCreate,
    ValidationIssueCreate,
)
from app.repository.repository import PaperRepository, ValidationIssueRepository
from app.services.embedding_service import generate_embedding, warm_up

log = logging.getLogger(__name__)


# ── Redis idempotency client ──────────────────────────────────────────────────

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(
            settings.REDIS_URL, decode_responses=True, encoding="utf-8"
        )
    return _redis


async def is_already_processed(key: str) -> bool:
    r = await get_redis()
    return await r.exists(f"m4:processed:{key}") == 1


async def mark_processed(key: str) -> None:
    r = await get_redis()
    await r.setex(
        f"m4:processed:{key}",
        settings.REDIS_PROCESSED_TTL_SECONDS,
        "1",
    )


# ── Message processor ─────────────────────────────────────────────────────────

def _map_topic_to_status(topic: str, action: str) -> str:
    """Derive the paper DB status from topic + routing final_action."""
    mapping = {
        "AUTO_SAVE": "PUBLISHED",
        "REVIEW_REQUIRED": "PENDING_REVIEW",
        "BLOCK": "REJECTED",
    }
    return mapping.get(action, "DRAFT")


def _build_paper_create(payload: KafkaPayload, topic: str) -> PaperCreate:
    """
    Build a PaperCreate from the REAL Module 3 payload shape.

    Field mapping confirmed against an actual papers.validated sample:
      - idempotency_key is TOP-LEVEL (not nested under "email")
      - extraction_id is TOP-LEVEL (not under extraction_result)
      - routing_decision.final_action (not "action")
      - extraction_result.embedding is always null from Module 3 --
        Module 4 generates it locally (see embedding step below)
    """
    meta = payload.extraction_result.get("metadata", {})
    ctx  = payload.enriched_context
    ref  = payload.content_reference

    # Authors
    raw_authors = meta.get("authors", [])
    if isinstance(raw_authors, str):
        raw_authors = [{"name": raw_authors, "order": 1}]
    authors = [
        Author(
            name=a.get("name", "Unknown"),
            affiliation=a.get("affiliation"),
            order=a.get("order", i + 1),
        )
        for i, a in enumerate(raw_authors)
    ]
    if not authors:
        authors = [Author(name="Unknown Author", order=1)]

    # Attachments
    raw_attachments = ref.get("attachments", [])
    attachments = [
        Attachment(
            filename=a.get("filename", "unknown"),
            uri=a.get("uri", a.get("s3_uri", "")),
            checksum_sha256=a.get("checksum_sha256", a.get("checksum", "0" * 64)),
        )
        for a in raw_attachments
    ]

    status = _map_topic_to_status(topic, payload.final_action)

    # idempotency_key is top-level in the real payload, always present
    idem_key = payload.idempotency_key
    idem_key = idem_key[:64].ljust(64, "0") if len(idem_key) < 64 else idem_key[:64]

    raw_text_hash = ref.get("raw_text_hash", "0" * 64)
    title = meta.get("title", "Untitled Paper")
    venue = meta.get("venue") or meta.get("journal")

    # ── Embedding generation (Module 4's job, per locked design) ──────────
    # Only generate when the paper is being PUBLISHED -- cost optimization.
    # REVIEW_REQUIRED / BLOCK papers get embedding=NULL and may be embedded
    # later if/when a human reviewer promotes them to PUBLISHED.
    embedding: Optional[list[float]] = None
    if status == "PUBLISHED":
        try:
            embedding = generate_embedding(title=title, venue=venue)
        except Exception as exc:
            # Never let an embedding failure block the paper from being
            # stored -- log loudly and continue with embedding=NULL.
            log.error(
                "Embedding generation failed for paper title=%r: %s",
                title[:60], exc, exc_info=True,
            )

    return PaperCreate(
        ingestion_idempotency_key=idem_key,
        extraction_id=uuid.UUID(payload.extraction_id),
        title=title,
        authors=authors,
        venue=venue,
        year=int(meta.get("year", 2024)),
        doi=meta.get("doi"),
        paper_type=meta.get("paper_type", "unknown"),
        faculty_id=uuid.UUID(str(ctx.get("faculty_id", uuid.uuid4()))),
        faculty_email=payload.faculty_email,
        department_code=payload.department_code,
        status=status,
        overall_confidence=payload.overall_confidence,
        raw_text_hash=raw_text_hash,
        attachment_uris=attachments,
        embedding=embedding,
    )


def _build_validation_issues(
    payload: KafkaPayload,
    paper_id: uuid.UUID,
) -> list[ValidationIssueCreate]:
    issues = []
    for item in payload.validation_issues:
        try:
            issues.append(
                ValidationIssueCreate(
                    paper_id=paper_id,
                    issue_code=item.get("code", "UNKNOWN"),
                    severity=item.get("severity", "warning"),
                    action=item.get("action", "REVIEW_REQUIRED"),
                    json_path=item.get("json_path"),
                    extracted_value=str(item.get("extracted_value", ""))[:500],
                    confidence=item.get("confidence"),
                    threshold=item.get("threshold"),
                    source=item.get("source", "module3"),
                    message=item.get("message", ""),
                )
            )
        except Exception as exc:
            log.warning("Skipping malformed validation_issue: %s – %s", item, exc)
    return issues


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _process_message(msg_value: bytes, topic: str) -> None:
    """Parse, validate, and persist a single Kafka message."""
    raw: Dict[str, Any] = json.loads(msg_value.decode("utf-8"))
    payload = KafkaPayload.model_validate(raw)

    idem_key = payload.idempotency_key

    # Redis-level idempotency (fast path)
    if await is_already_processed(idem_key):
        log.debug("Redis cache hit – skipping duplicate: %s", idem_key)
        return

    async with AsyncSessionFactory() as session:
        # Set RLS context for all DB ops inside this transaction
        await set_rls_context(
            session,
            department_code=payload.department_code,
            role="system",
            user_id="kafka-consumer",
            actor_type="system",
            change_reason="kafka_ingestion",
            trace_id=payload.trace_id_uuid or payload.event_id,
        )

        paper_repo = PaperRepository(session)
        issue_repo = ValidationIssueRepository(session)

        # DB-level idempotency (second fence)
        existing = await paper_repo.get_by_idempotency_key(idem_key)
        if existing:
            log.info("DB already has paper for key %s – skipping.", idem_key)
            await mark_processed(idem_key)
            return

        paper_data = _build_paper_create(payload, topic)
        paper = await paper_repo.create(paper_data)
        log.info(
            "Created paper %s | status=%s | dept=%s | topic=%s",
            paper.paper_id, paper.status, paper.department_code, topic,
        )

        # Persist validation issues
        issues = _build_validation_issues(payload, paper.paper_id)
        if issues:
            await issue_repo.bulk_create(issues)
            log.debug("Stored %d validation issues for %s", len(issues), paper.paper_id)

        await session.commit()

    await mark_processed(idem_key)


# ── Consumer loop ─────────────────────────────────────────────────────────────

async def run_consumer() -> None:
    """Main async consumer loop. Blocks until KeyboardInterrupt or fatal error."""
    # Warm up the embedding model BEFORE subscribing, so the first
    # PUBLISHED paper doesn't pay the ~1-2s model-load cost mid-transaction.
    log.info("Warming up embedding model (sentence-transformers/all-mpnet-base-v2)...")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, warm_up)
    log.info("Embedding model ready.")

    consumer = Consumer(settings.kafka_consumer_config)
    consumer.subscribe(settings.kafka_topics)
    log.info(
        "Module 4 Kafka consumer started. Topics: %s | Group: %s",
        settings.kafka_topics,
        settings.KAFKA_CONSUMER_GROUP,
    )

    try:
        while True:
            # Non-blocking poll; run in thread so we don't block the event loop
            msg = await loop.run_in_executor(None, consumer.poll, 1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    log.debug("End of partition: %s [%d]", msg.topic(), msg.partition())
                else:
                    log.error("Kafka consumer error: %s", msg.error())
                continue

            topic = msg.topic()
            log.debug("Received message from %s offset %d", topic, msg.offset())

            try:
                await _process_message(msg.value(), topic)
                # Manual commit only on success
                consumer.commit(asynchronous=False)
            except Exception as exc:
                log.error(
                    "Failed to process message from %s offset %d: %s",
                    topic, msg.offset(), exc, exc_info=True,
                )
                # Don't commit – message will be redelivered

    except KeyboardInterrupt:
        log.info("Consumer interrupted, shutting down...")
    except KafkaException as exc:
        log.critical("Fatal Kafka error: %s", exc, exc_info=True)
        raise
    finally:
        consumer.close()
        log.info("Kafka consumer closed.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(run_consumer())
