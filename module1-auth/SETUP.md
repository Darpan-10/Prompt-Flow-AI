# Module 1: Auth & Access Control — Setup Guide

Manual, step-by-step instructions. No scripts run anything for you — every
command below is meant to be typed (or copy-pasted one block at a time) so
you can see exactly what's happening and stop/adjust at any point.

Two paths are documented:

- **Path A — Native (venv + local PostgreSQL/Redis via Docker only for infra)**
- **Path B — Fully Dockerized (app itself also runs in containers)**

Pick whichever matches how you want to work. You don't need to do both.

---

## 0. Prerequisites

| Tool | Version | Check with |
|---|---|---|
| Python | 3.11.x | `python3 --version` |
| Docker | 24+ | `docker --version` |
| Docker Compose | v2 (the `docker compose` subcommand, not `docker-compose`) | `docker compose version` |

---

## 1. Unzip the project

```bash
unzip module1-auth.zip
cd module1-auth
```

Confirm the structure:

```bash
find . -maxdepth 2 -type f | sort
```

Expected output includes: `Dockerfile`, `docker-compose.yml`, `schema.sql`,
`.env.example`, `requirements.txt`, `app/main.py`, `scripts/generate_keys.py`,
`scripts/create_service_account.py`, `tests/test_auth.py`.

---

# PATH A — Native Python (recommended for active development)

Infra (Postgres, Redis) runs in Docker; the Python app runs directly on
your machine in a venv, so you get fast reload and easy debugging.

## A.1 — Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows (cmd)
# venv\Scripts\Activate.ps1     # Windows (PowerShell)
```

Confirm:
```bash
which python
# should print .../module1-auth/venv/bin/python
```

## A.2 — Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Takes 1-2 minutes.

## A.3 — Generate RS256 keypair (one-time)

JWTs are signed with RS256. Generate your own keypair — never commit
these to git:

```bash
python scripts/generate_keys.py
```

Creates `keys/private.pem` and `keys/public.pem`.

## A.4 — Create your `.env` file

```bash
cp .env.example .env
```

The defaults already match the docker-compose infra ports (Postgres on
`5432`, Redis on `6379` — Module 1 runs its own dedicated instances,
separate from Module 4's, which use `5433`/`6380`). Key variables:

```bash
DATABASE_URL=postgresql://promptflow:secret@localhost:5432/promptflow
REDIS_URL=redis://localhost:6379
APP_ENV=development
JWT_PRIVATE_KEY_PATH=keys/private.pem
JWT_PUBLIC_KEY_PATH=keys/public.pem
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://localhost:8000,http://localhost:8001,http://localhost:8002,http://localhost:8003
```

`ALLOWED_ORIGINS` matters if you're calling this API from a browser (e.g.
Swagger UI on a different port, or a frontend dev server) — the API
issues a `refresh_token` cookie, and `allow_credentials=True` means CORS
can't just be wildcarded (`"*"`); the browser will silently reject that
combination. Add any extra origin you need to this comma-separated list.

## A.5 — Create the shared Docker network (one-time, system-wide)

Module 1 joins `promptflow_shared_net` so it's reachable by Module 3 (for
directory lookups) once the rest of the system is up. If nothing else has
created it yet:

```bash
docker network create promptflow_shared_net || true
```

(`|| true` so this doesn't fail loudly if the network already exists —
e.g. because Module 4 created it first.)

## A.6 — Start infrastructure (Postgres + Redis via Docker)

```bash
docker compose up -d postgres redis
docker compose ps
```

Wait for both to show `healthy` (~10 seconds).

**Schema note:** `docker-compose.yml` mounts `schema.sql` into
`/docker-entrypoint-initdb.d/`, so the official Postgres image runs it
**automatically** the first time the container starts against an empty
data volume — you don't need to run it manually. If you edit `schema.sql`
later and want to re-apply it, you have to wipe the volume first (the
init script only runs once, against a genuinely empty database):

```bash
docker compose down -v postgres   # deletes the volume — all data lost
docker compose up -d postgres     # re-runs schema.sql fresh
```

Verify it actually landed:
```bash
docker compose exec postgres psql -U promptflow -d promptflow -c "\dt"
```

Expected tables: `users`, `service_accounts`, `departments` (plus
`alembic_version` will NOT be here — Module 1 doesn't use Alembic, it
uses this one `schema.sql` file directly).

**Important:** `psql` only exists inside the `postgres` container (the
official Postgres image ships it) — Module 1's own `auth` container is a
plain `python:3.11-slim` image and does **not** have `psql` installed.
Any raw-SQL command below targets `postgres`, not `auth`.

## A.7 — Bootstrap your first admin user

There's a chicken-and-egg problem: provisioning new users requires an
admin JWT, but you don't have one yet. Insert the very first admin
directly:

```bash
docker compose exec postgres psql -U promptflow -d promptflow -c "
INSERT INTO users (user_id, email, name, role, department_code, is_active)
VALUES ('user_admin001', 'admin@srmap.edu.in', 'Local Admin', 'admin', NULL, true)
ON CONFLICT (user_id) DO NOTHING;
"
```

Verify it landed and check the auto-minted `faculty_id`:
```bash
docker compose exec postgres psql -U promptflow -d promptflow -c \
  "SELECT user_id, faculty_id, email, role FROM users;"
```

## A.8 — Run the test suite

```bash
pytest tests/test_auth.py -q --asyncio-mode=auto
```

Expected: **14 passed, 1 skipped** (the skip is
`test_audit_log_no_update_permission` — that table is owned by Module 4's
migration, not Module 1, so it's only meaningful when running against the
full integrated system). There are 2 known pre-existing failures in the
M2M endpoint tests (`test_m2m_token_endpoint_*`) caused by an
unrelated `MagicMock`/`AsyncMock` mismatch in the test fixture, not
anything this setup guide affects.

## A.9 — Start the app

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## A.10 — Smoke test

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"auth","version":"1.0.0"}
```

Browse the interactive docs:
```
http://localhost:8000/docs
```

## A.11 — Provision a service account for Module 3's M2M auth

Module 3 calls Module 1's directory API using an M2M (machine-to-machine)
token. Provision credentials for it:

```bash
python scripts/create_service_account.py module3-ai-worker "Module 3 AI Worker"
```

This prompts for a secret (press Enter to auto-generate one) and prints
it once — save it, it's not retrievable later. Put it in
`module3-ai-worker/.env` as `M2M_CLIENT_SECRET`.

**Note:** this only works when `APP_ENV != production` (see
`app/services/cognito.py::verify_m2m_client`) — it checks the local
`service_accounts` table instead of real AWS Cognito, which is what
makes fully-local M2M auth possible without any AWS account at all.

## A.12 — Test the M2M + directory flow end-to-end

```bash
SECRET="<paste the secret from A.11>"

TOKEN=$(curl -s -X POST http://localhost:8000/auth/m2m/token \
  -u "module3-ai-worker:$SECRET" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/faculty/admin
```

Expected:
```json
{"faculty_id":"<uuid>","faculty_name":"Local Admin","faculty_email":"admin@srmap.edu.in","department_code":null,"faculty_status":"active"}
```

## A.13 — Stopping everything

```bash
# Ctrl+C in the uvicorn terminal
docker compose down          # stops Postgres/Redis, keeps volumes
# docker compose down -v     # stops AND deletes all data
```

---

# PATH B — Fully Dockerized

Use this if you'd rather not manage a local Python environment at all.

## B.1 — Create the shared network

```bash
docker network create promptflow_shared_net || true
```

## B.2 — Create `.env`

```bash
cp .env.example .env
```

No changes needed for local docker-compose — `docker-compose.yml`'s
`auth` service already points `DATABASE_URL`/`REDIS_URL` at the
container hostnames (`postgres`, `redis`), overriding whatever's in `.env`.

## B.3 — Generate keys (before building, so they get baked into the image... actually, mounted at runtime)

The Dockerfile creates an empty `keys/` directory but does NOT generate
keys itself (you shouldn't bake private keys into an image layer).
Generate them on the host first — `docker-compose.yml` doesn't currently
mount `keys/` as a volume, so for Path B you need them present before
`docker compose build`:

```bash
python3 -m venv /tmp/keygen-venv && source /tmp/keygen-venv/bin/activate
pip install --quiet cryptography
python scripts/generate_keys.py
deactivate && rm -rf /tmp/keygen-venv
```

(A throwaway venv just for `cryptography` — you don't need the full
`requirements.txt` installed on the host for Path B.)

## B.4 — Build and start

```bash
docker compose build --no-cache
docker compose up -d
docker compose ps
```

Wait for `postgres`, `redis`, and `auth` to all show healthy (~20-30
seconds — `schema.sql` is applied automatically on Postgres's first
startup, same as Path A).

## B.5 — Check logs

```bash
docker compose logs -f auth
```

Expected:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Ctrl+C to stop following.

## B.6 — Smoke test

```bash
curl http://localhost:8000/health
```

## B.7 — Bootstrap first admin user

Same as Path A, just via the containerized Postgres directly (same
service name either way):

```bash
docker compose exec postgres psql -U promptflow -d promptflow -c "
INSERT INTO users (user_id, email, name, role, department_code, is_active)
VALUES ('user_admin001', 'admin@srmap.edu.in', 'Local Admin', 'admin', NULL, true)
ON CONFLICT (user_id) DO NOTHING;
"
```

## B.8 — Provision Module 3's service account

`scripts/create_service_account.py` needs `asyncpg`, which isn't on your
host in Path B — run it inside the `auth` container instead, pointed at
the containerized Postgres:

```bash
docker compose exec auth python scripts/create_service_account.py module3-ai-worker "Module 3 AI Worker"
```

## B.9 — Run tests inside the container

```bash
docker compose exec auth pytest tests/test_auth.py -q --asyncio-mode=auto
```

## B.10 — Stopping everything

```bash
docker compose down       # keep data volumes
docker compose down -v    # wipe everything, fresh start next time
```

---

## 2. Common problems and fixes

**`psql: command not found` when running a command against `auth`**
`psql` only exists in the `postgres` container (official Postgres image).
Module 1's own `auth` container is `python:3.11-slim` and doesn't have
it. Target `postgres`, not `auth`, for any raw SQL.

**CORS errors in the browser (`No 'Access-Control-Allow-Origin' header`)**
Add your actual origin (protocol + host + port, exactly as the browser
sends it) to `ALLOWED_ORIGINS` in `.env`, comma-separated, then restart.
Because `allow_credentials=True` (the refresh_token cookie needs it),
this can never be `"*"` — browsers reject that combination outright.

**`docker compose up` fails with "network promptflow_shared_net not found"**
Run `docker network create promptflow_shared_net` once — see step A.5/B.1.
If Module 4 is already running, it creates this network itself; you only
need the manual step if Module 1 is the first thing you're starting.

**Schema changes not showing up after editing `schema.sql`**
The init script only runs once, against a genuinely empty Postgres data
volume. `docker compose down -v postgres && docker compose up -d postgres`
to force a fresh re-apply (this deletes existing data).

**M2M token request returns 401**
- Confirm `APP_ENV` is NOT `production` in `.env` (real Cognito isn't
  configured for local dev — see A.11's note).
- Confirm the service account was actually created:
  ```bash
  docker compose exec postgres psql -U promptflow -d promptflow -c \
    "SELECT client_id, is_active FROM service_accounts;"
  ```
- Double check you're passing `-u "client_id:secret"` exactly as printed
  by `create_service_account.py` — the secret is not retrievable after
  that first print (it's stored as a salted hash, not recoverable).

**`GET /api/faculty/{key}` returns 404**
The lookup matches on `user_id`, full `email`, or the local-part before
`@`. Confirm the user actually exists:
```bash
docker compose exec postgres psql -U promptflow -d promptflow -c \
  "SELECT user_id, email FROM users;"
```

---

## 3. What's NOT covered by this guide

- AWS deployment (Cognito, RDS) — see `terraform/` for that; this guide
  is local-only. In production, `verify_m2m_client` uses real Cognito,
  not the local `service_accounts` table fallback.
- A real login flow via Cognito's Authorization Code + PKCE flow — that
  requires a configured Cognito User Pool. For local testing, use the
  M2M flow (A.11/A.12) or insert users directly (A.7) instead.
