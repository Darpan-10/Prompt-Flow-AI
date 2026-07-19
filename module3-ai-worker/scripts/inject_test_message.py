#!/usr/bin/env python3
"""
Module 3: Test Message Injector
Injects realistic test messages into the ingest.raw Kafka topic (from Module 2).

Usage:
    python3 inject_test_message.py              # Single test message
    python3 inject_test_message.py --bulk 10    # 10 test messages
    python3 inject_test_message.py --help       # Show options
"""

import json
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    # pyrefly: ignore [missing-import]
    from confluent_kafka import Producer
    # pyrefly: ignore [missing-import]
    from confluent_kafka.error import KafkaError
except ImportError:
    print("❌ Error: confluent-kafka not installed")
    print("   Run: pip install confluent-kafka")
    exit(1)

# Colors for output
GREEN = '\033[0;32m'
BLUE = '\033[0;34m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
NC = '\033[0m'  # No Color


def log_info(msg: str):
    print(f"{BLUE}ℹ️  {msg}{NC}")


def log_success(msg: str):
    print(f"{GREEN}✅ {msg}{NC}")


def log_error(msg: str):
    print(f"{RED}❌ {msg}{NC}")


def log_warn(msg: str):
    print(f"{YELLOW}⚠️  {msg}{NC}")


def generate_test_message(paper_id: int = 1) -> dict:
    """Generate a realistic test message matching Module 2 output schema."""
    
    # Realistic test papers with DOI and proper metadata
    test_papers = [
        {
            "title": "A Comprehensive Survey on Deep Learning for Image Recognition",
            "authors": "John Smith, Jane Doe, Prof. Venkata Rao",
            "text": """
This paper presents a comprehensive survey of deep learning techniques for image recognition. 
Recent advances in convolutional neural networks (CNNs) have significantly improved accuracy on 
large-scale image classification tasks. DOI: 10.1145/3290605.3300501. Published in ACM SIGCHI 2023.
Our approach uses ResNet-50 architecture with batch normalization and achieves 95% accuracy 
on ImageNet. The model was trained on 1.2 million images using distributed GPU training.
            """,
            "sender": "dr.smith@srmap.edu.in",
        },
        {
            "title": "Machine Learning Applications in Healthcare: A Review",
            "authors": "Dr. Priya Mehta, Prof. Rao, Research Team",
            "text": """
Machine learning has revolutionized healthcare diagnostics and treatment planning.
This paper reviews state-of-the-art ML applications in medical imaging, drug discovery, and 
personalized medicine. DOI: 10.1016/j.neunet.2023.01.005. Published in Neural Networks 2023.
We analyze 150+ peer-reviewed papers and identify key trends in AI-driven healthcare.
            """,
            "sender": "dr.mehta@srmap.edu.in",
        },
        {
            "title": "Quantum Computing: Future of Cryptography",
            "authors": "Inactive Researcher",
            "text": """
Quantum computers pose significant threats to current cryptographic systems.
This research explores post-quantum cryptography algorithms and their implementation.
DOI: 10.1109/TQE.2023.3245678. Published in IEEE Quantum Electronics 2023.
We propose novel lattice-based key exchange protocols.
            """,
            "sender": "dr.inactive@srmap.edu.in",
        },
        {
            "title": "No DOI Paper - Testing Tier 2 Fallback",
            "authors": "Anonymous Author",
            "text": """
This paper discusses natural language processing without providing a DOI.
It focuses on transformer architectures and their applications in machine translation.
Published in ArXiv preprint server. Uses BERT and GPT-based models.
            """,
            "sender": "papers@srmap.edu.in",
        },
    ]
    
    # Select paper (cycle through test papers)
    paper = test_papers[paper_id % len(test_papers)]
    
    # Generate IDs
    event_id = f"test-event-{paper_id:04d}-{uuid.uuid4().hex[:8]}"
    idempotency_key = f"test-{paper_id:04d}-{uuid.uuid4().hex[:8]}"
    message_id = f"<{uuid.uuid4().hex}@srmap.edu.in>"
    
    # Calculate hashes
    raw_text = paper["text"].strip()
    raw_text_hash = hashlib.sha256(raw_text.encode()).hexdigest()
    
    # Timestamp
    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Build the payload matching Module 2 output schema
    payload = {
        "event_id": event_id,
        "contract_version": "v1",
        "pipeline_status": "ingested",
        "created_at": timestamp,
        "email": {
            "message_id": message_id,
            "subject": paper["title"],
            "sender": paper["sender"],
            "recipients": ["papers@srmap.edu.in"],
            "received_at": timestamp,
            "idempotency_key": idempotency_key,
        },
        "content": {
            "raw_text": raw_text,
            "raw_text_hash": raw_text_hash,
            "attachments": [
                {
        "filename": "paper.pdf",
                    "content_type": "application/pdf",
                    "url": "https://example.com/paper.pdf",
                    "size_bytes": 102400,
                    "checksum_sha256": "dummychecksum1234567890abcdef",
                    "s3_key": "dummy/path/paper.pdf",
                    "s3_bucket": "dummy-bucket"
                }
            ],
        }, 
        "security": {
            "pii_redacted": True,
            "source_domain_verified": True,
            "clamav_scanned": True,
            "clamav_result": "CLEAN",
        },
    }
    
    return payload


def send_message_to_kafka(payload: dict, bootstrap_servers: str = "localhost:9093") -> bool:
    """Send test message to Kafka ingest.raw topic."""
    
    def delivery_report(err, msg):
        if err is not None:
            log_error(f"Message delivery failed: {err}")
            return False
        else:
            log_success(f"Message delivered to {msg.topic()} partition {msg.partition()}")
            return True
    
    try:
        # Create Kafka producer
        producer = Producer({
            'bootstrap.servers': bootstrap_servers,
            'client.id': 'test-injector',
            'acks': 'all',
            'retries': 3,
        })
        
        # Serialize payload
        message_json = json.dumps(payload)
        
        # Send message
        log_info(f"Sending test message to ingest.raw (event_id: {payload['event_id']})...")
        
        producer.produce(
            topic='ingest.raw',
            key=payload['event_id'].encode(),
            value=message_json.encode(),
            callback=delivery_report,
        )
        
        # Wait for delivery
        producer.flush(timeout=10)
        
        log_success("Message sent successfully!")
        return True
        
    except Exception as e:
        log_error(f"Failed to send message: {e}")
        log_warn("Ensure Kafka is running: docker compose ps kafka")
        return False


def print_test_summary(payload: dict):
    """Print summary of test message."""
    print("")
    print("=" * 70)
    print("📨 TEST MESSAGE SUMMARY")
    print("=" * 70)
    print(f"Event ID:          {payload['event_id']}")
    print(f"Paper:             {payload['email']['subject']}")
    print(f"Sender:            {payload['email']['sender']}")
    print(f"Idempotency Key:   {payload['email']['idempotency_key']}")
    print(f"Text Hash:         {payload['content']['raw_text_hash'][:16]}...")
    print(f"Text Length:       {len(payload['content']['raw_text'])} chars")
    print(f"Security Status:   {payload['security']}")
    print("")
    print("Expected Behavior:")
    print("  1. Message routed through Module 3 extraction pipeline")
    print("  2. Metadata extracted via 4-tier cascade")
    print("  3. Faculty status checked (should be active)")
    print("  4. Routed to papers.validated (high confidence)")
    print("")
    print("Monitor with:")
    print("  docker exec m4_kafka kafka-console-consumer \\")
    print("    --bootstrap-server localhost:29092 \\")
    print("    --topic papers.validated \\")
    print("    --from-beginning")
    print("=" * 70)
    print("")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Inject test messages into Module 3 Kafka pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 inject_test_message.py                    # Single test
  python3 inject_test_message.py --bulk 5           # 5 messages
  python3 inject_test_message.py --server kafka:9093  # Custom Kafka server
        """,
    )
    
    parser.add_argument(
        '--bulk',
        type=int,
        default=1,
        help='Number of test messages to inject (default: 1)',
    )
    
    parser.add_argument(
        '--server',
        type=str,
        default='localhost:9093',
        help='Kafka bootstrap servers (default: localhost:9093)',
    )
    
    parser.add_argument(
        '--no-summary',
        action='store_true',
        help='Skip printing test summary',
    )
    
    args = parser.parse_args()
    
    print("")
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  Module 3: Test Message Injector                             ║")
    print("╚═══════════════════════════════════════════════════════════════╝")
    print("")
    
    # Validate Kafka connection
    log_info(f"Testing Kafka connection to {args.server}...")
    try:
        test_producer = Producer({'bootstrap.servers': args.server})
        test_producer.flush(timeout=2)
        log_success(f"Connected to Kafka at {args.server}")
    except Exception as e:
        log_error(f"Cannot connect to Kafka: {e}")
        log_warn("Make sure Docker services are running:")
        log_warn("  docker compose up -d zookeeper kafka")
        print("")
        return False
    
    # Inject messages
    success_count = 0
    for i in range(args.bulk):
        print("")
        log_info(f"Generating test message {i+1}/{args.bulk}...")
        
        payload = generate_test_message(paper_id=i)
        
        if not args.no_summary and args.bulk == 1:
            print_test_summary(payload)
        
        if send_message_to_kafka(payload, bootstrap_servers=args.server):
            success_count += 1
        else:
            log_error(f"Failed to send message {i+1}")
    
    # Summary
    print("")
    print("╔═══════════════════════════════════════════════════════════════╗")
    if success_count == args.bulk:
        print(f"║  ✅ Successfully injected {success_count}/{args.bulk} messages!        ║")
    else:
        print(f"║  ⚠️  Injected {success_count}/{args.bulk} messages (some failed)          ║")
    print("╚═══════════════════════════════════════════════════════════════╝")
    print("")
    
    if success_count > 0:
        print("📊 Next Steps:")
        print("")
        print("  1. Watch extraction in progress:")
        print("     docker exec m4_kafka kafka-console-consumer \\")
        print("       --bootstrap-server localhost:29092 \\")
        print("       --topic papers.validated \\")
        print("       --from-beginning")
        print("")
        print("  2. Check worker logs:")
        print("     docker compose logs -f worker")
        print("")
        print("  3. Query review queue (if low confidence):")
        print("     docker exec m4_kafka kafka-console-consumer \\")
        print("       --bootstrap-server localhost:29092 \\")
        print("       --topic papers.review \\")
        print("       --from-beginning")
        print("")
    
    return success_count == args.bulk


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
