#!/usr/bin/env bash
# scripts/create_topics.sh
# Run ONCE after MSK cluster is provisioned.
# Usage: ./scripts/create_topics.sh <bootstrap-servers>

set -euo pipefail

BOOTSTRAP="${1:?Usage: $0 <bootstrap-servers>}"

echo "Creating Kafka topics on: $BOOTSTRAP"

# ingest.raw — primary ingestion topic
kafka-topics.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --create --if-not-exists \
  --topic ingest.raw \
  --replication-factor 3 \
  --partitions 6 \
  --config retention.ms=604800000 \
  --config compression.type=lz4 \
  --config min.insync.replicas=2

# dlq.ingestion.failed — dead letter queue (30-day retention)
kafka-topics.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --create --if-not-exists \
  --topic dlq.ingestion.failed \
  --replication-factor 3 \
  --partitions 3 \
  --config retention.ms=2592000000 \
  --config compression.type=lz4

echo ""
echo "✅ Topics created:"
kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list
