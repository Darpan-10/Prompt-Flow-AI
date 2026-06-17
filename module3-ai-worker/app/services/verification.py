"""
Cryptographic Integrity Verification.

RULES (from locked spec):
  1. Download file from content.attachments[0].storage_uri
  2. sha256(file_bytes) MUST == checksum_sha256
  3. sha256(raw_text.encode()) MUST == raw_text_hash
  If EITHER fails → BLOCK + DLQ, do not process further.
"""
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    passed: bool
    file_hash_ok: bool
    text_hash_ok: bool
    failure_reason: Optional[str] = None
    file_bytes: Optional[bytes] = None


def _download_from_s3(bucket: str, key: str) -> bytes:
    """Download file bytes from S3."""
    client = boto3.client("s3", region_name=settings.aws_region)
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except ClientError as e:
        raise RuntimeError(
            f"S3 download failed s3://{bucket}/{key}: {e.response['Error']['Message']}"
        )


def verify_integrity(
    raw_text: str,
    raw_text_hash: str,
    s3_bucket: str,
    s3_key: str,
    checksum_sha256: str,
) -> VerificationResult:
    """
    Perform dual cryptographic verification:
      1. Download file from S3 → sha256(bytes) == checksum_sha256
      2. sha256(raw_text.encode()) == raw_text_hash

    Returns VerificationResult. If either check fails, passed=False.
    """
    # ── Text hash check ───────────────────────────────────────────────────
    computed_text_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    text_hash_ok = computed_text_hash == raw_text_hash

    if not text_hash_ok:
        logger.error(
            "INTEGRITY_CHECK_FAILED: text hash mismatch | "
            "expected=%s computed=%s",
            raw_text_hash, computed_text_hash,
        )

    # ── File download + hash check ────────────────────────────────────────
    file_bytes = None
    file_hash_ok = False
    failure_reason = None

    try:
        file_bytes = _download_from_s3(bucket=s3_bucket, key=s3_key)
        computed_file_hash = hashlib.sha256(file_bytes).hexdigest()
        file_hash_ok = computed_file_hash == checksum_sha256

        if not file_hash_ok:
            logger.error(
                "INTEGRITY_CHECK_FAILED: file hash mismatch | "
                "expected=%s computed=%s | s3://%s/%s",
                checksum_sha256, computed_file_hash, s3_bucket, s3_key,
            )
    except Exception as e:
        failure_reason = f"S3 download error: {str(e)}"
        logger.error("INTEGRITY_CHECK_FAILED: %s", failure_reason)

    if not text_hash_ok:
        failure_reason = (failure_reason or "") + " text_hash_mismatch"
    if not file_hash_ok and not failure_reason:
        failure_reason = "file_checksum_mismatch"

    passed = text_hash_ok and file_hash_ok

    return VerificationResult(
        passed=passed,
        file_hash_ok=file_hash_ok,
        text_hash_ok=text_hash_ok,
        failure_reason=failure_reason.strip() if failure_reason else None,
        file_bytes=file_bytes if passed else None,
    )
