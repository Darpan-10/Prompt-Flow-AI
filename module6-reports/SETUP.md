# Module 6: NAAC Report Generator — Setup Guide

Manual, step-by-step instructions. No scripts run anything for you. Two
paths are documented:

- **Path A — Native (venv, Docker only for shared infra + local S3)**
- **Path B — Fully Dockerized**

Module 6 is **read-write** against the SAME shared PostgreSQL Module 4
owns (unlike Module 5, which is read-only). It also needs an actual
S3-compatible storage backend to test report upload/download end-to-end
-- this guide uses MinIO locally so you don't need real AWS credentials
during development.

**Module 4 must already be set up and running** before Module 6 can do
anything useful, since Module 6 reads `papers`/`validation_issues` for
the compliance gate and its own tables live in that same database.

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
unzip module6-reports.zip
cd module6-reports
```

Confirm the structure:

```bash
find . -maxdepth 2 -type f | sort
```

(Top-level files plus subdirectories under `app/routes`, `app/services`,
`app/templates/reports`, `tests/unit`, `tests/integration`,
`tests/terraform`, `terraform/modules`)

---

## 2. Apply the database migration FIRST

Module 6 does not run its own Alembic environment -- it shares Module
4's. Read `migrations/README.md` for the full explanation; the short
version:

```bash
cp migrations/002_module6_reports.py ../module4-storage/migrations/versions/
cd ../module4-storage
source venv/bin/activate
alembic upgrade head
cd ../module6-reports
```

Verify it landed:

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c "\dt generated_reports report_checksums"
```

---

# PATH A -- Native Python (recommended for active development)

## A.1 -- Create and activate a virtual environment

```bash
python3.11 -m venv venv
source venv/bin/activate        # Linux/macOS
```

## A.2 -- Install dependencies

Unlike Module 4/5, Module 6 has **no PyTorch/sentence-transformers
dependency at all** -- no embedding model, so no CPU-only-wheel dance
needed here.

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This installs FastAPI, SQLAlchemy 2.0 (async), asyncpg, **WeasyPrint**
(PDF rendering), **openpyxl** (Excel rendering), Jinja2, boto3, PyJWT,
pytest, and `moto[s3]` (an in-memory S3 mock used by some tests).

## A.3 -- Install WeasyPrint's system dependencies

This is the one step genuinely unique to Module 6. WeasyPrint is a pure
Python PDF renderer, but it links against real native libraries for text
shaping (Pango) -- these are **not** installable via pip.

**Important:** if you've seen WeasyPrint Docker tutorials online listing
`libcairo2`, `libgdk-pixbuf2.0-0`, `libffi-dev`, etc -- that's the
**old** dependency list from before WeasyPrint dropped its
pycairo/PyGObject bindings. This project pins `weasyprint==69.0`, whose
actual dependencies (verified by inspecting WeasyPrint's own `ffi.py`
source for its exact `dlopen()` calls -- not guessed, not copied from a
possibly-outdated blog post) are:

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    fonts-liberation \
    fonts-dejavu-core
```

**macOS (Homebrew):**
```bash
brew install pango
```

**Verify it works:**
```bash
python3 -c "
from weasyprint import HTML
pdf = HTML(string='<h1>Test</h1>').write_pdf()
print('PDF generated:', len(pdf), 'bytes, magic:', pdf[:8])
"
```
Expected: `PDF generated: <some number> bytes, magic: b'%PDF-1.7'`

If this fails with something like `OSError: cannot load library
'pango-1.0'`, the system package above either isn't installed or isn't
on your library search path -- this is a WeasyPrint/OS issue, not a
Python one; reinstalling `weasyprint` via pip won't fix it.

## A.4 -- Create your `.env` file

```bash
cp .env.example .env
```

Defaults point at Module 4's local Postgres (`5433`) and a **local MinIO
instance** (`http://localhost:9100`) for S3-compatible storage -- see
step A.5.

## A.5 -- Start MinIO (local S3-compatible storage)

Module 6 needs somewhere to actually upload report bytes to for
meaningful local testing. Rather than requiring real AWS credentials for
local dev, this project runs MinIO (an open-source S3-compatible server)
via the `docker-compose.yml` already included:

```bash
docker compose up -d minio minio-init
docker compose ps
```

`minio-init` is a one-shot container that creates the
`promptflow-reports-dev` bucket automatically and then exits -- that's
expected (`Exited (0)`), not a failure.

Confirm the bucket exists via MinIO's web console at
`http://localhost:9101`. Login: `minioadmin` / `minioadmin` (from
`docker-compose.yml` -- local dev only, obviously not production
credentials).

## A.6 -- Confirm Module 4's Postgres is running

```bash
cd ../module4-storage
docker compose up -d postgres
cd ../module6-reports
```

## A.7 -- Generate a local test JWT

```bash
export TEST_JWT_TOKEN=$(python scripts/make_test_token.py --role hod --dept CSE | tail -1)
```

Report generation has role-based authorization (see `app/auth.py`):
- `NAAC_CRITERIA_III` requires `coordinator`, `hod`, or `admin`
- `FACULTY_PROFILE` requires `coordinator`/`hod`/`admin`, OR a `faculty`
  role generating their own profile (`faculty_id` must match the JWT's)

## A.8 -- Run the unit test suite (no database needed)

```bash
pytest tests/unit/ -v
```

Expected: **28 passed**. These test the checksum service, Pydantic
schemas, and S3 URI parsing in isolation.

## A.9 -- Set up a real PostgreSQL test database (for integration tests)

The integration tests run against a **real** PostgreSQL instance, not
mocks -- this is deliberate. Four serious, genuinely dangerous bugs were
found and fixed during this module's development specifically *because*
of testing against a real database instead of relying on mocks:

1. `SET LOCAL app.X = :param` (a bound SQL parameter) raises a
   PostgreSQL syntax error -- `SET` is a utility statement and doesn't
   accept bind parameters for the value.
2. `SET LOCAL app.current_role = ...` separately fails because
   `current_role` is a SQL-reserved keyword.
3. A `session.commit()` in the middle of the report-generation pipeline
   silently wiped the transaction-scoped RLS context for every query
   that followed -- meaning the compliance gate would have ALWAYS passed
   trivially (seeing zero rows, not an error) regardless of real data
   state. This is about as close to a silent NAAC-compliance-safety bug
   as this codebase gets.
4. A raw Python `dict` passed to a JSONB column via `asyncpg` + raw SQL
   silently fails -- unlike psycopg2, asyncpg requires the value be
   pre-serialized to a JSON string first.

None of these would have been caught by tests using a mocked database
session. This is why the integration tests require real Postgres, not
mocks, and why you should actually run them, not skip straight to unit
tests.

```bash
sudo apt-get install -y postgresql postgresql-contrib
sudo service postgresql start
```

Create a test database OWNED BY a non-superuser role -- and critically,
have THAT SAME role create the schema (not a superuser who then GRANTs
access afterward). This matters: PostgreSQL RLS policies do not restrict
a table's OWNER by default (a separate exemption from the well-known
superuser bypass), and production's docker-compose.yml/Terraform both
use the SAME role ("promptflow") to both run migrations (owning every
table) AND connect as the running application. A test setup where a
superuser creates the tables and a different, merely-GRANTed role reads
them does NOT match this -- it would pass even if FORCE ROW LEVEL
SECURITY were missing, giving false confidence (this exact gap existed
in an earlier version of this test suite, found and fixed during a
cross-module RLS security sweep).

```bash
sudo -u postgres psql -c "CREATE ROLE promptflow_test_user LOGIN PASSWORD 'testpass' NOSUPERUSER CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE promptflow_test OWNER promptflow_test_user;"
sudo -u postgres psql -d promptflow_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
```

Load the test fixture schema AS promptflow_test_user (not as postgres),
so that role owns every table it creates -- matching production exactly:

```bash
PGPASSWORD=testpass psql -h localhost -U promptflow_test_user -d promptflow_test \
  -f tests/integration/fixture_schema.sql
```

## A.10 -- Run the integration test suite

```bash
TEST_DATABASE_URL="postgresql+asyncpg://promptflow_test_user:testpass@localhost/promptflow_test" \
  pytest tests/integration/ -v
```

Expected: **23 passed**. This includes:
- `test_rls_context.py` (7 tests) -- proves RLS context setting actually
  works, including the dangerous "zero rows without context" failure
  mode and the admin-bypass condition
- `test_compliance_gate.py` (9 tests) -- proves the exact compliance-gate
  SQL from the spec (unresolved errors, pending papers, year/department
  scoping) behaves correctly against real data
- `test_full_pipeline.py` (7 tests) -- the closest thing to true
  end-to-end: real WeasyPrint PDF rendering, real openpyxl Excel
  rendering, real PostgreSQL writes, with only S3 mocked out. Confirms
  actual PDF magic bytes (`%PDF-`) and actual XLSX/ZIP magic bytes
  (`PK`) reach the (mocked) S3 upload call.

## A.11 -- Start the reports API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8006 --reload
```

## A.12 -- Smoke test

```bash
curl http://localhost:8006/health
```

Browse the interactive docs at `http://localhost:8006/docs`.

## A.13 -- Generate a real report end-to-end

First, make sure Module 4 has at least one `PUBLISHED` paper for the
department/year you're about to request (otherwise you'll get a valid,
successfully-generated report -- it'll just be an "N=0 papers" empty
report, which is a legitimate and correctly-handled state, not an
error).

```bash
curl -X POST http://localhost:8006/api/v1/reports/generate \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "report_type": "NAAC_CRITERIA_III",
    "department_code": "CSE",
    "academic_year": "2023-2024",
    "output_format": "pdf"
  }'
```

This returns immediately with `status: PENDING` and a `report_id` --
generation runs as a background task, not inline in the request (per
the locked anti-pattern rule). Poll for completion:

```bash
export REPORT_ID="828da4fd-d1d4-4e5e-b13a-35455a59b7ae"

curl http://localhost:8006/api/v1/reports/$REPORT_ID \
  -H "Authorization: Bearer $TEST_JWT_TOKEN"
```

Once `status` becomes `COMPLETED`, get a download link:

```bash
curl http://localhost:8006/api/v1/reports/download/$REPORT_ID \
  -H "Authorization: Bearer $TEST_JWT_TOKEN"
```

This returns a pre-signed MinIO URL (1-hour expiry). Open it directly in
a browser or `curl` it to download the actual PDF.

## A.14 -- Verify the checksum was recorded correctly

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT status, checksum_sha256, total_papers FROM generated_reports WHERE report_id = '$REPORT_ID';"
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT event_type, checksum_sha256 FROM report_checksums WHERE report_id = '$REPORT_ID';"
```

Both checksums should match exactly.

## A.15 -- Test the compliance gate actually blocks bad data

Insert an unresolved error-severity validation issue for a paper in the
department/year you're testing, then try generating a report for that
same scope -- it should fail with a clear error message instead of
producing a report:

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c "
INSERT INTO validation_issues (paper_id, severity, message)
SELECT paper_id, 'error', 'Test: manually injected for compliance gate testing'
FROM papers WHERE department_code = 'CSE' AND status = 'PUBLISHED' LIMIT 1;
"
```

Generate again, then poll -- `status` should become `FAILED` with an
`error_message` mentioning "unresolved validation error(s)". S3 upload
must never have been attempted for a blocked report.

## A.16 -- Stopping everything

```bash
docker compose down                              # stops MinIO
cd ../module4-storage && docker compose down     # stops shared Postgres/Redis
```

---

# PATH B -- Fully Dockerized

## B.1 -- Make sure Module 4's stack is running first

```bash
cd ../module4-storage
docker compose up -d postgres
```

## B.2 -- Create your `.env` file

```bash
cd ../module6-reports
cp .env.example .env
```

No changes needed -- `docker-compose.yml`'s `reports-api` service
overrides `DATABASE_URL` and the MinIO-related S3 settings automatically.

## B.3 -- Build the image

```bash
docker compose build
```

This installs the WeasyPrint system dependencies via `apt-get` inside
the image, then the Python requirements. Much faster/smaller than Module
4/5's builds since there's no PyTorch/embedding model to download.

## B.4 -- Start everything

```bash
docker compose up -d
```

This starts `minio` then `minio-init` (creates the bucket, exits
successfully) then `reports-api`, joining the `promptflow_shared_net`
external network so `reports-api` can reach Module 4's Postgres by
hostname.

## B.5 -- Check logs

```bash
docker compose logs -f reports-api
```

## B.6 -- Generate a test token and smoke test

```bash
docker compose exec reports-api python scripts/make_test_token.py --role hod --dept CSE
```

Then from your host machine, same curl examples as Path A steps A.12-A.15.

## B.7 -- Running tests inside the container

```bash
docker compose exec reports-api pytest tests/unit/ -v
```

Integration tests need a `TEST_DATABASE_URL` pointed at a real Postgres
reachable from inside the container -- the simplest option is running
them from Path A's host venv instead (against `localhost`).

## B.8 -- Stopping everything

```bash
docker compose down
cd ../module4-storage && docker compose down
```

---

## 3. Testing the Excel export path

```bash
curl -X POST http://localhost:8006/api/v1/reports/generate \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "report_type": "NAAC_CRITERIA_III",
    "department_code": "CSE",
    "academic_year": "2023-2024",
    "output_format": "xlsx"
  }'
```

Same poll/download flow as the PDF case -- the downloaded file should
open in Excel/LibreOffice/Google Sheets with columns: Title, Authors,
Venue, Year, DOI, Type, Confidence, Faculty Email.

## 4. Testing FACULTY_PROFILE reports

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT DISTINCT faculty_id, faculty_email FROM papers WHERE department_code='CSE' LIMIT 5;"

curl -X POST http://localhost:8006/api/v1/reports/generate \
  -H "Authorization: Bearer $TEST_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "report_type": "FACULTY_PROFILE",
    "department_code": "CSE",
    "academic_year": "2023-2024",
    "faculty_id": "<paste a real faculty_id here>"
  }'
```

## 5. Common problems and fixes

**`ImportError` / `OSError: cannot load library 'pango-1.0'`**
WeasyPrint's system dependencies aren't installed. Revisit step A.3 --
this is an OS-level library issue, `pip install --force-reinstall
weasyprint` will not fix it.

**`docker compose up` fails with "network promptflow_shared_net not
found"**
Module 4's stack isn't running yet. Start it first.

**Report status stuck at `GENERATING` forever**
Check the `reports-api` logs -- `run_generation()` catches every
exception and always sets a terminal status (`COMPLETED` or `FAILED`),
so a report stuck at `GENERATING` almost always means the background
task itself never started (check for an earlier 4xx/5xx on the `POST
.../generate` call itself).

**Compliance gate always passes even with known bad data**
This is the exact dangerous bug described in step A.9, item 3 -- if
you're running a version of `app/services/report_service.py` older than
the fix, upgrade. Confirmed fixed in this delivered version; the
`test_full_pipeline.py::TestFullGenerationPipelineComplianceGateBlocks`
integration test exists specifically to catch a regression here.

**`asyncpg.exceptions.DataError` mentioning `'dict' object has no
attribute 'encode'`**
This is the JSONB-serialization bug (item 4 above) -- if you're editing
`report_service.py` and adding a new raw-SQL `INSERT`/`UPDATE` against a
JSONB column, remember to `orjson.dumps(...).decode()` the value first;
asyncpg does not auto-serialize Python dicts for raw `text()` queries
the way the ORM's JSONB column type does.

**MinIO bucket doesn't exist / `NoSuchBucket` error**
`minio-init` didn't run successfully. Check `docker compose logs
minio-init` -- it should show `Bucket promptflow-reports-dev ready` and
exit with code 0.

**Pre-signed download URL gives `SignatureDoesNotMatch` from MinIO**
Usually means `AWS_ENDPOINT_URL`/`AWS_ACCESS_KEY_ID`/
`AWS_SECRET_ACCESS_KEY` in your running process don't match
`docker-compose.yml`'s MinIO credentials.

---

## 6. AWS Deployment Notes (ECS Fargate)

Module 6's ECS task is sized **0.5 vCPU / 1GB RAM** -- notably smaller
than Module 4/5's Fargate tasks, since there's no embedding model to
load into memory. WeasyPrint rendering is CPU-bound but runs
synchronously in a low-volume background task (one report per
compliance cycle per department, not a high-throughput hot path).

See `TERRAFORM_TESTING.md` for the full validation writeup (checkov
scan, structural checks, custom pytest suite -- same rigor as Module
4/5) and `terraform/` for the actual infrastructure: an S3 bucket
(KMS-encrypted, versioned, 7-year lifecycle, SNS event notifications),
a security group + cross-stack ingress rule into Module 4's RDS, IAM
roles, and a commented-out ECS module pending the same category of
external inputs (ECR repo, ALB, JWT public key) as Module 4/5's.
