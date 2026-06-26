# Module 5: Search & Discovery — Setup Guide

Manual, step-by-step instructions. No scripts run anything for you. Two
paths are documented:

- **Path A — Native (venv, Docker only for shared infra)**
- **Path B — Fully Dockerized**

Module 5 is **read-only**: it never writes to PostgreSQL, and it shares
the same Postgres + Redis instance as Module 4 (per the locked
architecture). This means **Module 4 must already be set up and running**
before Module 5 can do anything useful — Module 5 has no database of its
own to migrate or seed.

If you haven't already, set up Module 4 first using its own `SETUP.md`.
This guide assumes Module 4's Postgres has papers in it (ideally at least
one with `status='PUBLISHED'` and a populated `embedding` column, so you
have something real to search for).

---

## 0. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.11.x | `python3.11 --version` |
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| Module 4 | already running | `curl http://localhost:8003/health` |

---

## 1. Unzip the project

```bash
unzip module5-search.zip
cd module5-search
```

Confirm the structure:

```bash
find . -maxdepth 2 -type f | sort
```

Expected:

```
./.dockerignore
./.env.example
./Dockerfile
./app/auth.py
./app/config.py
./app/database.py
./app/main.py
./app/schemas.py
./docker-compose.yml
./pytest.ini
./requirements.txt
./scripts/make_test_token.py
./tests/performance/locustfile.py
```

(plus subdirectories under `app/repositories`, `app/routes`,
`app/services`, `app/utils`, `tests/unit`, `tests/integration`)

---

# PATH A — Native Python (recommended for active development)

## A.1 — Create and activate a virtual environment

```bash
python3.11 -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows (cmd)
# venv\Scripts\Activate.ps1     # Windows (PowerShell)
```

Confirm:

```bash
which python
# .../module5-search/venv/bin/python
```

## A.2 — Install PyTorch (CPU-only) BEFORE the rest of requirements.txt

Same reasoning as Module 4: `sentence-transformers` depends on `torch`,
and the default PyPI wheel bundles CUDA libraries (2-4GB), which is wasted
space on a machine with no GPU. Installing the CPU-only wheel first means
pip sees it's already satisfied later and skips the CUDA download.

```bash
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

Should download roughly 150-250MB. If it's pulling gigabytes, you typo'd
the `--index-url`.

## A.3 — Install the rest of the dependencies

```bash
pip install -r requirements.txt
```

This installs FastAPI, SQLAlchemy 2.0 (async), asyncpg, sentence-transformers,
redis, PyJWT, locust (for performance testing), pytest, and utilities.

## A.4 — Pre-download the embedding model (one-time, ~420MB)

Module 5 uses the **exact same model** as Module 4
(`all-mpnet-base-v2`) so that query embeddings and the document embeddings
already stored in PostgreSQL are comparable via cosine similarity. If
you already ran Module 4's setup on this same machine, the model is
likely already cached and this step will be instant.

```bash
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"
```

Verify:

```bash
python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('all-mpnet-base-v2')
v = m.encode('neural network architecture').tolist()
print('Embedding dimension:', len(v))
"
```

Expected: `Embedding dimension: 768`

## A.5 — Create your `.env` file

```bash
cp .env.example .env
```

The defaults already point at Module 4's local Docker ports (`5433` for
Postgres, `6380` for Redis). **For local development, you likely don't
need to change anything**, provided Module 4's Docker stack is already
running on those same ports.

Key variables to know about:

```bash
DATABASE_URL=postgresql+asyncpg://promptflow:secret@localhost:5433/promptflow
REDIS_URL=redis://:localdevtoken@localhost:6380
REDIS_PUBSUB_CHANNEL=search_invalidate
SKIP_JWT_VALIDATION=true   # local dev only -- see step A.7
```

## A.6 — Confirm Module 4's infrastructure is running

Module 5 doesn't start its own Postgres/Redis/Kafka — it connects to
Module 4's.

```bash
cd ../module4-storage   # or wherever you unzipped Module 4
docker compose up -d postgres redis
docker compose ps
```

You want `postgres` and `redis` both `Up`/`healthy`. Then go back to the
Module 5 directory:

```bash
cd ../module5-search
```

Confirm connectivity from Module 5's intended config:

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c "SELECT COUNT(*) FROM papers WHERE status='PUBLISHED';"
```

If this returns `0`, search will technically work but return empty
results for everything — feed Module 4 a real paper first via its own
Kafka consumer + `inject_test_message.py`, or insert a manual test row.

## A.7 — Generate a local test JWT (no real Auth service needed)

Module 5 trusts JWT claims issued by Module 1, but for local testing you
don't need Module 1 running at all. A small script generates an unsigned
token with the right claims, and `SKIP_JWT_VALIDATION=true` (already set
in your `.env` from step A.5) tells Module 5 to accept it without
verifying a cryptographic signature.

```bash
python scripts/make_test_token.py --role admin --dept CSE
```

This prints the claims, the raw token, and a ready-to-use `curl` example.
**Copy the token** — you'll need it for every request below. For
convenience, store it in a shell variable:

```bash
export TEST_JWT_TOKEN=$(python scripts/make_test_token.py --role admin --dept CSE | tail -1)
echo $TEST_JWT_TOKEN
```

Other useful variations:

```bash
# Faculty user scoped to a specific department (RLS will restrict results)
python scripts/make_test_token.py --role faculty --dept ECE

# Coordinator role
python scripts/make_test_token.py --role coordinator --dept CSE
```

⚠️ This bypass is for local development only. `SKIP_JWT_VALIDATION` must
be `false` in any deployed environment — see the AWS section near the end
of this guide.

## A.8 — Run the test suite

```bash
pytest tests/unit/ tests/integration/ -v
```

Expected: **54 passed**. These tests mock the database session, Redis
client, and embedding model entirely — they validate the RRF fusion math,
query sanitization, cursor encode/decode, cache key generation, and JWT
claim extraction logic without needing Postgres/Redis/the real model
running. If you see import errors, revisit step A.3.

## A.9 — Start the search API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload
```

Expected startup log:

```
INFO | Module 5 Search service starting...
INFO | Warming up embedding model (sentence-transformers/all-mpnet-base-v2)...
INFO | Embedding model ready.
INFO | Started Redis pub/sub invalidation listener (channel=search_invalidate)
INFO:     Uvicorn running on http://0.0.0.0:8005
```

If you see a Redis connection error instead, double-check Module 4's
Redis container is actually running (step A.6).

## A.10 — Smoke test

In a second terminal:

```bash
curl http://localhost:8005/health
```

Expected:

```json
{"status":"ok","service":"module5-search","database":true,"redis":true,"embedding_model_loaded":true}
```

```bash
curl http://localhost:8005/ready
```

Browse the interactive docs:

```
http://localhost:8005/docs
```

## A.11 — Run a real search

Using the token from step A.7:

```bash
curl -X POST http://localhost:8005/api/v1/search \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "attention mechanism", "mode": "hybrid", "limit": 10}'
```

Try each mode individually:

```bash
# Keyword (full-text, ts_headline highlights)
curl -X POST http://localhost:8005/api/v1/search \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "attention mechanism", "mode": "keyword", "limit": 10}'

# Semantic (pgvector cosine similarity, no highlights)
curl -X POST http://localhost:8005/api/v1/search \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "transformer neural network", "mode": "semantic", "limit": 10}'
```

If you get an empty `results: []` array but `total_count: 0` as well
(not an error), that's expected if Module 4's database has no matching
papers yet — it's working correctly, there's just no data to find.

## A.12 — Test facets (heavily cached, 1-hour TTL)

```bash
curl http://localhost:8005/api/v1/search/facets \
  -H "Authorization: Bearer $TEST_JWT_TOKEN"
```

First call hits the database; run it again immediately and check the
response is identical and instant (served from Redis cache).

## A.13 — Test autocomplete suggestions

```bash
curl "http://localhost:8005/api/v1/search/suggestions?prefix=attention&type=title" \
  -H "Authorization: Bearer $TEST_JWT_TOKEN"
```

## A.14 — Test cache invalidation (Redis Pub/Sub)

This is the mechanism that keeps search results fresh after Module 4
writes a new/updated paper. You can trigger it manually without Module 4
to confirm Module 5's listener is working:

```bash
docker exec m4_redis redis-cli -a localdevtoken PUBLISH search_invalidate "manual_test"
```

Watch Module 5's `uvicorn` terminal (from step A.9) — within a moment you
should see:

```
INFO | Cache invalidation triggered by pub/sub message: 'manual_test'
INFO | Invalidated N cache keys in response to: 'manual_test'
```

If you previously ran the facets request (step A.12) and it was cached,
running it again right after this invalidation should hit the database
again (not the stale cache).

**In production, Module 4 calls this exact same `PUBLISH` command** after
every successful paper insert/update — see the note at the bottom of this
guide if Module 4's consumer doesn't already do this.

## A.15 — Run the Locust performance test (local)

Verifies the <600ms p95 latency target.

```bash
# Interactive mode (opens a web UI at http://localhost:8089)
locust -f tests/performance/locustfile.py --host http://localhost:8005
```

Open `http://localhost:8089`, set number of users (try 20-50 for a local
machine) and spawn rate, click "Start swarming". Watch the live charts —
particularly the "Response times" and the 95th percentile column in the
statistics table.

Or run headless for a fixed duration (useful for CI or quick checks):

```bash
locust -f tests/performance/locustfile.py --host http://localhost:8005 \
  --users 50 --spawn-rate 5 --run-time 2m --headless \
  --html tests/performance/report.html
```

Open `tests/performance/report.html` in a browser afterward for the full
report including p50/p95/p99 latency charts per endpoint.

**Realistic expectation for local dev**: your laptop is not an ECS Fargate
task with dedicated 1 vCPU/2GB, and Module 4's Postgres is also running
locally competing for the same CPU cores. Don't be alarmed if local p95
numbers are higher than the 600ms production target — the meaningful run
is against a real AWS deployment (see the comment block at the bottom of
`locustfile.py` for pointing Locust at an ALB endpoint instead).

## A.16 — Stopping everything

```bash
# Ctrl+C in the uvicorn terminal
cd ../module4-storage && docker compose down   # stops shared Postgres/Redis
```

---

# PATH B — Fully Dockerized

## B.1 — Make sure Module 4's stack is running first

```bash
cd ../module4-storage
docker compose up -d postgres redis
docker compose ps
```

## B.2 — Create your `.env` file

```bash
cd ../module5-search
cp .env.example .env
```

No changes needed — `docker-compose.yml`'s `search-api` service overrides
`DATABASE_URL` and `REDIS_URL` to use Module 4's container hostnames
(`postgres`, `redis`) automatically, since it joins Module 4's Docker
network.

## B.3 — Build the image

```bash
docker compose build
```

This installs the CPU-only PyTorch wheel, the rest of `requirements.txt`,
and pre-downloads the embedding model into the image (~420MB), same
pattern as Module 4. Expect several minutes on the first build; the
resulting image is roughly 1.3-1.6GB.

## B.4 — Start the service

```bash
docker compose up -d
```

This joins the `promptflow_shared_net` external network (created by
Module 4's compose stack) so `search-api` can resolve `postgres` and
`redis` by hostname.

```bash
docker compose ps
```

If this fails with a network-not-found error, it means Module 4's stack
isn't running yet — go back to step B.1. (Module 4's `docker-compose.yml`
pins its network to the fixed name `promptflow_shared_net` specifically
so Module 5 can reliably join it regardless of which directory name you
unzipped Module 4 into.)

## B.5 — Check logs

```bash
docker compose logs -f search-api
```

```
INFO | Module 5 Search service starting...
INFO | Warming up embedding model...
INFO | Embedding model ready.
INFO | Started Redis pub/sub invalidation listener (channel=search_invalidate)
INFO:     Uvicorn running on http://0.0.0.0:8005
```

## B.6 — Generate a test token and smoke test

```bash
docker compose exec search-api python scripts/make_test_token.py --role admin --dept CSE
```

Copy the printed token, then from your host machine:

```bash
export TEST_JWT_TOKEN="<paste token here>"
curl http://localhost:8005/health
curl -X POST http://localhost:8005/api/v1/search \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "attention mechanism", "mode": "hybrid", "limit": 10}'
```

## B.7 — Running tests inside the container

```bash
docker compose run --rm search-api pytest tests/unit/ tests/integration/ -v
```

## B.8 — Running Locust against the Dockerized service

Locust is already installed inside the image (it's in `requirements.txt`):

```bash
docker compose exec search-api locust -f tests/performance/locustfile.py \
  --host http://localhost:8005 --users 20 --spawn-rate 5 --run-time 1m --headless
```

Or simpler — run Locust from your **host** machine (Path A's venv) against
the Dockerized API's exposed port `8005`, which is often easier since you
get the interactive web UI without extra port-forwarding inside the
container.

## B.9 — Stopping everything

```bash
docker compose down                              # stops search-api only
cd ../module4-storage && docker compose down     # stops shared infra
```

---

## 2. Verifying RLS / department scoping works correctly

Generate two tokens for two different departments and confirm each only
sees their own department's results (assuming Module 4 has papers from
multiple departments):

```bash
export TOKEN_CSE=$(python scripts/make_test_token.py --role faculty --dept CSE | tail -1)
export TOKEN_ECE=$(python scripts/make_test_token.py --role faculty --dept ECE | tail -1)

curl -X POST http://localhost:8005/api/v1/search \
  -H "Authorization: Bearer $TOKEN_CSE" -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "mode": "keyword"}' | python3 -m json.tool

curl -X POST http://localhost:8005/api/v1/search \
  -H "Authorization: Bearer $TOKEN_ECE" -H "Content-Type: application/json" \
  -d '{"query": "machine learning", "mode": "keyword"}' | python3 -m json.tool
```

Results should differ based on department (unless you're using the
`admin` role, which the locked design treats as seeing across all
departments).

---

## 3. Common problems and fixes

**`docker compose up` fails with "network promptflow_shared_net not found"**
Module 4's stack isn't running. Start it first (`cd ../module4-storage &&
docker compose up -d postgres redis`), which creates the network. Module
5 only *joins* this network — it doesn't create it.

**`curl .../health` shows `"database": false`**
Module 4's Postgres isn't reachable. Check `docker ps` for `m4_postgres`
being `Up`, and confirm `.env`'s `DATABASE_URL` port matches what Module
4's compose exposes (`5433` on host, `5432` inside the Docker network).

**Search always returns empty results**
Either Module 4 has no `PUBLISHED` papers yet, or none have a populated
`embedding` column (affects semantic/hybrid modes specifically — keyword
mode doesn't need embeddings). Check directly:
```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT status, COUNT(*), COUNT(embedding) FROM papers GROUP BY status;"
```

**401 Unauthorized on every request**
You forgot the `Authorization: Bearer <token>` header, or your
`TEST_JWT_TOKEN` shell variable expired/was cleared in a new terminal
session. Regenerate with `scripts/make_test_token.py`.

**Cache invalidation listener never fires**
Confirm Module 5's Redis connection is pointed at the SAME Redis instance
Module 4 (or your manual test) publishes to. `REDIS_PUBSUB_CHANNEL` must
match exactly (`search_invalidate` by default in both `.env` files).

**Embedding model download fails / times out**
Network/firewall issue reaching `huggingface.co`. Retry step A.4 —
downloads typically resume rather than restart from zero.

**`pip install torch` pulls a multi-GB CUDA build despite `--index-url`**
You likely already have a cached/installed torch from an earlier attempt
that grabbed the CUDA build first. `pip uninstall torch -y`, then redo
step A.2 before step A.3.

---

## 4. AWS Deployment Notes (ECS Fargate)

Per the locked sizing decision: **1 vCPU / 2GB RAM** for the search-api
task (the embedding model + PyTorch runtime need headroom beyond what a
lighter API-only service would require).

**The Terraform for this now exists** in `terraform/` (it didn't when
this guide was first written -- see `TERRAFORM_TESTING.md` for how it
was validated: structural checks, a checkov security scan, and a custom
pytest suite, same rigor as Module 4's). It's structured as mostly
`data` lookups against Module 4's already-deployed VPC/RDS/Redis/KMS/
Secrets Manager resources, plus Module 5's own ECS Fargate layer.

The `security_groups` module is active by default (it only needs Module
4 to already be deployed in AWS -- no other external inputs). The `ecs`
and `iam` modules are fully built but commented out in root `main.tf`,
pending exactly the same category of inputs Module 4's own ECS module is
still waiting on:

1. An ECR repository URL for Module 5's image.
2. `JWT_PUBLIC_KEY` -- Module 1's RS256 public key (PEM format). Set
   `SKIP_JWT_VALIDATION=false` once this is available; the local-dev
   bypass must NEVER be enabled in a deployed environment.
3. `redis_url` -- get this via `terraform output -raw redis_url` from
   Module 4's stack. Module 5 wraps it in its own Secrets Manager entry
   (Module 4 doesn't currently wrap it in one itself -- see
   `TERRAFORM_TESTING.md` section 3.2) rather than ever passing it as a
   plaintext environment variable.
4. An ALB target group ARN + listener ARN (`target_group_arn` /
   `alb_listener_arn`) -- reusing Module 4's ALB once one exists, or a
   dedicated one for Module 5.

The database connection currently reuses Module 4's write-capable DB
user (no separate read-only role provisioned yet) -- ask if you want the
`GRANT SELECT`-only role SQL as a follow-up.

Once those four inputs are filled into `terraform/dev.tfvars`, uncomment
the `module "ecs"` and `module "iam"` blocks in `terraform/main.tf` and
run `terraform plan -var-file=dev.tfvars` (see `TERRAFORM_TESTING.md`
section 5 for the exact commands and what to expect).

---

## 5. One thing to flag back to Module 4

Module 5's cache invalidation relies on Module 4 running this command
after every successful paper write:

```
PUBLISH search_invalidate "paper_updated"
```

If Module 4's consumer (`app/consumer.py`) doesn't already do this, it's
a small addition there (a single `redis_client.publish(...)` call after
the repository's create/update commit succeeds). Flag it back if you'd
like that line added directly into Module 4's consumer code — it's a
small, isolated edit.
