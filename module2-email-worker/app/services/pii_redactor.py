"""
PII Redaction Service — MANDATORY FIRST STEP before hashing or storage.

EXACT patterns from spec:
  Phone:       \\b(?:\\+91[-\\s]?)?[0]?(?:\\d{2,3}[-\\s]?\\d{3,4}[-\\s]?\\d{4})\\b
  Student ID:  \\bAP\\d{10}\\b
  Ext. Email:  \\b[A-Za-z0-9._%+-]+@(?!srmap\\.edu\\.in)[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- EXACT patterns from specification ---

_PHONE_PATTERN = re.compile(
    r"\b(?:\+91[-\s]?)?[0]?(?:\d{2,3}[-\s]?\d{3,4}[-\s]?\d{4})\b"
)

_STUDENT_ID_PATTERN = re.compile(
    r"\bAP\d{10}\b"
)

_EXTERNAL_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@(?!srmap\.edu\.in)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# --- Replacement tokens ---
_PHONE_REPLACEMENT      = "[PHONE_REDACTED]"
_STUDENT_ID_REPLACEMENT = "[STUDENT_ID_REDACTED]"
_EMAIL_REPLACEMENT      = "[EMAIL_REDACTED]"


@dataclass
class RedactionResult:
    redacted_text: str
    phone_count: int
    student_id_count: int
    external_email_count: int

    @property
    def total_redactions(self) -> int:
        return self.phone_count + self.student_id_count + self.external_email_count

    @property
    def pii_found(self) -> bool:
        return self.total_redactions > 0


def redact_pii(raw_text: str) -> RedactionResult:
    """
    Apply PII redaction to raw text.
    Order matters: emails first (prevents partial phone matches in URLs).
    Returns RedactionResult with redacted text and counts.

    MUST be called BEFORE any hashing or S3 storage of text content.
    """
    if not raw_text:
        return RedactionResult(
            redacted_text=raw_text,
            phone_count=0,
            student_id_count=0,
            external_email_count=0,
        )

    text = raw_text

    # 1. Redact external emails first
    external_emails = _EXTERNAL_EMAIL_PATTERN.findall(text)
    text, email_count = _EXTERNAL_EMAIL_PATTERN.subn(_EMAIL_REPLACEMENT, text)

    # 2. Redact student IDs
    text, student_id_count = _STUDENT_ID_PATTERN.subn(_STUDENT_ID_REPLACEMENT, text)

    # 3. Redact phone numbers
    text, phone_count = _PHONE_PATTERN.subn(_PHONE_REPLACEMENT, text)

    if email_count or student_id_count or phone_count:
        logger.info(
            "PII redacted — phones: %d, student_ids: %d, emails: %d",
            phone_count, student_id_count, email_count,
        )

    return RedactionResult(
        redacted_text=text,
        phone_count=phone_count,
        student_id_count=student_id_count,
        external_email_count=email_count,
    )
