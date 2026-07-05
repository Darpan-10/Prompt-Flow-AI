"""
Module 6 – Checksum Service
SHA-256 calculation and verification for tamper-proof report integrity.

Matches the locked spec verbatim -- this is the simplest, most
load-bearing piece of the whole module: every report MUST have a
verifiable checksum, and that checksum must be computed from the EXACT
bytes that get uploaded to S3, not from some intermediate representation.
"""

from __future__ import annotations

import hashlib


class ChecksumService:
    @staticmethod
    def calculate_sha256(file_bytes: bytes) -> str:
        """Calculate SHA-256 checksum of file bytes."""
        return hashlib.sha256(file_bytes).hexdigest()

    @staticmethod
    def verify_checksum(file_bytes: bytes, expected_hash: str) -> bool:
        """Verify file integrity against stored checksum."""
        actual = hashlib.sha256(file_bytes).hexdigest()
        return actual == expected_hash
