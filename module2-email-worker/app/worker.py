"""
Module 2: Email Ingestion Worker — Main Pipeline

PROCESS FLOW (in strict order):
  1.  Fetch UNSEEN emails (Gmail API)
  2.  Parse MIME (text + attachments)
  3.  Validate domain (@srmap.edu.in)
  4.  Check Redis dedupe
  5.  Redact PII (MANDATORY before hashing/storage)
  6.  ClamAV scan
  7.  Upload to S3
  8.  Compute hashes
  9.  Validate schema (PaperIngestedV1)
  10. Publish Kafka event
  11. Store Message-ID in Redis (TTL=7d)
"""

import logging
import time
import socket
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.services.gmail_auth import (
    build_gmail_service,
    fetch_unread_messages,
    mark_message_read,
    decode_raw_message,
)
from app.services.email_parser import parse_email, ParsedEmail
from app.services.pii_redactor import redact_pii
from app.services.clamav import scan_bytes, ScanResult
from app.services.s3_uploader import upload_attachment, quarantine_file
from app.services.redis_dedup import is_duplicate, mark_processed
from app.services.kafka_producer import publish_event
from app.services.audit import log_audit
from app.utils.hashing import (
    compute_file_checksum,
    compute_text_hash,
    compute_idempotency_key,
)
from app.models.events import (
    PaperIngestedV1,
    EmailMetadata,
    ContentBlock,
    AttachmentInfo,
    ProcessingBlock,
    SecurityBlock,
)

logger = logging.getLogger(__name__)

ALLOWED_DOMAIN = settings.allowed_domain


def _validate_domain(email_address: str) -> bool:
    """Hard domain lock — reject anything not @srmap.edu.in."""
    if not email_address:
        return False
    normalized = email_address.lower().strip()
    return normalized.endswith(f"@{ALLOWED_DOMAIN}")


def _get_worker_id() -> str:
    return f"worker-{socket.gethostname()}"


def process_single_email(raw_msg: dict, gmail_service) -> bool:
    """
    Process one email through the full 11-step pipeline.
    Returns True on success, False on skip or failure.
    """
    # ── STEP 1: Parse MIME ──────────────────────────────────────────────────
    try:
        email_msg = decode_raw_message(raw_msg)
        parsed = parse_email(email_msg)
    except Exception as e:
        logger.error("MIME parse failed for msg id %s: %s", raw_msg.get("id"), str(e))
        return False

    logger.info("Processing email: %s | From: %s", parsed.message_id, parsed.sender)

    # ── STEP 2: Validate domain ──────────────────────────────────────────────
    if not _validate_domain(parsed.sender):
        logger.warning(
            "DOMAIN REJECTED: sender '%s' is not @%s — skipping",
            parsed.sender, ALLOWED_DOMAIN,
        )
        log_audit(
            action="EMAIL_DOMAIN_REJECTED",
            resource_type="ingestion",
            resource_id=parsed.message_id,
            details={"sender": parsed.sender},
        )
        mark_message_read(gmail_service, raw_msg["id"])
        return False

    # Determine primary attachment filename for idempotency key
    primary_filename = (
        parsed.attachments[0][0] if parsed.attachments else "no-attachment"
    )
    idempotency_key = compute_idempotency_key(parsed.message_id, primary_filename)

    # ── STEP 3: Redis dedup check ────────────────────────────────────────────
    if is_duplicate(
        message_id=parsed.message_id,
        idempotency_key=idempotency_key,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
    ):
        logger.info("DUPLICATE skipped: %s", parsed.message_id)
        mark_message_read(gmail_service, raw_msg["id"])
        return False

    # ── STEP 4: Redact PII (MANDATORY FIRST) ────────────────────────────────
    redaction = redact_pii(parsed.body_text)
    redacted_text = redaction.redacted_text

    if len(redacted_text.strip()) < 50:
        logger.warning(
            "Email body too short after redaction (%d chars) — skipping: %s",
            len(redacted_text.strip()), parsed.message_id,
        )
        mark_message_read(gmail_service, raw_msg["id"])
        return False

    # Compute text hash AFTER redaction
    raw_text_hash = compute_text_hash(redacted_text)

    # ── STEP 5–7: Scan + Upload attachments ─────────────────────────────────
    attachment_infos = []
    clamav_scanned = True
    clamav_result = "CLEAN"

    for filename, content_type, file_bytes in parsed.attachments:
        if len(file_bytes) > settings.max_attachment_size_bytes:
            logger.warning(
                "Attachment '%s' exceeds size limit (%d bytes) — skipping",
                filename, len(file_bytes),
            )
            continue

        # Compute file checksum BEFORE any processing (raw bytes)
        checksum = compute_file_checksum(file_bytes)

        # Verify hashes never collide
        if checksum == raw_text_hash:
            logger.critical(
                "HASH COLLISION: checksum_sha256 == raw_text_hash for '%s' — aborting",
                filename,
            )
            return False

        # ClamAV scan
        scan = scan_bytes(file_bytes, filename=filename)

        if scan.requires_quarantine:
            quarantine_file(
                file_bytes=file_bytes,
                filename=filename,
                reason=scan.virus_name or scan.error_message or "scan_failed",
                idempotency_key=idempotency_key,
            )
            clamav_scanned = True
            clamav_result = scan.result.value
            logger.warning(
                "Attachment quarantined — NOT publishing to Kafka: %s",
                parsed.message_id,
            )
            log_audit(
                action="ATTACHMENT_QUARANTINED",
                resource_type="ingestion",
                resource_id=parsed.message_id,
                details={
                    "filename": filename,
                    "reason": scan.virus_name or scan.error_message or "scan_failed",
                },
            )
            # Per policy: do NOT publish to Kafka if any attachment is infected
            return False

        # Upload clean file to S3
        s3_key = upload_attachment(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            idempotency_key=idempotency_key,
            message_id=parsed.message_id,
        )

        attachment_infos.append(AttachmentInfo(
            filename=filename,
            content_type=content_type,
            size_bytes=len(file_bytes),
            checksum_sha256=checksum,
            s3_key=s3_key,
            s3_bucket=settings.s3_ingestion_bucket,
        ))

    # ── STEP 8: Build and validate event schema ──────────────────────────────
    try:
        received_at = datetime.now(timezone.utc)

        event = PaperIngestedV1(
            email=EmailMetadata(
                message_id=parsed.message_id,
                thread_id=parsed.in_reply_to,
                subject=parsed.subject,
                sender=parsed.sender,
                recipients=parsed.recipients,
                received_at=received_at,
                idempotency_key=idempotency_key,
            ),
            content=ContentBlock(
                raw_text=redacted_text,
                raw_text_hash=raw_text_hash,
                attachments=attachment_infos,
            ),
            processing=ProcessingBlock(
                attempts=[],
                worker_id=_get_worker_id(),
            ),
            security=SecurityBlock(
                pii_redacted=True,
                source_domain_verified=True,
                clamav_scanned=clamav_scanned,
                clamav_result=clamav_result,
            ),
        )
    except Exception as e:
        logger.error(
            "Event schema validation FAILED for %s: %s",
            parsed.message_id, str(e),
        )
        return False

    # ── STEP 9: Publish to Kafka ─────────────────────────────────────────────
    success = publish_event(
        payload=event.to_kafka_payload(),
        idempotency_key=idempotency_key,
    )

    if not success:
        logger.error("Kafka publish failed for %s — DLQ routed", parsed.message_id)
        return False

    # ── STEP 10: Mark processed in Redis ────────────────────────────────────
    mark_processed(
        message_id=parsed.message_id,
        idempotency_key=idempotency_key,
        in_reply_to=parsed.in_reply_to,
    )

    # Mark as read in Gmail
    mark_message_read(gmail_service, raw_msg["id"])

    logger.info(
        "✅ Successfully ingested: %s | key: %s | attachments: %d",
        parsed.message_id, idempotency_key, len(attachment_infos),
    )
    log_audit(
        action="EMAIL_INGESTED",
        resource_type="ingestion",
        resource_id=parsed.message_id,
        details={"idempotency_key": idempotency_key, "attachment_count": len(attachment_infos)},
    )
    return True


def run_worker_loop():
    """
    Main polling loop. Runs indefinitely, polling Gmail every N seconds.
    """
    logger.info(
        "Starting Email Ingestion Worker | domain: @%s | poll interval: %ds",
        ALLOWED_DOMAIN, settings.poll_interval_seconds,
    )

    gmail_service = build_gmail_service()

    while True:
        try:
            messages = fetch_unread_messages(gmail_service)

            if not messages:
                logger.debug("No unread messages found")
            else:
                logger.info("Processing %d unread messages", len(messages))
                success_count = 0
                for raw_msg in messages:
                    if process_single_email(raw_msg, gmail_service):
                        success_count += 1
                logger.info(
                    "Batch complete — %d/%d processed successfully",
                    success_count, len(messages),
                )

        except Exception as e:
            logger.error("Worker loop error: %s", str(e), exc_info=True)

        time.sleep(settings.poll_interval_seconds)
