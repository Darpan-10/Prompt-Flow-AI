# Module 2: Email Ingestion Worker — Setup Guide (Arch Linux)

## 📋 What You Need to Do (In Order)

### STEP 1 — Install System Dependencies

```bash
# Python 3.12 (already done from Module 1)
python3.12 --version

# Docker + Docker Compose
sudo pacman -S docker docker-compose
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
newgrp docker  # Apply group change without logout
```

---

### STEP 2 — Google Cloud Setup (Gmail OAuth2)

This is the most important step. Do it carefully.

#### 2a. Create Google Cloud Project
```
1. Go to: https://console.cloud.google.com
2. Create new project: "promptflow-srmap"
3. Enable Gmail API:
   → APIs & Services → Library → Search "Gmail API" → Enable
```

#### 2b. Create Service Account
```
1. IAM & Admin → Service Accounts → Create Service Account
2. Name: promptflow-email-worker
3. Click "Create and Continue" → Skip roles → Done
4. Click the service account → Keys → Add Key → JSON
5. Download the JSON file → KEEP IT SAFE
```

#### 2c. Enable Domain-Wide Delegation
```
1. Click the service account → Details tab
2. Enable "Enable Google Workspace Domain-wide Delegation"
3. Note the Client ID (numeric, e.g. 123456789012345678901)
```

#### 2d. Google Workspace Admin Console (your IT admin must do this)
```
1. Go to: https://admin.google.com
2. Security → API Controls → Domain-wide Delegation
3. Click "Add new" → Enter Client ID from step 2c
4. OAuth Scopes: https://www.googleapis.com/auth/gmail.readonly
5. Authorize
```

#### 2e. Set the JSON in your .env
```bash
# Convert JSON file to single line and add to .env
cat service-account.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))" > sa_oneline.txt

# Copy contents of sa_oneline.txt into .env as:
# GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
```

---

### STEP 3 — Project Setup

```bash
# Extract zip
unzip module2-email-worker.zip
cd module2-email-worker

# Create Python 3.12 virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

### STEP 4 — Environment Configuration

```bash
cp .env.example .env
nano .env  # Fill in your values
```

Key values to set:
```env
GOOGLE_SERVICE_ACCOUNT_JSON=<paste single-line JSON here>
GMAIL_DELEGATED_USER=papers@srmap.edu.in
REDIS_URL=redis://:localdevtoken@localhost:6379
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
S3_INGESTION_BUCKET=promptflow-ingestion-dev
S3_QUARANTINE_BUCKET=promptflow-quarantine-dev
```

---

### STEP 5 — Start Local Infrastructure (Docker)

```bash
# Start all services: Kafka, Zookeeper, Redis, PostgreSQL, ClamAV
docker-compose up -d postgres redis zookeeper kafka clamav

# Wait for services to be healthy (~2 min for ClamAV to load signatures)
docker-compose ps

# Check Kafka is ready
docker-compose logs kafka | grep "started"

# Create Kafka topics
docker-compose up kafka-init
```

Verify topics created:
```bash
docker exec promptflow_kafka kafka-topics \
  --bootstrap-server localhost:9092 --list
# Should show: ingest.raw, dlq.ingestion.failed
```

---

### STEP 6 — AWS S3 Buckets (Local Testing)

For local testing, create buckets using LocalStack OR real AWS:

#### Option A: Real AWS (recommended)
```bash
# Configure AWS credentials
aws configure
# Region: ap-south-1

# Create buckets
aws s3 mb s3://promptflow-ingestion-dev --region ap-south-1
aws s3 mb s3://promptflow-quarantine-dev --region ap-south-1

# Block public access
aws s3api put-public-access-block \
  --bucket promptflow-ingestion-dev \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

#### Option B: LocalStack (no AWS account needed)
```bash
# Add to docker-compose (or run separately)
pip install awscli-local
docker run -d -p 4566:4566 localstack/localstack

# Create buckets on localstack
awslocal s3 mb s3://promptflow-ingestion-dev
awslocal s3 mb s3://promptflow-quarantine-dev

# Update .env:
# AWS_ENDPOINT_URL=http://localhost:4566
```

---

### STEP 7 — Run Tests

```bash
source venv/bin/activate
pytest tests/test_validation.py -v
```

Expected output:
```
test_domain_validation_accepts_srmap           PASSED
test_domain_validation_rejects_non_srmap       PASSED
test_domain_validation_rejects_subdomain_spoof PASSED
test_pii_redaction_phone_numbers               PASSED
test_pii_redaction_student_ids                 PASSED
test_pii_redaction_external_emails             PASSED
test_pii_redaction_preserves_srmap_email       PASSED
test_pii_redaction_multiple_patterns           PASSED
test_hash_separation                           PASSED
test_idempotency_key_is_deterministic          PASSED
test_idempotency_key_formula                   PASSED
test_event_schema_valid                        PASSED
test_event_schema_rejects_wrong_contract_version PASSED
test_event_schema_hash_collision_rejected      PASSED
...
```

---

### STEP 8 — Run the Worker

```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Or with Docker:
```bash
docker-compose up worker
```

Health check:
```bash
curl http://localhost:8001/health
curl http://localhost:8001/ready
```

---

### STEP 9 — AWS Production Deployment (Terraform)

#### 9a. Bootstrap Terraform state (run ONCE)
```bash
chmod +x scripts/bootstrap_terraform_state.sh
./scripts/bootstrap_terraform_state.sh
```

#### 9b. Initialize Terraform
```bash
cd terraform/
terraform init
```

#### 9c. Plan
```bash
terraform plan \
  -var-file=dev.tfvars \
  -var="db_password=$DB_PASSWORD" \
  -var="redis_auth_token=$REDIS_TOKEN" \
  -var="imap_password=$IMAP_PASSWORD"
```

#### 9d. Apply
```bash
terraform apply \
  -var-file=dev.tfvars \
  -var="db_password=$DB_PASSWORD" \
  -var="redis_auth_token=$REDIS_TOKEN" \
  -var="imap_password=$IMAP_PASSWORD" \
  -auto-approve
```

#### 9e. Create Kafka topics on MSK
```bash
BOOTSTRAP=$(terraform output -raw kafka_bootstrap_brokers)
chmod +x ../scripts/create_topics.sh
../scripts/create_topics.sh "$BOOTSTRAP"
```

#### 9f. Destroy when done
```bash
terraform destroy -var-file=dev.tfvars
```

---

## 🗂️ Complete File Structure

```
module2-email-worker/
├── app/
│   ├── main.py                    # FastAPI health + worker thread
│   ├── config.py                  # Pydantic settings
│   ├── worker.py                  # 11-step pipeline orchestrator
│   ├── models/
│   │   └── events.py              # paper.ingested.v1 strict schema
│   ├── services/
│   │   ├── gmail_auth.py          # OAuth2 service account (headless)
│   │   ├── email_parser.py        # MIME parsing
│   │   ├── pii_redactor.py        # PII redaction (3 regex patterns)
│   │   ├── clamav.py              # ZINSTSTREAM malware scan
│   │   ├── s3_uploader.py         # Multipart upload + quarantine
│   │   ├── kafka_producer.py      # Idempotent producer + DLQ
│   │   └── redis_dedup.py         # 7-day dedup TTL
│   └── utils/
│       └── hashing.py             # SHA256 (file vs text — strict separation)
├── terraform/
│   ├── main.tf                    # Root module
│   ├── backend.tf                 # S3 + DynamoDB state
│   ├── variables.tf               # Input variables
│   ├── outputs.tf                 # Outputs
│   ├── dev.tfvars                 # Dev environment values
│   └── modules/
│       ├── vpc/                   # VPC, 3 private subnets, NAT
│       ├── rds/                   # PostgreSQL 15, IAM auth, private
│       ├── elasticache/           # Redis 7, auth, encrypted
│       ├── msk/                   # Kafka, SASL, private subnets
│       ├── s3/                    # Ingestion + quarantine + lifecycle
│       ├── cognito/               # User pool + M2M client
│       ├── iam/                   # Least-privilege worker role
│       └── secrets/               # Secrets Manager (no hardcoded values)
├── tests/
│   ├── conftest.py
│   └── test_validation.py         # All hard constraint validations
├── scripts/
│   ├── create_topics.sh           # MSK topic creation
│   └── bootstrap_terraform_state.sh # One-time state backend setup
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── SETUP.md
```

---

## 🔐 Security Checklist

✅ Domain lock: @srmap.edu.in only — all else rejected
✅ PII redacted BEFORE hashing/storage (3 regex patterns)
✅ checksum_sha256 ≠ raw_text_hash (enforced by schema)
✅ Idempotency: SHA256(message_id:filename) — deterministic
✅ ClamAV ZINSTSTREAM scan — infected → quarantine, no Kafka publish
✅ Kafka: enable.idempotence=true, acks=all
✅ DLQ: dlq.ingestion.failed — never silently drops messages
✅ Redis dedup: Message-ID, In-Reply-To, References (7-day TTL)
✅ S3: versioning, AES256, block public access, NAAC lifecycle
✅ IAM: no wildcard (*) permissions — specific ARNs only
✅ Secrets Manager: no hardcoded credentials anywhere
✅ RDS + Redis + MSK: private subnets only, no public IPs

---

## 🧪 Verify All Hard Constraints

```bash
# Run full validation suite
pytest tests/test_validation.py -v

# Check Kafka topics exist
docker exec promptflow_kafka \
  kafka-topics --bootstrap-server localhost:9092 --list

# Check Redis connectivity
redis-cli -a localdevtoken ping

# Check ClamAV is scanning
echo "X5O!P%@AP[4\\PZX54(P^)7CC)7}\$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!\$H+H*" | \
  nc localhost 3310

# Check health endpoint
curl http://localhost:8001/health | python3 -m json.tool
```

---

## 🚨 Troubleshooting

### Gmail Auth: "GOOGLE_SERVICE_ACCOUNT_JSON is not set"
```bash
# Verify env variable is set
echo $GOOGLE_SERVICE_ACCOUNT_JSON | python3 -m json.tool | head -5
```

### Kafka: "Connection refused"
```bash
docker-compose ps kafka  # Is it healthy?
docker-compose logs kafka | tail -20
```

### ClamAV: Takes too long to start
```bash
# Normal — ClamAV loads ~300MB virus definitions at startup
# Wait 2-3 minutes, then check:
docker-compose logs clamav | grep "Listening"
```

### Redis: "WRONGPASS invalid username-password pair"
```bash
# Make sure URL includes the password
# REDIS_URL=redis://:localdevtoken@localhost:6379
redis-cli -a localdevtoken ping
```

### S3: "NoCredentialsError"
```bash
aws configure  # Set your AWS credentials
aws sts get-caller-identity  # Verify
```

---

## 📌 Next Steps

1. ✅ Run tests: `pytest tests/ -v`
2. ✅ Start infra: `docker-compose up -d`
3. ✅ Run worker: `uvicorn app.main:app --port 8001 --reload`
4. ✅ Send a test email to papers@srmap.edu.in
5. ✅ Watch Kafka topic: `kafka-console-consumer --topic ingest.raw --bootstrap-server localhost:9092 --from-beginning`
6. ✅ Move to Module 3: Paper Processing & NLP pipeline
