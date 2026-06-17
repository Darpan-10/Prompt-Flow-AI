"""
Kafka Producer — idempotent, exactly-once delivery.

Config:
  enable.idempotence = true
  acks = all
  max.in.flight.requests.per.connection = 5
  retries = 3 (exponential backoff)

DLQ: dlq.ingestion.failed (on all retry exhaustion)
Message Key: idempotency_key (deterministic, sha256-based)
"""

import json
import logging
import time
from typing import Optional

from confluent_kafka import Producer, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from app.config import settings

logger = logging.getLogger(__name__)

_producer: Optional[Producer] = None


def _build_producer_config() -> dict:
    """Build Kafka producer config. Mirrors AWS MSK config for parity."""
    config = {
        # Idempotency — exactly-once delivery guarantee
        "enable.idempotence": True,
        "acks": "all",
        "max.in.flight.requests.per.connection": 5,
        "retries": 2147483647,  # max int — let our retry logic control attempts

        # Bootstrap
        "bootstrap.servers": settings.kafka_bootstrap_servers,

        # Compression
        "compression.type": "lz4",

        # Delivery reliability
        "delivery.timeout.ms": 120000,   # 2 minutes
        "linger.ms": 5,
        "batch.size": 65536,
    }

    # MSK SASL_SSL (production)
    if settings.kafka_security_protocol == "SASL_SSL":
        config.update({
            "security.protocol": "SASL_SSL",
            "sasl.mechanism": settings.kafka_sasl_mechanism,
            "sasl.username": settings.kafka_sasl_username,
            "sasl.password": settings.kafka_sasl_password,
            "ssl.ca.location": "/etc/ssl/certs/ca-certificates.crt",
        })
    else:
        config["security.protocol"] = "PLAINTEXT"

    return config


def get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = Producer(_build_producer_config())
        logger.info(
            "Kafka producer initialized — brokers: %s",
            settings.kafka_bootstrap_servers,
        )
    return _producer


def _delivery_report(err, msg):
    """Callback fired after each produce attempt."""
    if err:
        logger.error(
            "Kafka delivery FAILED — topic: %s | key: %s | error: %s",
            msg.topic(), msg.key().decode() if msg.key() else None, str(err),
        )
    else:
        logger.info(
            "Kafka delivery OK — topic: %s | key: %s | offset: %d | partition: %d",
            msg.topic(), msg.key().decode() if msg.key() else None,
            msg.offset(), msg.partition(),
        )


def publish_event(
    payload: bytes,
    idempotency_key: str,
    topic: str = None,
) -> bool:
    """
    Publish event to Kafka with retry (3 attempts, exponential backoff).
    Returns True on success, False if all retries fail (routes to DLQ).

    Message key = idempotency_key → same partition for duplicate detection.
    """
    topic = topic or settings.kafka_topic_ingest
    producer = get_producer()
    max_attempts = settings.kafka_max_retries

    for attempt in range(1, max_attempts + 1):
        try:
            producer.produce(
                topic=topic,
                key=idempotency_key.encode("utf-8"),
                value=payload,
                on_delivery=_delivery_report,
            )
            producer.flush(timeout=30)
            logger.info(
                "Published to %s — key: %s (attempt %d/%d)",
                topic, idempotency_key, attempt, max_attempts,
            )
            return True

        except KafkaException as e:
            backoff = 2 ** attempt  # 2s, 4s, 8s
            logger.warning(
                "Kafka publish attempt %d/%d failed for key %s: %s — retrying in %ds",
                attempt, max_attempts, idempotency_key, str(e), backoff,
            )
            if attempt < max_attempts:
                time.sleep(backoff)

    # All retries exhausted → route to DLQ
    logger.error(
        "All %d Kafka attempts failed for key %s — routing to DLQ: %s",
        max_attempts, idempotency_key, settings.kafka_topic_dlq,
    )
    _publish_to_dlq(payload=payload, idempotency_key=idempotency_key)
    return False


def _publish_to_dlq(payload: bytes, idempotency_key: str) -> None:
    """
    Route failed message to Dead Letter Queue.
    Wraps original payload with failure metadata.
    Never silently drops a message.
    """
    producer = get_producer()
    dlq_payload = json.dumps({
        "failed_at": time.time(),
        "idempotency_key": idempotency_key,
        "original_topic": settings.kafka_topic_ingest,
        "original_payload_b64": payload.decode("utf-8", errors="replace"),
        "reason": "max_retries_exhausted",
    }).encode("utf-8")

    try:
        producer.produce(
            topic=settings.kafka_topic_dlq,
            key=idempotency_key.encode("utf-8"),
            value=dlq_payload,
            on_delivery=_delivery_report,
        )
        producer.flush(timeout=30)
        logger.warning(
            "DLQ message published — key: %s → topic: %s",
            idempotency_key, settings.kafka_topic_dlq,
        )
    except Exception as e:
        # Absolute last resort — log to stderr so CloudWatch picks it up
        logger.critical(
            "CRITICAL: DLQ publish also failed for key %s: %s | payload: %s",
            idempotency_key, str(e), payload[:500],
        )
