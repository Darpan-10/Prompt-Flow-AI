"""
Hashing utilities for Module 2.

STRICT SEPARATION:
  checksum_sha256 = SHA256(raw file bytes)
  raw_text_hash   = SHA256(PII-redacted text)

These MUST NEVER be computed from the same input.
"""
import hashlib


def compute_file_checksum(file_bytes: bytes) -> str:
    """
    SHA256 of raw file bytes.
    Used as: AttachmentInfo.checksum_sha256
    Input: raw bytes BEFORE any processing.
    """
    if not isinstance(file_bytes, bytes):
        raise TypeError("file_bytes must be raw bytes")
    return hashlib.sha256(file_bytes).hexdigest()


def compute_text_hash(redacted_text: str) -> str:
    """
    SHA256 of PII-redacted text.
    Used as: ContentBlock.raw_text_hash
    Input: text AFTER PII redaction — NEVER raw text.
    """
    if not isinstance(redacted_text, str):
        raise TypeError("redacted_text must be a string")
    return hashlib.sha256(redacted_text.encode("utf-8")).hexdigest()


def compute_idempotency_key(message_id: str, primary_filename: str) -> str:
    """
    Deterministic idempotency key.
    Formula: SHA256(f"{message_id}:{primary_filename}")
    Used as: Kafka message key + Redis dedup key.
    """
    raw = f"{message_id}:{primary_filename}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
