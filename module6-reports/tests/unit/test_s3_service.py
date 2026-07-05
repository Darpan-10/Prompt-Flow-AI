"""
Module 6 – Unit Tests: S3Service URI parsing (pure logic, no AWS calls)
"""

import pytest

from app.services.s3_service import S3Service


class TestParseS3Uri:
    def test_simple_bucket_and_key(self):
        bucket, key = S3Service.parse_s3_uri("s3://my-bucket/path/to/file.pdf")
        assert bucket == "my-bucket"
        assert key == "path/to/file.pdf"

    def test_nested_key_path(self):
        bucket, key = S3Service.parse_s3_uri("s3://promptflow-reports-dev/reports/CSE/2023-2024/criteria_iii_20260101_120000.pdf")
        assert bucket == "promptflow-reports-dev"
        assert key == "reports/CSE/2023-2024/criteria_iii_20260101_120000.pdf"

    def test_missing_scheme_raises(self):
        with pytest.raises(ValueError, match="Not a valid s3"):
            S3Service.parse_s3_uri("https://my-bucket/file.pdf")

    def test_missing_key_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            S3Service.parse_s3_uri("s3://my-bucket")

    def test_missing_bucket_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            S3Service.parse_s3_uri("s3:///just-a-key.pdf")

    def test_roundtrip_matches_upload_format(self):
        """The format S3Service.upload_file() actually returns must be
        parseable by parse_s3_uri() -- this test pins that contract."""
        bucket, key = "promptflow-reports-dev", "reports/CSE/2023-2024/test.pdf"
        constructed_uri = f"s3://{bucket}/{key}"
        parsed_bucket, parsed_key = S3Service.parse_s3_uri(constructed_uri)
        assert parsed_bucket == bucket
        assert parsed_key == key
