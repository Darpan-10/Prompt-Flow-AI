"""
S3 Uploader — multipart upload for files > 5MB.
Handles both ingestion bucket and quarantine bucket.
"""

import logging
import boto3
from botocore.exceptions import ClientError
from typing import Optional
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)


def _s3_client():
    return boto3.client("s3", region_name=settings.aws_region)


def upload_attachment(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    idempotency_key: str,
    message_id: str,
) -> str:
    """
    Upload attachment to ingestion bucket.
    Uses multipart upload for files > 5MB.
    Returns S3 object key.
    """
    now = datetime.now(timezone.utc)
    # Partition by date for lifecycle management
    s3_key = (
        f"attachments/"
        f"{now.strftime('%Y/%m/%d')}/"
        f"{idempotency_key[:8]}/"
        f"{filename}"
    )

    client = _s3_client()

    metadata = {
        "idempotency-key": idempotency_key,
        "message-id": message_id[:1024],  # S3 metadata limit
        "upload-timestamp": now.isoformat(),
    }

    if len(file_bytes) >= settings.s3_multipart_threshold_bytes:
        _multipart_upload(
            client=client,
            bucket=settings.s3_ingestion_bucket,
            key=s3_key,
            file_bytes=file_bytes,
            content_type=content_type,
            metadata=metadata,
        )
        logger.info(
            "Multipart uploaded %s (%d bytes) to s3://%s/%s",
            filename, len(file_bytes), settings.s3_ingestion_bucket, s3_key,
        )
    else:
        client.put_object(
            Bucket=settings.s3_ingestion_bucket,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
            Metadata=metadata,
            ServerSideEncryption="AES256",
        )
        logger.info(
            "Uploaded %s (%d bytes) to s3://%s/%s",
            filename, len(file_bytes), settings.s3_ingestion_bucket, s3_key,
        )

    return s3_key


def quarantine_file(
    file_bytes: bytes,
    filename: str,
    reason: str,
    idempotency_key: str,
) -> str:
    """
    Upload infected or suspicious file to quarantine bucket.
    Emits CloudWatch metric: MalwareDetected.
    Returns quarantine S3 key.
    """
    now = datetime.now(timezone.utc)
    s3_key = (
        f"quarantine/"
        f"{now.strftime('%Y/%m/%d')}/"
        f"{idempotency_key[:8]}/"
        f"{filename}"
    )

    client = _s3_client()

    client.put_object(
        Bucket=settings.s3_quarantine_bucket,
        Key=s3_key,
        Body=file_bytes,
        ContentType="application/octet-stream",
        Metadata={
            "quarantine-reason": reason[:1024],
            "idempotency-key": idempotency_key,
            "quarantined-at": now.isoformat(),
        },
        ServerSideEncryption="AES256",
    )

    logger.warning(
        "QUARANTINED %s → s3://%s/%s | reason: %s",
        filename, settings.s3_quarantine_bucket, s3_key, reason,
    )

    # Emit CloudWatch metric
    _emit_malware_metric(filename=filename, reason=reason)

    return s3_key


def _multipart_upload(
    client,
    bucket: str,
    key: str,
    file_bytes: bytes,
    content_type: str,
    metadata: dict,
) -> None:
    """Perform a multipart S3 upload in 5MB chunks."""
    PART_SIZE = 5 * 1024 * 1024  # 5MB

    mpu = client.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        ContentType=content_type,
        Metadata=metadata,
        ServerSideEncryption="AES256",
    )
    upload_id = mpu["UploadId"]
    parts = []

    try:
        offset = 0
        part_number = 1
        while offset < len(file_bytes):
            chunk = file_bytes[offset : offset + PART_SIZE]
            response = client.upload_part(
                Bucket=bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=chunk,
            )
            parts.append({"PartNumber": part_number, "ETag": response["ETag"]})
            offset += PART_SIZE
            part_number += 1

        client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except Exception as e:
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        logger.error("Multipart upload aborted for %s: %s", key, str(e))
        raise


def _emit_malware_metric(filename: str, reason: str) -> None:
    """Emit CloudWatch metric for malware detection."""
    try:
        cw = boto3.client("cloudwatch", region_name=settings.aws_region)
        cw.put_metric_data(
            Namespace="PromptFlow/EmailWorker",
            MetricData=[
                {
                    "MetricName": "MalwareDetected",
                    "Value": 1,
                    "Unit": "Count",
                    "Dimensions": [
                        {"Name": "Environment", "Value": settings.app_env},
                        {"Name": "Reason", "Value": reason[:256]},
                    ],
                }
            ],
        )
        logger.info("Emitted CloudWatch MalwareDetected metric for: %s", filename)
    except Exception as e:
        logger.error("Failed to emit CloudWatch metric: %s", str(e))
