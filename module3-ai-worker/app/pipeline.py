"""
Module 3: AI Extraction Worker — Main Pipeline.

Steps (in strict order):
  1.  Consume message from ingest.raw
  2.  Check idempotency (Redis) — skip if already processed
  3.  Validate schema (IngestedPayload Pydantic v2)
  4.  Cryptographic verification (SHA256 file + text)
  5.  Faculty lookup (Directory API)
  6.  4-Tier extraction cascade (Regex → CrossRef → NLP → Bedrock)
  7.  Build validation issues
  8.  Deterministic routing (AUTO_SAVE / REVIEW_REQUIRED / BLOCK)
  9.  Produce paper.extracted.v1 to target Kafka topic
  10. Mark as processed in Redis
  11. Commit Kafka offset
"""
import json
import logging
import asyncio
import socket
from typing import List

from pydantic import ValidationError

from app.config import settings
from app.models.schemas import (
    IngestedPayload,
    PaperExtractedV1,
    PipelineStatus,
    ValidationIssue,
    FacultyStatus,
    RoutingDecision,
)
from app.services.verification import verify_integrity
from app.services.extraction.cascade import run_extraction_cascade
from app.services.directory.service import get_directory_service
from app.services.kafka_client import (
    get_consumer,
    publish_to_topic,
    publish_to_dlq,
)
from app.services.idempotency import is_already_processed, mark_as_processed
from app.routing.engine import compute_routing

logger = logging.getLogger(__name__)


def _get_worker_id() -> str:
    return f"m3-{socket.gethostname()}-{settings.worker_id}"


def _build_validation_issues(
    payload: IngestedPayload,
    enriched_context,
) -> List[ValidationIssue]:
    """Build list of validation issues from payload and context checks."""
    issues: List[ValidationIssue] = []

    # No attachments
    if not payload.content.attachments:
        issues.append(ValidationIssue(
            code="NO_ATTACHMENTS",
            message="Email has no file attachments to process",
            action="REVIEW_REQUIRED",
        ))

    # PII not redacted
    if not payload.security.pii_redacted:
        issues.append(ValidationIssue(
            code="PII_NOT_REDACTED",
            message="Security flag pii_redacted=False — cannot process",
            action="BLOCK",
        ))

    # Domain not verified
    if not payload.security.source_domain_verified:
        issues.append(ValidationIssue(
            code="DOMAIN_NOT_VERIFIED",
            message="source_domain_verified=False",
            action="BLOCK",
        ))

    # Malware not clean
    if payload.security.clamav_result != "CLEAN":
        issues.append(ValidationIssue(
            code="MALWARE_FLAG",
            message=f"ClamAV result: {payload.security.clamav_result}",
            action="BLOCK",
        ))

    # Faculty not found or inactive — flag for routing engine
    if enriched_context.faculty_status in (
        FacultyStatus.not_found, FacultyStatus.inactive
    ):
        issues.append(ValidationIssue(
            code="FACULTY_INVALID",
            message=(
                f"Faculty '{enriched_context.faculty_id}' status="
                f"'{enriched_context.faculty_status.value}'"
            ),
            action="BLOCK",
        ))

    return issues


async def process_message(raw_value: bytes, raw_key: bytes) -> None:
    """
    Process a single Kafka message through the full Module 3 pipeline.
    """
    idempotency_key = raw_key.decode("utf-8") if raw_key else "unknown"

    # ── Step 1: Idempotency check ─────────────────────────────────────────
    if is_already_processed(idempotency_key):
        logger.info("SKIP (already processed): %s", idempotency_key)
        return

    # ── Step 2: Deserialise + schema validation ───────────────────────────
    try:
        data = json.loads(raw_value.decode("utf-8"))
        payload = IngestedPayload.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as e:
        logger.error(
            "Schema validation FAILED for key '%s': %s — routing to DLQ",
            idempotency_key, str(e),
        )
        publish_to_dlq(
            payload=raw_value,
            idempotency_key=idempotency_key,
            reason=f"schema_validation_failed: {str(e)[:500]}",
        )
        return

    source_event_id = payload.event_id
    # Use payload idempotency_key as authoritative key
    idempotency_key = payload.email.idempotency_key

    # ── Step 3: Cryptographic verification ───────────────────────────────
    attachment = payload.content.attachments[0] if payload.content.attachments else None

    if attachment:
        verification = verify_integrity(
            raw_text=payload.content.raw_text,
            raw_text_hash=payload.content.raw_text_hash,
            s3_bucket=attachment.s3_bucket,
            s3_key=attachment.s3_key,
            checksum_sha256=attachment.checksum_sha256,
        )
    else:
        # Text-only email — only verify text hash
        import hashlib
        computed = hashlib.sha256(
            payload.content.raw_text.encode("utf-8")
        ).hexdigest()
        text_ok = computed == payload.content.raw_text_hash

        from app.services.verification import VerificationResult
        verification = VerificationResult(
            passed=text_ok,
            file_hash_ok=True,   # no file to check
            text_hash_ok=text_ok,
            failure_reason=None if text_ok else "text_hash_mismatch",
        )

    if not verification.passed:
        logger.error(
            "INTEGRITY_CHECK_FAILED for key '%s': %s",
            idempotency_key, verification.failure_reason,
        )
        failed_event = _build_failed_event(
            source_event_id=source_event_id,
            idempotency_key=idempotency_key,
            reason="INTEGRITY_CHECK_FAILED",
            payload=payload,
        )
        publish_to_dlq(
            payload=failed_event.to_kafka_payload(),
            idempotency_key=idempotency_key,
            reason=f"integrity_check_failed: {verification.failure_reason}",
        )
        return

    # ── Step 4: Faculty lookup ────────────────────────────────────────────
    # Extract faculty_id from sender email (local part)
    sender_email = payload.email.sender
    faculty_id = sender_email.split("@")[0] if "@" in sender_email else sender_email

    directory_service = get_directory_service()
    enriched_context = await directory_service.get_faculty(faculty_id)

    # ── Step 5: 4-Tier extraction cascade ────────────────────────────────
    extracted_metadata, overall_confidence = run_extraction_cascade(
        payload.content.raw_text
    )

    # ── Step 6: Build validation issues ───────────────────────────────────
    validation_issues = _build_validation_issues(payload, enriched_context)

    # ── Step 7: Deterministic routing ─────────────────────────────────────
    routing = compute_routing(
        enriched_context=enriched_context,
        overall_confidence=overall_confidence,
        validation_issues=validation_issues,
    )

    # ── Step 8: Build output event ────────────────────────────────────────
    pipeline_status = (
        PipelineStatus.blocked
        if routing.final_action == RoutingDecision.BLOCK
        else PipelineStatus.extracted
    )

    output_event = PaperExtractedV1.build(
        idempotency_key=idempotency_key,
        extracted_metadata=extracted_metadata,
        overall_confidence=overall_confidence,
        enriched_context=enriched_context,
        routing=routing,
        validation_issues=validation_issues,
        pipeline_status=pipeline_status,
        raw_text_hash=payload.content.raw_text_hash,
        attachments=payload.content.attachments,
        worker_id=_get_worker_id(),
        source_event_id=source_event_id,
    )

    # ── Step 9: Publish to target topic ───────────────────────────────────
    success = publish_to_topic(
        topic=routing.target_topic,
        payload=output_event.to_kafka_payload(),
        idempotency_key=idempotency_key,
    )

    if not success:
        publish_to_dlq(
            payload=output_event.to_kafka_payload(),
            idempotency_key=idempotency_key,
            reason="kafka_produce_failed_all_retries",
        )
        return

    # ── Step 10: Mark processed ───────────────────────────────────────────
    mark_as_processed(idempotency_key)

    logger.info(
        "✅ Processed: %s | action=%s | topic=%s | confidence=%.4f",
        idempotency_key,
        routing.final_action.value,
        routing.target_topic,
        overall_confidence.score,
    )


def _build_failed_event(
    source_event_id: str,
    idempotency_key: str,
    reason: str,
    payload: IngestedPayload,
) -> PaperExtractedV1:
    """Build a failed/blocked PaperExtractedV1 for DLQ routing."""
    from app.models.schemas import (
        ExtractedMetadata, ExtractedAuthors, OverallConfidence,
        EnrichedContext, RoutingDecisionBlock,
    )

    zero_conf = OverallConfidence(
        score=0.0, authors_score=0.0, title_score=0.0, venue_year_score=0.0
    )

    return PaperExtractedV1.build(
        idempotency_key=idempotency_key,
        extracted_metadata=ExtractedMetadata(
            authors=ExtractedAuthors(),
            title_confidence=0.0,
            venue_year_confidence=0.0,
        ),
        overall_confidence=zero_conf,
        enriched_context=EnrichedContext(
            faculty_id="00000000-0000-0000-0000-000000000000",
            faculty_status=FacultyStatus.not_found,
        ),
        routing=RoutingDecisionBlock(
            final_action=RoutingDecision.BLOCK,
            reasons=[reason],
            target_topic=settings.kafka_topic_dlq,
            confidence_threshold_used=settings.default_confidence_threshold,
            overall_confidence=0.0,
        ),
        validation_issues=[ValidationIssue(
            code="INTEGRITY_CHECK_FAILED",
            message=reason,
            action="BLOCK",
        )],
        pipeline_status=PipelineStatus.failed,
        raw_text_hash=payload.content.raw_text_hash,
        attachments=payload.content.attachments,
        worker_id=_get_worker_id(),
        source_event_id=source_event_id,
    )


def run_worker_loop() -> None:
    """Main polling loop — consumes from ingest.raw indefinitely."""
    logger.info(
        "Module 3 worker starting | consumer_group=%s | topic=%s",
        settings.kafka_consumer_group,
        settings.kafka_topic_ingest_raw,
    )

    consumer = get_consumer()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logger.debug("Reached end of partition")
                else:
                    logger.error("Kafka consumer error: %s", msg.error())
                continue

            try:
                loop.run_until_complete(
                    process_message(
                        raw_value=msg.value(),
                        raw_key=msg.key(),
                    )
                )
                # Manual commit after successful processing
                consumer.commit(message=msg)

            except Exception as e:
                logger.error(
                    "Unhandled error processing message key '%s': %s",
                    msg.key(), str(e), exc_info=True,
                )
                # Still commit to avoid infinite retry loop on corrupt messages
                consumer.commit(message=msg)

    except KeyboardInterrupt:
        logger.info("Worker shutting down...")
    finally:
        consumer.close()
        loop.close()


# Needed for import in worker.py
from confluent_kafka import KafkaError
