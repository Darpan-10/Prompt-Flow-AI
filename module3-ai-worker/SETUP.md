# Module 3: AI Extraction Worker — Setup Guide (Arch Linux)

## 🗂️ File Structure
```
module3-ai-worker/
├── app/
│   ├── main.py                        # FastAPI + worker thread
│   ├── config.py                      # Pydantic settings
│   ├── pipeline.py                    # 11-step pipeline orchestrator
│   ├── models/
│   │   └── schemas.py                 # IngestedPayload + PaperExtractedV1
│   ├── services/
│   │   ├── verification.py            # SHA256 dual integrity check
│   │   ├── kafka_client.py            # Consumer + producer
│   │   ├── idempotency.py             # Redis dedup guard
│   │   └── extraction/
│   │       ├── cascade.py             # 4-tier orchestrator
│   │       ├── tier1_regex.py         # DOI + Year regex (conf=0.95)
│   │       ├── tier2_crossref.py      # CrossRef API (conf=1.0)
│   │       ├── tier3_nlp.py           # spaCy NLP (conf=0.75)
│   │       └── tier4_bedrock.py       # AWS Bedrock LLM (cap=0.90)
│   ├── services/directory/
│   │   └── service.py                 # DirectoryService adapter + HTTP impl
│   └── routing/
│       └── engine.py                  # Deterministic routing engine
├── mock_directory/
│   ├── main.py                        # FastAPI mock on port 8080
│   └── Dockerfile
├── terraform/                         # AWS infrastructure
│   ├── main.tf / variables.tf / outputs.tf
│   └── modules/{vpc,rds,s3,msk,elasticache,cognito,iam,secrets}/
├── tests/
│   └── test_module3.py               # Full validation suite
├── requirements.txt
├── .env.example
├── Dockerfile
└── docker-compose.yml
```

---

## ⚙️ STEP 1 — Install System Dependencies (Arch Linux)

```bash
sudo pacman -S python python-pip docker docker-compose
sudo systemctl start docker && sudo systemctl enable docker
sudo usermod -aG docker $USER && newgrp docker
```

---

## ⚙️ STEP 2 — Project Setup

```bash
unzip module3-ai-worker.zip
cd module3-ai-worker

python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Download spaCy NLP model (required for Tier 3)
python -m spacy download en_core_web_sm
```

---

## ⚙️ STEP 3 — Configure .env

```bash
cp .env.example .env
nano .env
```

**Required values:**
```env
# Kafka — matches Module 2 topics
KAFKA_BOOTSTRAP_SERVERS=localhost:9093

# Redis
REDIS_URL=redis://:localdevtoken@localhost:6380

# AWS credentials (for Bedrock + S3)
AWS_REGION=ap-south-1

# Directory API (docker-compose provides this)
DIRECTORY_API_URL=http://localhost:8080
```

---

## ⚙️ STEP 4 — Configure AWS Credentials (for Bedrock)

```bash
aws configure
# Region: ap-south-1
# Access Key + Secret Key from your AWS console

# Verify Bedrock access
aws bedrock list-foundation-models --region ap-south-1 | grep haiku
```

---

## ⚙️ STEP 5 — Start Infrastructure (Docker)

```bash
# Start all services (Kafka, Redis, PostgreSQL, Mock Directory)
docker-compose up -d zookeeper kafka redis postgres mock-directory

# Wait for Kafka to be healthy (~30 sec)
docker-compose ps

# Create all Kafka topics
docker-compose up kafka-init

# Verify topics
docker exec m3_kafka kafka-topics --bootstrap-server localhost:9093 --list
# Should show: ingest.raw, papers.validated, papers.review, papers.failed, dlq.ingestion.failed
```

---

## ⚙️ STEP 6 — Run Tests

```bash
source venv/bin/activate
pytest tests/test_module3.py -v

# Expected: all PASSED
# Tests cover:
#   ✅ Routing logic (AUTO_SAVE, REVIEW, BLOCK)
#   ✅ Confidence formula
#   ✅ DOI regex
#   ✅ Bedrock gate + confidence cap
#   ✅ Schema validation
#   ✅ Hash verification
```

---

## ⚙️ STEP 7 — Run the Worker

```bash
# Option A: Local (no Docker)
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8002 --reload

# Option B: Docker
docker-compose up worker
```

Health check:
```bash
curl http://localhost:8002/health
curl http://localhost:8002/ready
```

---

## ⚙️ STEP 8 — Test Directory API

```bash
# Active faculty
curl http://localhost:8080/api/faculty/dr.smith
# {"faculty_name":"Dr. John Smith","faculty_email":"dr.smith@srmap.edu.in","department_code":"CSE","faculty_status":"active"}

# Inactive faculty
curl http://localhost:8080/api/faculty/dr.inactive
# {"faculty_status":"inactive",...}

# Not found
curl http://localhost:8080/api/faculty/unknown_xyz
# 404
```

---

## ⚙️ STEP 9 — Inject a Test Message to Kafka

```bash
# Publish a test paper.ingested.v1 event to ingest.raw
python3 scripts/inject_test_message.py
```

Or manually:
```bash
echo '{"event_id":"test-001","contract_version":"v1","pipeline_status":"ingested","created_at":"2026-06-10T13:00:00Z","email":{"message_id":"<test@srmap.edu.in>","subject":"Research Paper","sender":"dr.smith@srmap.edu.in","recipients":["papers@srmap.edu.in"],"received_at":"2026-06-10T13:00:00Z","idempotency_key":"abc123"},"content":{"raw_text":"This paper presents a novel approach to deep learning. DOI: 10.1145/3290605.3300501. Authors: John Smith, Jane Doe. Published in ACM SIGCHI Conference on Human Factors in Computing Systems 2023. The study demonstrates significant improvements over baseline methods in multiple benchmark datasets with extensive experimental validation.","raw_text_hash":"REPLACE_WITH_ACTUAL_HASH","attachments":[]},"security":{"pii_redacted":true,"source_domain_verified":true,"clamav_scanned":true,"clamav_result":"CLEAN"}}' | \
docker exec -i m3_kafka kafka-console-producer \
  --bootstrap-server localhost:29092 \
  --topic ingest.raw \
  --property "key.serializer=org.apache.kafka.common.serialization.StringSerializer"
```

Watch output:
```bash
docker exec m3_kafka kafka-console-consumer \
  --bootstrap-server localhost:29092 \
  --topic papers.validated \
  --from-beginning
```

---

## ⚙️ STEP 10 — AWS Terraform Deployment

```bash
cd terraform/

# One-time: bootstrap state backend (skip if already done for Module 2)
# aws s3api create-bucket --bucket promptflow-terraform-state-ap --region ap-south-1

terraform init

# Plan
terraform plan \
  -var-file=dev.tfvars \
  -var="db_password=$DB_PASSWORD" \
  -var="redis_auth_token=$(openssl rand -hex 32)"

# Apply
terraform apply \
  -var-file=dev.tfvars \
  -var="db_password=$DB_PASSWORD" \
  -var="redis_auth_token=$(openssl rand -hex 32)" \
  -auto-approve

# After apply — create MSK topics
BOOTSTRAP=$(terraform output -raw kafka_bootstrap_brokers)
../scripts/create_topics.sh "$BOOTSTRAP"

# Destroy
terraform destroy -var-file=dev.tfvars
```

---

## 🔐 Security Checklist

✅ Cryptographic verification: SHA256 of file bytes + text before processing
✅ BLOCK + DLQ on any hash mismatch — never processes corrupted data
✅ 4-tier cascade: Regex → CrossRef → NLP → LLM (strict order)
✅ Bedrock gate: only invoked if confidence < 0.70
✅ Bedrock input truncated to 1,500 tokens (cost control)
✅ Confidence cap: Bedrock results hard-capped at 0.90
✅ Faculty lookup: timeout=3s, retries=2, not_found → BLOCK
✅ Routing: deterministic, covers all 3 branches
✅ IAM: no wildcard permissions, specific resource ARNs
✅ Kafka: idempotent producer, manual offset commit, DLQ on failure

---

## 🆘 Troubleshooting

### spaCy model not found
```bash
python -m spacy download en_core_web_sm
```

### Bedrock: "AccessDeniedException"
```bash
# Enable claude-3-haiku in AWS Bedrock console:
# https://console.aws.amazon.com/bedrock → Model Access → Enable Anthropic models
aws bedrock list-foundation-models --region ap-south-1
```

### Kafka consumer not receiving messages
```bash
# Check consumer group lag
docker exec m3_kafka kafka-consumer-groups \
  --bootstrap-server localhost:29092 \
  --group module3-ai-extraction \
  --describe
```

### Directory API timeout
```bash
# Check mock directory is running
curl http://localhost:8080/health
docker-compose logs mock-directory
```
