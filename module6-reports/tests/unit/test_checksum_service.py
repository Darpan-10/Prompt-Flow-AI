"""
Module 6 – Unit Tests: ChecksumService
"""

import hashlib

from app.services.checksum_service import ChecksumService


class TestChecksumService:
    def test_calculate_sha256_matches_hashlib_directly(self):
        data = b"hello world, this is a test PDF byte stream"
        expected = hashlib.sha256(data).hexdigest()
        assert ChecksumService.calculate_sha256(data) == expected

    def test_calculate_sha256_is_deterministic(self):
        data = b"same bytes every time"
        assert ChecksumService.calculate_sha256(data) == ChecksumService.calculate_sha256(data)

    def test_different_bytes_produce_different_checksums(self):
        c1 = ChecksumService.calculate_sha256(b"version one")
        c2 = ChecksumService.calculate_sha256(b"version two")
        assert c1 != c2

    def test_single_byte_difference_changes_checksum(self):
        """The whole point of SHA-256 for tamper detection: even a
        single flipped byte must produce a completely different hash."""
        c1 = ChecksumService.calculate_sha256(b"AAAA")
        c2 = ChecksumService.calculate_sha256(b"AAAB")
        assert c1 != c2

    def test_empty_bytes_produces_known_sha256(self):
        # SHA-256 of empty string is a well-known constant -- good sanity check
        assert ChecksumService.calculate_sha256(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_checksum_length_is_always_64_hex_chars(self):
        for data in [b"", b"x", b"a" * 10000]:
            assert len(ChecksumService.calculate_sha256(data)) == 64

    def test_verify_checksum_true_for_matching_hash(self):
        data = b"a real PDF's worth of bytes, pretend"
        expected_hash = ChecksumService.calculate_sha256(data)
        assert ChecksumService.verify_checksum(data, expected_hash) is True

    def test_verify_checksum_false_for_tampered_bytes(self):
        original = b"the original report content"
        tampered = b"the original report content!"  # one char added
        original_hash = ChecksumService.calculate_sha256(original)
        assert ChecksumService.verify_checksum(tampered, original_hash) is False

    def test_verify_checksum_false_for_wrong_hash(self):
        data = b"some content"
        wrong_hash = "0" * 64
        assert ChecksumService.verify_checksum(data, wrong_hash) is False

    def test_verify_checksum_case_sensitivity(self):
        """hexdigest() is always lowercase -- an uppercase expected_hash
        should NOT match, since this is a strict byte-level integrity
        check, not a case-insensitive comparison."""
        data = b"test data"
        correct_hash = ChecksumService.calculate_sha256(data)
        uppercase_hash = correct_hash.upper()
        assert ChecksumService.verify_checksum(data, uppercase_hash) is False
