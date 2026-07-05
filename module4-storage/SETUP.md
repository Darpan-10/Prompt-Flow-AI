# Module 4: Storage & Indexing Layer — Setup Guide

Manual, step-by-step instructions. No scripts run anything for you — every
command below is meant to be typed (or copy-pasted one block at a time) so
you can see exactly what's happening and stop/adjust at any point.

Two paths are documented:

- **Path A — Native (venv + local PostgreSQL/Redis/Kafka via Docker only for infra)**
- **Path B — Fully Dockerized (app itself also runs in containers)**

Pick whichever matches how you want to work. You don't need to do both.

---

## 0. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.11.x | `python3.11 --version` |
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 (the `docker compose` subcommand, not `docker-compose`) | `docker compose version` |
| psql (optional, for manual DB inspection) | any recent | `psql --version` |

If `python3.11` isn't available on your system, anything 3.11.x works — just
adjust the venv creation command below to whatever binary you have
(`python3 --version` first to confirm it's 3.11+).

---

## 1. Unzip the project

```bash
unzip module4-storage.zip
cd module4-storage
```

Confirm the structure looks like this:

```bash
find . -maxdepth 2 -type f | sort
```

Expected output:

```
./.dockerignore
./.env.example
./Dockerfile
./alembic.ini
./app/config.py
./app/consumer.py
./app/database.py
./app/main.py
./docker-compose.yml
./migrations/env.py
./requirements.txt
./terraform/main.tf
./terraform/variables.tf
./terraform/dev.tfvars
./tests/test_module4.py
```

(plus subdirectories under `app/models`, `app/repository`, `app/services`,
`migrations/versions`, `terraform/modules`, `tests/fixtures`)

---

# PATH A — Native Python (recommended for active development)

Infra (Postgres, Redis, Kafka) runs in Docker; the Python app itself runs
directly on your machine in a venv, so you get fast reload, easy debugging,
and direct access to breakpoints/print statements.

## A.1 — Create and activate a virtual environment

```bash
python3.11 -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows (cmd)
# venv\Scripts\Activate.ps1     # Windows (PowerShell)
```

Confirm you're in the venv:

```bash
which python
# should print .../module4-storage/venv/bin/python
```

## A.2 — Install PyTorch (CPU-only) BEFORE the rest of requirements.txt

This matters. `sentence-transformers` depends on `torch`. If you `pip
install -r requirements.txt` directly, pip will pull the **default PyPI
torch wheel**, which bundles CUDA libraries and is 2-4GB. Installing the
CPU-only wheel first means pip sees torch is already satisfied later and
skips the CUDA download entirely.

```bash
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

This should download roughly 150-250MB depending on platform, not gigabytes.
If it tries to download something huge, stop and check you didn't typo the
`--index-url`.

## A.3 — Install the rest of the dependencies

```bash
pip install -r requirements.txt
```

This installs: FastAPI, SQLAlchemy 2.0 (async), asyncpg, Alembic, pgvector
client bindings, sentence-transformers, confluent-kafka, redis, boto3,
pytest, and a handful of utilities. Should take 1-3 minutes.

## A.4 — Pre-download the embedding model (one-time, ~420MB)

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"
```

This downloads `all-mpnet-base-v2` from HuggingFace Hub and caches it under
`~/.cache/huggingface` (or `~/.cache/torch/sentence_transformers`,
depending on library version). You only need to do this once — subsequent
runs reuse the cache. If you're on a slow connection, this step is the
longest one in the whole setup (model is ~420MB).

Verify it worked:

```bash
python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('all-mpnet-base-v2')
v = m.encode('Attention Is All You Need').tolist()
print('Embedding dimension:', len(v))
print('First 5 values:', v[:5])
"
```

Expected: `Embedding dimension: 768` followed by 5 floating point numbers.

## A.5 — Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and review it. The defaults already match the docker-compose
infra ports defined in `docker-compose.yml` (Postgres on `5433`, Redis on
`6380`, Kafka on `9093`), so for local development **you likely don't need
to change anything** unless you're pointing at AWS RDS/ElastiCache/MSK
instead. Key variables to know about:

```bash
DATABASE_URL=postgresql+asyncpg://promptflow:secret@localhost:5433/promptflow
REDIS_URL=redis://:localdevtoken@localhost:6380
KAFKA_BOOTSTRAP_SERVERS=localhost:9093
KAFKA_CONSUMER_GROUP=module4-storage-worker
```

## A.6 — Start infrastructure only (Postgres + Redis + Kafka via Docker)

This is the one place Docker is still involved in Path A — just for the
stateful infrastructure, not your app code.

```bash
docker compose up -d postgres redis zookeeper kafka
```

Watch the logs until everything is healthy:

```bash
docker compose ps
```

You want to see `postgres`, `redis`, `zookeeper`, `kafka` all show `Up` (and
`healthy` once their healthchecks pass — give it 20-30 seconds). If `kafka`
shows `Restarting`, it usually means `zookeeper` wasn't ready yet; just wait
and check again.

```bash
docker compose ps
```

## A.7 — Create the Kafka topics

```bash
docker compose up kafka-init
```

This runs a one-shot container that creates `papers.validated`,
`papers.review`, `papers.failed`, and `dlq.ingestion.failed` (3 partitions
each). Confirm:

```bash
docker exec m4_kafka kafka-topics --bootstrap-server localhost:29092 --list
```

You should see all four topics listed (plus the internal
`__consumer_offsets`).

## A.8 — Run the database migration

```bash
alembic upgrade head
```

This creates all four tables (`papers`, `paper_versions`,
`validation_issues`, `audit_log`), their year/month partitions, every
index (B-tree, GIN full-text, HNSW vector, BRIN time-series), Row-Level
Security policies, and the versioning/audit triggers.

If this fails with a connection error, double check Postgres is actually
up (`docker compose ps postgres`) and that `DATABASE_URL` in `.env` matches
the port Postgres is exposed on (`5433` by default in `docker-compose.yml`).

Verify the schema landed correctly:

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c "\dt"
```

Expected tables: `papers`, `paper_versions`, `validation_issues`,
`audit_log`, plus their partition children (`papers_y2024`, `papers_y2025`,
etc.) and `alembic_version`.

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN ('papers','paper_versions','validation_issues');"
```

`rowsecurity` should be `t` (true) for all three.

## A.9 — Run the test suite

```bash
pytest tests/test_module4.py tests/test_rls_context_fail_closed.py -v
```

Expected: **52 passed**. These are all unit tests against Pydantic schemas,
the consumer's message-building logic, routing rules, and (per
`CRITICAL_PATCH_NOTES.md`'s second patch round) the auth-header
fail-closed behavior — they mock the embedding model and database
session and don't require Postgres/Kafka/Redis to be running. If you
see import errors here, go back to step A.3 and confirm
`pip install -r requirements.txt` completed without errors.

### Running the real-PostgreSQL RLS regression tests

`tests/integration_real_db/` connects directly to a real PostgreSQL
instance and proves the RLS mechanism itself is sound — not just that
the Python code calling it doesn't raise, but that the actual database
enforces department isolation, admin bypass, and (per
`CRITICAL_PATCH_NOTES.md`) that `FORCE ROW LEVEL SECURITY` is actually
doing something.

**This setup matters and is easy to get subtly wrong:** the test
database's schema must be created by, and therefore *owned by*, the
SAME role the test connects as — matching production exactly, where
`docker-compose.yml`'s `POSTGRES_USER` / Terraform's RDS
`master_username` (`promptflow`) is the same role that both runs Alembic
(owning every table) and the running application connects as. A test
setup where a `postgres` superuser creates the schema and a different,
merely-GRANTed role queries it gives false confidence — PostgreSQL does
not apply RLS to a table's owner by default, so such a test would pass
even with `FORCE ROW LEVEL SECURITY` missing entirely.

Also important: if you use your system's default `postgres` role for
this, it is very likely a TRUE PostgreSQL superuser (not just an RDS-style
`rds_superuser` role membership), and true superusers bypass RLS
*unconditionally* — `FORCE ROW LEVEL SECURITY` cannot override that. Use
a dedicated `NOSUPERUSER` role for this test to be meaningful.

```bash
sudo -u postgres psql -c "CREATE ROLE m4_test_owner LOGIN PASSWORD 'testpass' NOSUPERUSER CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE module4_rls_test OWNER m4_test_owner;"
sudo -u postgres psql -d module4_rls_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'

TEST_DATABASE_URL="postgresql+asyncpg://m4_test_owner:testpass@localhost/module4_rls_test" \
  pytest tests/integration_real_db/ -v
```

Expected: **11 passed** (4 in `test_set_rls_context_real_db.py`, 7 in
`test_papers_rls_policy_real_db.py`).

## A.10 — Start the Kafka consumer

In one terminal (keep this venv activated):

```bash
python -m app.consumer
```

First-run output should look like:

```
INFO | Warming up embedding model (sentence-transformers/all-mpnet-base-v2)...
INFO | Embedding model ready.
INFO | Module 4 Kafka consumer started. Topics: ['papers.validated', 'papers.review', 'papers.failed'] | Group: module4-storage-worker
```

Leave this running. It will sit idle until a message arrives on one of the
three topics.

## A.11 — Start the FastAPI service

In a **second terminal**, also with the venv activated:

```bash
source venv/bin/activate   # if this is a fresh terminal
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload
```

Expected output:

```
INFO:     Uvicorn running on http://0.0.0.0:8003 (Press CTRL+C to quit)
```

## A.12 — Smoke test the API

In a **third terminal**:

```bash
curl http://localhost:8003/health
```

Expected:

```json
{"status":"ok","service":"module4-storage","database":true,"kafka":true,"redis":true}
```

If `database` or `redis` show `false`, double-check `docker compose ps` and
your `.env` connection strings.

```bash
curl http://localhost:8003/ready
```

Expected: `{"status":"ready"}`

Browse the interactive API docs in your browser:

```
http://localhost:8003/docs
```

You should see all 19 endpoints (papers CRUD, versions, validation issues,
full-text search, semantic search, export, audit log).

## A.13 — Feed it a real paper from Module 3

If your Module 3 worker is also running locally and pointed at the same
Kafka broker (`localhost:9093`), just run its test injector:

```bash
# from your Module 3 directory
python3 scripts/inject_test_message.py
```

Watch Terminal 1 (the Module 4 consumer) — within a few seconds you should
see:

```
INFO | Created paper <uuid> | status=PUBLISHED | dept=CSE | topic=papers.validated
```

If the status is `PUBLISHED`, the embedding model just ran. Confirm it
landed correctly in the database:

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT title, status, department_code, (embedding IS NOT NULL) AS has_embedding FROM papers ORDER BY created_at DESC LIMIT 5;"
```

## A.14 — Test full-text search

CRITICAL FIX applied to this module (see CRITICAL_PATCH_NOTES.md): all
three of X-Department-Code, X-Role, and X-User-Id are now REQUIRED on
every request -- omitting any of them returns 401, rather than the old
behavior of silently defaulting to full admin access. Always pass all
three:

```bash
curl -G 'http://localhost:8003/api/v1/search/fulltext' \
  -H 'X-Department-Code: CSE' \
  -H 'X-Role: admin' \
  -H 'X-User-Id: local-dev-tester' \
  --data-urlencode 'q=attention'
```

(`X-Role: admin` bypasses Row-Level Security for local testing — in
production these headers are injected by an upstream authenticated
gateway/Module 1 after validating a real JWT.)

If you'd rather not type all three every time during local dev, you can
set `ALLOW_MISSING_AUTH_HEADERS=true` in your `.env` -- but this MUST be
`false` (the default) in any deployed environment; it exists purely as a
local convenience escape hatch and is logged loudly every time it's used.

## A.15 — Test semantic search (requires at least one PUBLISHED paper with an embedding)

```bash
python3 -c "
from sentence_transformers import SentenceTransformer
import json
m = SentenceTransformer('all-mpnet-base-v2')
v = m.encode('transformer neural network architecture').tolist()
print(json.dumps(v))
" > /tmp/test_embedding.json

curl -X POST http://localhost:8003/api/v1/search/semantic \
  -H 'Content-Type: application/json' \
  -H 'X-Department-Code: CSE' \
  -H 'X-Role: admin' \
  -H 'X-User-Id: local-dev-tester' \
  -d "{\"embedding\": $(cat /tmp/test_embedding.json), \"limit\": 5, \"similarity_threshold\": 0.5}"
```

## A.16 — Stopping everything

```bash
# Ctrl+C in the consumer terminal
# Ctrl+C in the uvicorn terminal
docker compose down          # stops Postgres/Redis/Kafka but keeps volumes
# docker compose down -v     # stops AND deletes all data (fresh start next time)
```

---

# PATH B — Fully Dockerized

Use this if you'd rather not manage a local Python environment at all, or
you're testing the actual production-shaped container before pushing it to
ECR.

## B.1 — Create your `.env` file

```bash
cp .env.example .env
```

No changes needed for the default docker-compose setup — the `consumer`
and `api` services in `docker-compose.yml` override `DATABASE_URL`,
`REDIS_URL`, and `KAFKA_BOOTSTRAP_SERVERS` to use Docker's internal network
hostnames (`postgres`, `redis`, `kafka`) automatically, regardless of what's
in `.env`.

## B.2 — Build the image

```bash
docker compose build
```

This builds the same `Dockerfile` used for both the `consumer` and `api`
services (they share one image, just run with different `command`
overrides). Expect this to take **several minutes** the first time —
it installs the CPU-only PyTorch wheel, the rest of `requirements.txt`, and
pre-downloads the embedding model into the image layer itself (~420MB),
so the resulting image is roughly 1.3-1.6GB.

Watch for this line partway through the build, confirming the model
downloaded successfully inside the container:

```
=> RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"
```

## B.3 — Start everything

```bash
docker compose up -d
```

This starts, in dependency order: `zookeeper` → `kafka` → `postgres` →
`redis` → `kafka-init` (one-shot topic creation) → `consumer` → `api`.

```bash
docker compose ps
```

All services should show `Up` (healthchecks `healthy` after ~30-60
seconds for Postgres/Kafka, the `api` container's own healthcheck hits
`/health` every 30s).

## B.4 — Run the migration inside the container

The Docker image includes Alembic and the migration files, but migrations
aren't run automatically on container start (intentionally — you don't want
a crashed/restarting container silently re-running migrations). Run it
manually, once:

```bash
docker compose exec api alembic upgrade head
```

Or, if the `api` container isn't up yet for some reason, run it as a
one-off:

```bash
docker compose run --rm api alembic upgrade head
```

## B.5 — Check logs

```bash
docker compose logs -f consumer
```

```
INFO | Warming up embedding model (sentence-transformers/all-mpnet-base-v2)...
INFO | Embedding model ready.
INFO | Module 4 Kafka consumer started. ...
```

```bash
docker compose logs -f api
```

```
INFO:     Uvicorn running on http://0.0.0.0:8003
```

## B.6 — Smoke test

Same as Path A, from your host machine (the `api` service publishes
`8003:8003`):

```bash
curl http://localhost:8003/health
```

## B.7 — Running tests inside the container

```bash
docker compose run --rm api pytest tests/test_module4.py -v
```

## B.8 — Stopping everything

```bash
docker compose down       # keep data volumes
docker compose down -v    # wipe everything, fresh start next time
```

---

## 3. Verifying compliance features (either path)

These checks work the same whether you went Path A or Path B, since they
just query Postgres directly:

**Partitions exist:**

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT tablename FROM pg_tables WHERE tablename LIKE 'papers_y%' OR tablename LIKE 'audit_log_y%' ORDER BY tablename;"
```

**Row-Level Security is enabled:**

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN ('papers','paper_versions','validation_issues');"
```

**Audit log is immutable (no UPDATE/DELETE grants to PUBLIC):**

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name='audit_log';"
```

**Versioning trigger fires on UPDATE:**

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT trigger_name, event_manipulation FROM information_schema.triggers WHERE event_object_table='papers';"
```

You should see `trg_paper_initial_version` (INSERT),
`trg_paper_versioning` (UPDATE), `trg_paper_audit` (INSERT/UPDATE/DELETE),
and `trg_papers_updated_at` (UPDATE).

---

## 4. Common problems and fixes

**`alembic upgrade head` hangs or times out**
Postgres isn't actually ready yet. Run `docker compose ps postgres` — wait
for `healthy`, not just `Up`.

**`ModuleNotFoundError: No module named 'app'`**
You're not running commands from the `module4-storage/` root directory, or
your venv isn't activated. Check `pwd` and `which python`.

**Embedding model download fails / times out**
Usually a network/firewall issue reaching `huggingface.co`. Retry the
command from step A.4 — HuggingFace downloads resume rather than restart
from zero on most setups.

**`pip install torch` pulls a multi-GB download despite using `--index-url`**
Double-check you didn't already have a cached/installed torch from a
previous `pip install -r requirements.txt` run that grabbed the CUDA
build first. `pip uninstall torch -y` then redo step A.2 before step A.3.

**Consumer logs show "Failed to process message" repeatedly for the same offset**
The consumer only commits the Kafka offset on success, so a malformed or
unexpected-schema message will retry indefinitely (by design — better
than silently dropping data). Check the full traceback in the log; this
usually means the upstream Module 3 payload doesn't match
`app/models/schemas.py`'s `KafkaPayload`. Compare the raw message
(`docker exec m4_kafka kafka-console-consumer --bootstrap-server
localhost:29092 --topic papers.validated --max-messages 1 --from-beginning`)
against `tests/fixtures/real_sample_validated.json`.

**`curl http://localhost:8003/health` shows `"database": false`**
Check `.env`'s `DATABASE_URL` port matches what's exposed in
`docker-compose.yml` (`5433` on the host, `5432` inside the Docker
network — Path A uses the host port, Path B's containers use the internal
port automatically).

---

## 5. What's NOT covered by this guide

- AWS deployment (RDS, ElastiCache, ECS Fargate) — see `terraform/` and the
  separate deployment notes for that; this guide is local-only.
- Populating embeddings retroactively for papers that were inserted before
  the embedding service existed, or for papers that get promoted from
  `PENDING_REVIEW` to `PUBLISHED` after human review (the embedding is only
  generated at ingestion time in the current consumer logic — promoting a
  paper later via `PATCH /api/v1/papers/{id}` does not currently trigger
  embedding generation; flag this to me if you want that added).
