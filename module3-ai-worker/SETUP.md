# Module 3: AI Extraction Worker — Setup Guide

Two paths:

- **Path A — Native (venv + shared infra via Docker)**
- **Path B — Fully Dockerized**

---

## 0. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.11.x | `python3 --version` |
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |

---

## 1. Unzip

```bash
unzip module3-ai-worker.zip
cd module3-ai-worker
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

Takes 2-3 minutes (spaCy + its dependencies are the bulk of it).

## A.3 — Download the spaCy NLP model (one-time, ~15MB)

```bash
python -m spacy download en_core_web_sm
```

Verify:
```bash
python -c "import spacy; spacy.load('en_core_web_sm'); print('spaCy model OK')"
```

## A.4 — Create `.env`

```bash
cp .env.example .env
```

The defaults already point at **Module 4's shared infra** on its
host-exposed ports:

```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9093
REDIS_URL=redis://:localdevtoken@localhost:6380
DATABASE_URL=postgresql://promptflow:secret@localhost:5433/promptflow
```

These are deliberately NOT the usual defaults (9092/6379/5432) — Module 4
maps to 9093/6380/5433 on the host specifically so it doesn't clash with
Module 1's own separate Postgres/Redis (5432/6379).

For the directory API, decide real vs mock (see section 3 below) and set
`DIRECTORY_API_URL` + `M2M_CLIENT_SECRET` accordingly.

## A.5 — Create the shared network (one-time, system-wide)

```bash
docker network create promptflow_shared_net || true
```

## A.6 — Start Module 4's shared infra + its own topics

```bash
cd ../module4-storage
docker compose up -d postgres redis zookeeper kafka
docker compose ps   # wait for all healthy, ~30 sec
docker compose up kafka-init
cd ../module3-ai-worker
```

## A.7 — Ensure Module 2's topic exists

Module 3 consumes `ingest.raw`, which Module 4's `kafka-init` doesn't
create (it only creates topics Module 4 itself consumes). Module 2 owns
creating it:

```bash
cd ../module2-email-worker
docker compose up kafka-init
cd ../module3-ai-worker
```

Confirm all topics exist:
```bash
docker exec m4_kafka kafka-topics --bootstrap-server localhost:29092 --list
```
Expect: `ingest.raw`, `papers.validated`, `papers.review`, `papers.failed`,
`dlq.ingestion.failed`.

## A.8 — Set up the directory API

**Option 1 — Real (Module 1, returns actual faculty_id UUIDs):**
```bash
cd ../module1-auth
docker compose up -d
python scripts/create_service_account.py module3-ai-worker "Module 3 AI Worker"
# copy the printed secret
cd ../module3-ai-worker
```
Then in `.env`:
```env
DIRECTORY_API_URL=http://localhost:8000
AUTH_SERVICE_URL=http://localhost:8000
M2M_CLIENT_SECRET=<paste secret>
```

**Option 2 — Mock (isolated testing, no Module 1 needed, no real UUIDs):**
```bash
docker compose up -d mock-directory
```
Then in `.env`:
```env
DIRECTORY_API_URL=http://localhost:8080
```
(Leave `M2M_CLIENT_SECRET` blank — the mock doesn't check auth at all.)

## A.9 — Run the test suite

```bash
pytest tests/test_module3.py -q --asyncio-mode=auto
```

Expected: **22 passed**. Unit tests for the confidence formula, routing
engine, and each extraction tier — no live Kafka/Postgres/directory
needed.

## A.10 — Start the worker

```bash
python -m app.pipeline
```

Expected startup log:
```
Module 3 worker starting | consumer_group=module3-ai-extraction | topics=['ingest.raw']
```

## A.11 — Smoke test

```bash
curl http://localhost:8002/health
```

## A.12 — Stopping everything

```bash
# Ctrl+C in the worker terminal
docker compose down                            # stops mock-directory if used
cd ../module4-storage && docker compose down   # stops shared infra
cd ../module1-auth && docker compose down      # if you started it for A.8
```

---

# PATH B — Fully Dockerized

## B.1 — Shared network

```bash
docker network create promptflow_shared_net || true
```

## B.2 — Start Module 4's shared infra + topics

```bash
cd ../module4-storage
docker compose up -d postgres redis zookeeper kafka
docker compose up kafka-init
cd ../module2-email-worker
docker compose up kafka-init   # creates ingest.raw
cd ../module3-ai-worker
```

## B.3 — Set up the directory API (real, recommended for integration testing)

```bash
cd ../module1-auth
docker compose up -d
python scripts/create_service_account.py module3-ai-worker "Module 3 AI Worker"
cd ../module3-ai-worker
```

Copy the printed secret.

## B.4 — Configure `.env`

```bash
cp .env.example .env
```

Edit and set:
```env
M2M_CLIENT_SECRET=<paste secret from B.3>
```

`docker-compose.yml`'s `worker` service already overrides
`KAFKA_BOOTSTRAP_SERVERS`/`REDIS_URL`/`DATABASE_URL`/`DIRECTORY_API_URL`/
`AUTH_SERVICE_URL` to the internal container hostnames — only
`M2M_CLIENT_SECRET` needs to come from your `.env` (it's substituted into
`docker-compose.yml` via `${M2M_CLIENT_SECRET}`).

## B.5 — Build and start

```bash
docker compose build --no-cache
docker compose up -d worker
docker compose ps
```

## B.6 — Check logs

```bash
docker compose logs -f worker
```

## B.7 — Smoke test

```bash
docker compose exec worker curl -f http://localhost:8002/health
```

## B.8 — Run tests inside the container

```bash
docker compose exec worker pytest tests/test_module3.py -q --asyncio-mode=auto
```

## B.9 — Stopping everything

```bash
docker compose down
cd ../module2-email-worker && docker compose down
cd ../module4-storage && docker compose down
cd ../module1-auth && docker compose down
```

---

## 3. Directory API: real vs mock

| | Real (Module 1) | Mock (`mock_directory/`) |
|---|---|---|
| Returns a real `faculty_id` UUID | Yes | No — never returns one at all |
| Needs Module 1 running | Yes | No |
| Needs M2M auth | Yes | No (unauthenticated) |
| Good for | Full end-to-end integration testing against Module 4 | Exercising the extraction cascade in isolation |

If you use the mock, every paper resolves to Module 4's "unresolved
faculty" sentinel UUID (`00000000-0000-0000-0000-000000000000`) rather
than a real user — fine for checking extraction logic, not for a real
end-to-end test through Module 4.

---

## 4. Testing the pipeline manually

Inject a synthetic message straight into `ingest.raw` without waiting on
a real email from Module 2:

```bash
python3 scripts/inject_test_message.py --server localhost:9093
```

(`--server localhost:9093` matters if you're running this script from
your host, i.e. Path A — the script's own default already matches this,
but it's worth being explicit. If running it from inside a container on
`promptflow_shared_net`, use `--server kafka:29092` instead.)

Watch the worker process it:
```bash
docker exec m4_kafka kafka-console-consumer --bootstrap-server localhost:29092 \
  --topic papers.validated --from-beginning --max-messages 1
```

---

## 5. Common problems and fixes

**`Connection refused` on Kafka from a native (Path A) process**
Check `.env` — `9092` isn't mapped to the host at all. Module 4 only
exposes `9093` (its `PLAINTEXT_HOST` listener). `9093` should already be
correct in `.env.example`; if you changed it, change it back.

**Directory lookups always time out / 401**
- Using the real directory: confirm Module 1 is up
  (`docker compose ps` in `module1-auth/`) and `M2M_CLIENT_SECRET` in
  `.env` matches what `create_service_account.py` printed.
- Using the mock: confirm `docker compose ps mock-directory` shows
  healthy, and `DIRECTORY_API_URL=http://localhost:8080` in `.env`.

**Every paper's `faculty_id` is `00000000-0000-0000-0000-000000000000`**
This is the mock directory's expected behavior (it never returns a real
UUID) — not a bug. Switch to the real directory (section 3) if you need
actual faculty resolution.

**`kafka-topics`/`kafka-console-consumer` says "command not found" inside a container**
Run it against `m4_kafka` (has the Kafka CLI tools), not
`m3_ai_extraction_worker` (a plain Python image, no Kafka CLI tools).

**Worker logs show repeated retries for the same message**
By design — the consumer only commits a Kafka offset after successfully
publishing its output, so a malformed message retries indefinitely
rather than silently dropping data. Check the full traceback; this
usually means either the directory API is unreachable (see above) or a
genuinely malformed `ingest.raw` payload (compare against
`app/models/schemas.py`'s `IngestedPayload`).

**Bedrock (Tier 4) calls fail / time out**
This is expected without real AWS credentials configured — the cascade
still works using Tiers 1-3 (regex, CrossRef, spaCy), which handle the
large majority of well-formed papers. Bedrock is only invoked as a
fallback when Tier 3's confidence is below `LLM_CONFIDENCE_THRESHOLD`
(default 0.70). No AWS account is required to run a demo.

---

## 6. What's NOT covered by this guide

- Real AWS Bedrock configuration (IAM role, model access request) — see
  `terraform/` for that. This guide's demo path works fully without it.
- Paper-type classification (`extraction_result.metadata.paper_type`
  currently defaults to `"unknown"` — none of the four extraction tiers
  classify it yet; flag this if you want it added).
