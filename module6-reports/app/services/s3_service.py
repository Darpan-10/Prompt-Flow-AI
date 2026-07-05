"""
Module 6 – S3 Service
Upload report bytes to S3 and generate time-limited pre-signed download
URLs. The file bytes themselves are NEVER stored in PostgreSQL -- only
the S3 URI and checksum live in the database (per locked spec).
"""

from __future__ import annotations

import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)


class S3Service:
    def __init__(self, s3_client=None, region: str = "ap-south-1", endpoint_url: Optional[str] = None):
        self.client = s3_client or boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url or None,
        )

    async def upload_file(
        self,
        bucket: str,
        key: str,
        file_bytes: bytes,
        content_type: str,
    ) -> str:
        """
        Upload file bytes to S3. Returns the s3:// URI.

        boto3's S3 client is synchronous; in a FastAPI BackgroundTasks
        context (which already runs off the request/response cycle) this
        is acceptable without wrapping in run_in_executor -- the
        background task itself doesn't block any client-facing request.
        """
        try:
            self.client.put_object(
                Bucket=bucket,
                Key=key,
                Body=file_bytes,
                ContentType=content_type,
                ServerSideEncryption="aws:kms",
            )
        except ClientError as exc:
            log.error("S3 upload failed: bucket=%s key=%s error=%s", bucket, key, exc)
            raise

        return f"s3://{bucket}/{key}"

    def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        expiry_seconds: int = 3600,
    ) -> str:
        """Generate a time-limited pre-signed GET URL (default 1hr, per locked spec)."""
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiry_seconds,
            )
        except ClientError as exc:
            log.error("Pre-signed URL generation failed: bucket=%s key=%s error=%s", bucket, key, exc)
            raise

    @staticmethod
    def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
        """Split 's3://bucket/key/path.pdf' into (bucket, key)."""
        if not s3_uri.startswith("s3://"):
            raise ValueError(f"Not a valid s3:// URI: {s3_uri}")
        without_scheme = s3_uri.removeprefix("s3://")
        bucket, _, key = without_scheme.partition("/")
        if not bucket or not key:
            raise ValueError(f"Could not parse bucket/key from: {s3_uri}")
        return bucket, key

    async def download_file(self, bucket: str, key: str) -> bytes:
        """Download file bytes -- used by checksum re-verification, not
        by the normal download flow (which uses pre-signed URLs so the
        client downloads directly from S3, not through this service)."""
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            log.error("S3 download failed: bucket=%s key=%s error=%s", bucket, key, exc)
            raise
