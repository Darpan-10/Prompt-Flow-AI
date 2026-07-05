# Module 6: Critical Patch — Cross-Module RLS Security Sweep

**Date:** following a requested systematic audit of Modules 4, 5, and 6
against 5 RLS failure classes (missing context-setting, transaction/pool
leakage, JWT claim fallback gaps, background-task context loss,
admin-bypass NULL-handling gaps).

Two of the sweep's most severe findings live in Module 4's migration
(which Module 6 shares — Module 6 extends the same physical database
rather than owning its own schema for `papers`/`validation_issues`). See
Module 4's `CRITICAL_PATCH_NOTES.md` for the full writeup:

1. **RLS was completely inert against the real deployed connection
   role.** PostgreSQL does not apply RLS policies to a table's owner by
   default. `docker-compose.yml`'s `POSTGRES_USER` / Terraform's RDS
   `master_username` is `"promptflow"` for all three modules — the same
   role that runs Alembic (owning every table) and the same role every
   module's `DATABASE_URL` connects as. Fixed via
   `ALTER TABLE ... FORCE ROW LEVEL SECURITY` in Module 4's migration.
   Module 6 automatically benefits once that migration is applied — no
   code changes needed here for this specific finding.
2. **Two PERMISSIVE policies on `papers` combined via OR to leak every
   department's PUBLISHED/PENDING_REVIEW/REJECTED papers** to any
   authenticated non-admin user. Fixed by replacing both with a single,
   correctly-scoped policy in Module 4's migration. Same automatic
   benefit for Module 6 — including the compliance-gate query in
   `report_service.py`, though that query already has its own explicit
   `WHERE p.department_code = :dept` clause independent of RLS, so it
   was never at risk of the specific over-permissive leak Finding 2
   describes (a useful confirmation that the defense-in-depth pattern
   of pairing RLS with explicit application-level scoping worked as
   intended here).

## What changed in Module 6 itself

Auditing Module 6's own test infrastructure surfaced a real
false-confidence gap: `tests/integration/fixture_schema.sql` (used by
the existing 7-test `test_rls_context.py` suite) enabled RLS correctly
but never applied `FORCE ROW LEVEL SECURITY`, and `SETUP.md`'s
documented setup had a `postgres` superuser create the schema before
GRANTing access to a separate, non-owner `promptflow_test_user` role.
This means those 7 tests would have passed even if the REAL Module 4
migration were still missing `FORCE ROW LEVEL SECURITY` — they were
testing a "granted role, not owner" scenario that doesn't match
production's single-role model.

Fixed both:
- `fixture_schema.sql` now includes
  `ALTER TABLE papers FORCE ROW LEVEL SECURITY;` (and the same for
  `validation_issues`).
- `SETUP.md`'s setup instructions now have `promptflow_test_user` create
  its OWN database and schema (`CREATE DATABASE promptflow_test OWNER
  promptflow_test_user`, then load the fixture AS that role) rather
  than a superuser creating everything and granting access afterward.

Module 6's own schema (`generated_reports`/`report_checksums`) has no
RLS at all, by design — department scoping for those tables is enforced
at the application layer in `app/routes/reports.py` (an explicit
`if data["department_code"] != user.department_code: raise 404` check),
which was already correct and is unaffected by any of this sweep's
findings.

## Verification performed

Re-ran Module 6's full test suite (28 unit + 23 integration = 51 tests)
against the corrected fixture (owner-role model, `FORCE ROW LEVEL
SECURITY` applied) — all 51 pass, confirming Module 6's actual query
logic was already correct; only the test *methodology* needed
correcting to actually prove that with the rigor the production role
model demands.
