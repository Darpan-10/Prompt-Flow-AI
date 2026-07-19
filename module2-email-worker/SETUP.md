# Module 2: Gmail Ingestion Worker — Setup Guide

Manual, step-by-step instructions. Two paths documented:

- **Path A — Native (venv + Module 4's shared infra via Docker)**
- **Path B — Fully Dockerized**

---

## 0. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.12.x | `python3 --version` |
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Google Service Account JSON | — | see "Gmail Setup" below |

---

## 1. Unzip

```bash
unzip module2-email-worker.zip
cd module2-email-worker
```

---

## 2. Gmail Setup (required for both paths)

Two ways to authorize this app against a Gmail inbox — pick one.

### Option 1 — Personal Gmail account (no admin access needed)

Use this if you're demoing with your own `@gmail.com` (or any account
you don't have Workspace admin control over). Standard OAuth2 consent —
you personally approve read-only access once in a browser, then it's
headless from there on.

1. **Google Cloud Console** (https://console.cloud.google.com) → create
   a project → APIs & Services → Enable APIs → enable **Gmail API**.

2. **OAuth consent screen**: APIs & Services → OAuth consent screen →
   External → fill in an app name + your email → Save. It's fine to
   leave this in "Testing" status for a demo — just add your own Gmail
   address under **Test users** (Google requires this for apps that
   haven't gone through verification).

3. **Create an OAuth client**: APIs & Services → Credentials → Create
   Credentials → OAuth client ID → Application type: **Desktop app**.
   Download the JSON.

4. Save it as `gmail_oauth_client_secret.json` in this directory
   (`module2-email-worker/`).

5. Run the one-time interactive login (opens your browser):
   ```bash
   python scripts/gmail_oauth_login.py
   ```
   Log in, approve the read-only Gmail scope. This writes
   `gmail_oauth_token.json` — a cached, auto-refreshing token. You won't
   need to do this again unless you delete that file or revoke access.

6. In `.env`, set:
   ```env
   GMAIL_AUTH_MODE=oauth_personal
   ```

That's it — no Workspace admin, no delegation. The worker reads from
whatever inbox you logged in as in step 5.

### Option 2 — Google Workspace domain (requires admin access)

Use this only if you actually have admin control over a Workspace
domain (e.g. your college's real IT-managed `@srmap.edu.in`). Headless
via Service Account + Domain-Wide Delegation — genuinely cannot work
against a personal account, there's no delegation to grant on one.

1. **Google Cloud Console** → create a project → enable **Gmail API**.

2. **Create a Service Account**: APIs & Services → Credentials → Create
   Credentials → Service Account → create a JSON key, download it.

3. **Domain-Wide Delegation**:
   - In the service account's details → Advanced settings → enable
     domain-wide delegation, note the numeric Client ID.
   - In Google Workspace Admin Console → Security → API Controls →
     Domain-wide Delegation → add that Client ID with scope
     `https://www.googleapis.com/auth/gmail.readonly`.

4. Flatten the JSON to one line (required — it's a single env var):
   ```bash
   python3 -c "import json; print(json.dumps(json.load(open('/path/to/key.json'))))"
   ```

5. In `.env`, set:
   ```env
   GMAIL_AUTH_MODE=service_account
   GOOGLE_SERVICE_ACCOUNT_JSON=<paste the single-line JSON from step 4>
   GMAIL_DELEGATED_USER=papers@srmap.edu.in
   ```

---

# PATH A — Native Python

## A.1 — Virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

## A.2 — Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## A.3 — Create `.env`

```bash
cp .env.example .env
```

Fill in your Gmail credentials from whichever option you used in step 2,
and double-check the infra ports — these point at **Module 4's shared
services**, not local ones:

```env
# From section 2, Option 1 (personal) or Option 2 (Workspace admin):
GMAIL_AUTH_MODE=oauth_personal
# ...plus whichever GOOGLE_*/GMAIL_* vars that option requires

# Module 4's shared Kafka/Redis/Postgres — note these ports are NOT the
# defaults (9092/6379/5432). Module 4 deliberately maps to 9093/6380/5433
# on the host so it doesn't clash with Module 1's own separate
# Postgres/Redis (which use 5434/6379 — see module1-auth/SETUP.md for
# why 5434 specifically).
KAFKA_BOOTSTRAP_SERVERS=localhost:9093
REDIS_URL=redis://:localdevtoken@localhost:6380
DATABASE_URL=postgresql://promptflow:secret@localhost:5433/promptflow
```

## A.4 — Set up Gmail auth (if using `oauth_personal`)

```bash
python scripts/gmail_oauth_login.py
```

Opens your browser for a one-time consent. Writes `gmail_oauth_token.json`
in this directory — the worker reads and auto-refreshes it from here on,
no browser needed again. (Skip this if you're using `service_account`
mode instead — nothing to do here in that case.)

## A.5 — Create the shared network (one-time, system-wide)

```bash
docker network create promptflow_shared_net || true
```

## A.6 — Start Module 4's shared infra

```bash
cd ../module4-storage
docker compose up -d postgres redis zookeeper kafka
docker compose ps   # wait for all to show "healthy", ~30 seconds

docker compose up kafka-init   # creates Module 4's own topics
cd ../module2-email-worker
```

## A.7 — Create Module 2's own Kafka topic

Module 4's `kafka-init` only creates the topics Module 4 itself consumes
(`papers.*`, `dlq.ingestion.failed`) — it has no reason to know about
`ingest.raw`, which is Module 2's own output topic. Module 2 is
responsible for making sure that one exists:

```bash
docker compose up kafka-init
```

Confirm:
```bash
docker exec m4_kafka kafka-topics --bootstrap-server localhost:29092 --list
```
Should include `ingest.raw` and `dlq.ingestion.failed` (plus Module 4's
own topics).

## A.8 — Start ClamAV

```bash
docker compose up -d clamav
docker compose ps
```

Wait for it to load virus signatures (~1-2 minutes on first run):
```bash
docker compose logs -f clamav
```
Look for a line mentioning the database is loaded, then Ctrl+C.

## A.9 — Run the test suite

```bash
pytest tests/test_validation.py -q --asyncio-mode=auto
```

Expected: **25 passed**. These are unit tests (domain validation, PII
redaction regex, idempotency key generation) — no live Kafka/Postgres
needed.

## A.10 — Start the worker

```bash
python -m app.main
```

This starts both the FastAPI health-check server AND the background
Gmail-polling worker thread (see `app/main.py`'s startup event).

## A.11 — Smoke test

```bash
curl http://localhost:8001/health
```

## A.12 — Stopping everything

```bash
# Ctrl+C in the worker terminal
docker compose down                      # stops ClamAV + kafka-init
cd ../module4-storage && docker compose down   # stops shared infra (keeps data)
```

---

# PATH B — Fully Dockerized

## B.1 — Shared network

```bash
docker network create promptflow_shared_net || true
```

## B.2 — Start Module 4's shared infra

```bash
cd ../module4-storage
docker compose up -d postgres redis zookeeper kafka
docker compose up kafka-init
cd ../module2-email-worker
```

## B.3 — Configure `.env`

```bash
cp .env.example .env
# Fill in GMAIL_AUTH_MODE and whichever credentials it needs (section 2 above)
```

`docker-compose.yml`'s `worker` service overrides `KAFKA_BOOTSTRAP_SERVERS`,
`REDIS_URL`, and `DATABASE_URL` to the internal container hostnames
(`kafka:29092`, `redis:6379`, `postgres:5432`) automatically — the `.env`
values above are only used if you run Path A (native, outside Docker).

## B.4 — Set up the Gmail OAuth files on the host (if using `oauth_personal`)

`docker-compose.yml` bind-mounts `gmail_oauth_client_secret.json` and
`gmail_oauth_token.json` into the container. **Both must already exist
on the host before you run `docker compose up`** — if a bind-mounted
file doesn't exist yet, Docker silently creates an empty *directory*
with that name instead of erroring, which then breaks everything and
isn't obvious from the logs. The interactive login itself needs a real
browser, which the container doesn't have, so this has to happen on the
host either way:

```bash
python3 -m venv /tmp/gmail-oauth-venv && source /tmp/gmail-oauth-venv/bin/activate
pip install --quiet -r requirements.txt
python scripts/gmail_oauth_login.py    # opens your browser, one-time consent
deactivate && rm -rf /tmp/gmail-oauth-venv
```

This leaves `gmail_oauth_client_secret.json` and `gmail_oauth_token.json`
in this directory, ready for the bind mount.

**If you're using `service_account` mode instead**, these two files are
still bind-mounted but never read — create empty placeholders so Docker
doesn't turn them into directories:
```bash
touch gmail_oauth_client_secret.json gmail_oauth_token.json
```

## B.5 — Build and start

```bash
docker compose build --no-cache
docker compose up -d kafka-init clamav worker
docker compose ps
```

Wait for `clamav` to show healthy (~1-2 min) and `worker` to show healthy.

## B.6 — Check logs

```bash
docker compose logs -f worker
```

## B.7 — Smoke test

```bash
docker compose exec worker curl -f http://localhost:8001/health
```

## B.8 — Run tests inside the container

```bash
docker compose exec worker pytest tests/test_validation.py -q --asyncio-mode=auto
```

## B.9 — Stopping everything

```bash
docker compose down
cd ../module4-storage && docker compose down
```

---

## 3. Testing the pipeline manually (either path)

Module 2 normally consumes real Gmail messages, but you can inject a
synthetic one straight into Kafka to test downstream processing (Module 3)
without waiting on an actual email:

```bash
cat > /tmp/test_ingest.json << 'PAYLOAD'
{"event_id":"test-1","contract_version":"v1","pipeline_status":"ingested","created_at":"2026-07-17T10:00:00Z","email":{"message_id":"<1@test>","subject":"Test Paper","sender":"faculty@srmap.edu.in","recipients":["papers@srmap.edu.in"],"received_at":"2026-07-17T10:00:00Z","idempotency_key":"test-idem-0001"},"content":{"raw_text":"Title: Deep Learning Survey. Authors: Jane Doe. DOI: 10.1234/test.2026. Year: 2026.","raw_text_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","attachments":[]},"security":{"pii_redacted":true,"source_domain_verified":true,"clamav_scanned":true,"clamav_result":"CLEAN"}}
PAYLOAD

docker exec -i m4_kafka kafka-console-producer \
  --broker-list localhost:29092 --topic ingest.raw < /tmp/test_ingest.json
```

(`-i` on `docker exec` is required to pipe stdin into the container —
without it, redirecting a file into the command silently does nothing.)

Verify it landed:
```bash
docker exec m4_kafka kafka-console-consumer --bootstrap-server localhost:29092 \
  --topic ingest.raw --from-beginning --max-messages 1
```

---

## 4. Common problems and fixes

**`Connection refused` on Kafka from a native (Path A) process**
You're probably using port `9092`. Module 4's Kafka only exposes
`9093` to the host (`9092` isn't mapped at all — see
`module4-storage/docker-compose.yml`'s `KAFKA_ADVERTISED_LISTENERS`).
`9092` is a red herring left over from Kafka's usual default; check `.env`.

**Redis `NOAUTH Authentication required`**
Module 4's Redis requires a password (`localdevtoken`). Make sure
`REDIS_URL` includes it: `redis://:localdevtoken@localhost:6380`.

**`docker compose up` fails: "network promptflow_shared_net not found"**
Run `docker network create promptflow_shared_net` once (step A.4/B.1) —
or start Module 4 first, which creates it automatically.

**ClamAV healthcheck never turns healthy**
It takes 1-2 minutes to download/load virus signatures on first start.
Check progress: `docker compose logs -f clamav`. If it's been more than
5 minutes, check disk space — the signature database is a few hundred MB.

**Worker logs show `psycopg2.OperationalError: could not connect`**
Audit logging (`app/services/audit.py`) needs Module 4's Postgres up.
This failure is non-fatal by design — ingestion still proceeds, and the
error is logged as `AUDIT_WRITE_FAILED` rather than crashing the worker —
but confirm `docker compose ps postgres` in `module4-storage/` shows healthy.

**`kafka-topics` command inside a container says "command not found"**
Run it against `m4_kafka` (has the Confluent Kafka CLI tools baked in),
not `m2_email_worker` (a plain Python image — no Kafka CLI tools there).

---

## 5. What's NOT covered by this guide

- Real AWS SES/S3 for attachment storage in production — this guide uses
  local dev bucket names (`promptflow-ingestion-dev`) that only work if
  you've configured real AWS credentials; without them, attachment
  upload will fail (email text extraction and Kafka publish still work).
- ElastiCache auth token rotation (Terraform-managed) — local dev uses a
  fixed password (`localdevtoken`) baked into `docker-compose.yml`.
