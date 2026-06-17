"""
Kafka Consumer + Producer for Module 3.

Consumer: ingest.raw (group: module3-ai-extraction)
Producer: papers.validated | papers.review | papers.failed | dlq.ingestion.failed

Producer config: enable.idempotence=true, acks=all (same as Module 2)
"""
import json
import logging
import time
from typing import Optional

from confluent_kafka import Consumer, Producer, KafkaException, KafkaError, Message

from app.config import settings

logger = logging.getLogger(__name__)

_producer: Optional[Producer] = None
_consumer: Optional[Consumer] = None


def _base_kafka_config() -> dict:
    config = {
        "bootstrap.servers": settings.kafka_bootstrap_servers,
    }
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
        config = _base_kafka_config()
        config.update({
            "enable.idempotence": True,
            "acks": "all",
            "max.in.flight.requests.per.connection": 5,
            "retries": 2147483647,
            "compression.type": "lz4",
            "delivery.timeout.ms": 120000,
            "linger.ms": 5,
        })
        _producer = Producer(config)
        logger.info("Kafka producer initialized")
    return _producer


def get_consumer() -> Consumer:
    global _consumer
    if _consumer is None:
        config = _base_kafka_config()
        config.update({
            "group.id": settings.kafka_consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,       # Manual commit after processing
            "max.poll.interval.ms": 300000,    # 5 min for long extractions
            "session.timeout.ms": 30000,
        })
        _consumer = Consumer(config)
        _consumer.subscribe([settings.kafka_topic_ingest_raw])
        logger.info(
            "Kafka consumer subscribed to '%s' (group: %s)",
            settings.kafka_topic_ingest_raw,
            settings.kafka_consumer_group,
        )
    return _consumer


def _delivery_report(err, msg):
    if err:
        logger.error(
            "Kafka delivery FAILED — topic: %s | key: %s | error: %s",
            msg.topic(), msg.key(), str(err),
        )
    else:
        logger.info(
            "Kafka delivery OK — topic: %s | key: %s | offset: %d",
            msg.topic(), msg.key(), msg.offset(),
        )


def publish_to_topic(
    topic: str,
    payload: bytes,
    idempotency_key: str,
    max_retries: int = 3,
) -> bool:
    """Publish with retry + exponential backoff. Returns True on success."""
    producer = get_producer()

    for attempt in range(1, max_retries + 1):
        try:
            producer.produce(
                topic=topic,
                key=idempotency_key.encode("utf-8"),
                value=payload,
                on_delivery=_delivery_report,
            )
            producer.flush(timeout=30)
            return True
        except KafkaException as e:
            backoff = 2 ** attempt
            logger.warning(
                "Kafka produce attempt %d/%d failed for topic '%s': %s — retrying in %ds",
                attempt, max_retries, topic, str(e), backoff,
            )
            if attempt < max_retries:
                time.sleep(backoff)

    logger.error(
        "All %d Kafka produce attempts failed for topic '%s' key '%s'",
        max_retries, topic, idempotency_key,
    )
    return False


def publish_to_dlq(
    payload: bytes,
    idempotency_key: str,
    reason: str,
) -> None:
    """Route failed events to DLQ. Never silently drops."""
    producer = get_producer()
    dlq_payload = json.dumps({
        "failed_at": time.time(),
        "idempotency_key": idempotency_key,
        "reason": reason,
        "original_payload": payload.decode("utf-8", errors="replace")[:2000],
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
            "DLQ: Message routed to '%s' — key: %s | reason: %s",
            settings.kafka_topic_dlq, idempotency_key, reason,
        )
    except Exception as e:
        logger.critical(
            "CRITICAL: DLQ publish failed for key '%s': %s",
            idempotency_key, str(e),
        )
