# Module 6 Migration — How To Apply It

Module 6 does **not** run its own independent Alembic environment. It
shares the exact same PostgreSQL database as Module 4, and Module 4
already owns the working `alembic.ini` + `migrations/env.py` pointed at
that database.

Running a *second*, independent Alembic setup against the same physical
database would create two separate `alembic_version` tracking rows that
don't know about each other — a well-known footgun (you'd get
"Target database is not up to date" errors, or worse, silently
conflicting migration histories).

So instead: **this one file gets copied into Module 4's migration
chain.**

## Steps

```bash
# 1. Copy this migration into Module 4's versions directory
cp 002_module6_reports.py ../module4-storage/migrations/versions/

# 2. From Module 4's directory, apply it
cd ../module4-storage
source venv/bin/activate   # or however you activated Module 4's venv
alembic upgrade head
```

That's it. `down_revision = "001"` in this file already points at
Module 4's existing head revision, so Alembic picks it up automatically
as the next step in the chain.

## What it creates

- `generated_reports` — one row per report generation request (tracks
  PENDING → GENERATING → COMPLETED/FAILED status, s3_uri, checksum)
- `report_checksums` — immutable audit trail of checksum events
  (REVOKE UPDATE/DELETE, same pattern as Module 4's `audit_log`)

Module 6 writes audit entries into Module 4's **existing** `audit_log`
table — there is no separate audit table for this module.

## Verifying it landed correctly

```bash
docker exec m4_postgres psql -U promptflow -d promptflow -c "\dt generated_reports report_checksums"
docker exec m4_postgres psql -U promptflow -d promptflow -c \
  "SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name='report_checksums';"
```

## This was actually tested, not just written

This migration's `upgrade()` function was executed against a real local
PostgreSQL 16 instance (installed via apt, not Docker, due to sandbox
constraints during development) using Alembic's `MigrationContext` +
`Operations` directly — not just read for syntax. Confirmed:

- Both tables created with all constraints or columns intact
- The `report_checksums_report_id_fkey` foreign key to `generated_reports`
  works (`ON DELETE CASCADE`)
- All three CHECK constraints (`report_type`, `output_format`, `status`)
  enforce their enums correctly
- A real `INSERT INTO generated_reports (...)` round-tripped correctly,
  confirming `status` defaults to `'PENDING'` and `report_id` defaults
  to `uuid_generate_v4()`
- The `audit_writer` role grants show exactly `INSERT, SELECT` (no
  `UPDATE`/`DELETE`) on `report_checksums`, confirming the immutability
  pattern matches Module 4's `audit_log`
