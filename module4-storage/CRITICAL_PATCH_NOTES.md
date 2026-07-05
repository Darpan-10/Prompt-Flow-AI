# Module 4: Critical Patch — RLS Context Setting Was Broken

**Date discovered:** while building Module 6 (NAAC Report Generator)
**Severity:** Critical. Every RLS-gated database operation in this
module would have raised a runtime error the moment it ran against a
real PostgreSQL connection via asyncpg.

## What was wrong

`app/database.py`'s `set_rls_context()` and `set_admin_context()`, plus
a second standalone call site in `app/repository/repository.py`'s
`PaperRepository.update()`, used this pattern:

```python
await session.execute(text("SET LOCAL app.current_department = :d"), {"d": department_code})
await session.execute(text("SET LOCAL app.current_role = :r"), {"r": role})
```

Two independent, real bugs in this pattern, both verified empirically
against a real PostgreSQL 16 instance (not just reasoned about):

1. **`SET LOCAL ... = :param` does not accept bind parameters.**
   PostgreSQL's `SET` command is a utility statement, not regular DML —
   it requires either a literal constant or a function call for the
   value, not a protocol-level bind parameter. Running this through
   SQLAlchemy + asyncpg (which sends `:d` as a `$1` placeholder) raises:
   ```
   asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "$1"
   ```
   This is easy to miss because typing the equivalent SQL with a
   *literal* value directly into `psql` (`SET LOCAL app.current_department = 'CSE';`)
   works completely fine — the bug only manifests when the value is
   parameterized, which only an actual application code path (not manual
   `psql` testing) would exercise.

2. **`SET LOCAL app.current_role = ...` fails independently, even with a
   literal value**, because `current_role` is a SQL-reserved keyword
   (synonym for `CURRENT_USER`) that PostgreSQL's grammar special-cases
   even as the second component of a dotted custom GUC name:
   ```
   ERROR: syntax error at or near "current_role"
   ```

Neither bug was caught by the existing 42-test suite in
`tests/test_module4.py`, because all of those tests validate Pydantic
schemas, routing logic, and consumer parsing with the database session
entirely mocked out — none of them execute real SQL against a real
PostgreSQL instance.

## The fix

Both call sites now use `set_config(name, value, true)` instead of
`SET LOCAL`:

```python
await session.execute(text("SELECT set_config('app.current_department', :d, true)"), {"d": department_code})
await session.execute(text("SELECT set_config('app.current_role', :r, true)"), {"r": role})
```

`set_config()` is a regular PostgreSQL function, which fixes both
problems at once:
- Being a function call, it accepts bind parameters normally.
- The variable name is passed as a **string argument**, not bare SQL
  syntax — so the `current_role` reserved-keyword restriction (which
  only applies to the bare `SET ... current_role` identifier form)
  simply doesn't apply.
- The third argument `true` means `is_local`, exactly equivalent to
  `SET LOCAL`'s transaction-scoping behavior.

**No changes were needed to any RLS `POLICY` definitions.** Module 4's
policies read context via `current_setting('app.current_role', true)` —
already a function call taking a string argument, which was never
affected by either bug. Only the *write* side (`set_rls_context()` /
`set_admin_context()`) needed fixing.

## Files changed

- `app/database.py` — `set_rls_context()`, `set_admin_context()`
- `app/repository/repository.py` — `PaperRepository.update()`'s
  standalone `change_reason` context-setting call

## New test added

- `tests/integration_real_db/test_set_rls_context_real_db.py` — runs
  against a **real PostgreSQL instance** (skipped automatically if
  `TEST_DATABASE_URL` isn't set) and would have caught this bug
  immediately. Confirms `set_rls_context()`/`set_admin_context()` don't
  raise for any role (including `admin`, where the reserved-keyword bug
  hit hardest), and that values set actually round-trip correctly via
  `current_setting()`.
- `pytest.ini` was added (didn't previously exist) with
  `asyncio_mode = auto` so this new async test file can run.

To run it:

```bash
createdb module4_rls_test
psql module4_rls_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost/module4_rls_test \
    pytest tests/integration_real_db/ -v
```

## Verification performed

- Patched `set_rls_context()`/`set_admin_context()`/the repository call
  site, confirmed via `ast.parse()` the files are syntactically valid.
- Ran the new real-PostgreSQL regression test against an actual
  PostgreSQL 16 instance — **4/4 passed**, including a round-trip check
  that `set_config()`'s bind-parameter substitution actually lands the
  correct value (not just "doesn't raise").
- Re-ran the original `tests/test_module4.py` suite after the patch —
  still **42/42 passed** (unaffected, as expected, since those tests
  never exercised the real SQL path).
- Confirmed `app/main.py`'s FastAPI app still imports cleanly with 20
  routes registered after the patch.

## Why this is the only fix needed in Module 4 (unlike Module 6)

Two related bugs were found and fixed in Module 6 during the same
investigation — a "commit mid-pipeline silently wipes the
transaction-scoped RLS context" bug, and a "raw Python dict not
JSON-serialized for a JSONB column via raw SQL INSERT" bug. **Neither
applies to Module 4**:

- Module 4's consumer (`app/consumer.py`) and its FastAPI routes call
  `set_rls_context()` exactly once per request/message and run all
  subsequent queries/writes within that same uncommitted transaction
  before a single final commit — there's no early commit that could
  wipe the context mid-flow.
- Module 4 writes via the SQLAlchemy ORM (`session.add(paper)`), whose
  JSONB-typed columns handle Python dict serialization automatically —
  it never constructs raw `INSERT ... VALUES (:dict_param)` SQL the way
  Module 6's `audit_log` write did.

---

# SECOND PATCH ROUND: Full Cross-Module RLS Security Sweep

**Date:** following a requested systematic audit of Modules 4, 5, and 6
against 5 specific RLS failure classes (missing context-setting,
transaction/pool leakage, JWT claim fallback gaps, background-task
context loss, admin-bypass NULL-handling gaps).

The sweep found the previous patch (above) was necessary but not
sufficient. Three additional, more severe issues were found, all
verified empirically against real PostgreSQL 16 (not just reasoned
about), all now fixed. Listed in order of severity.

## Finding 1 (MOST SEVERE): RLS was completely inert against the real deployed connection role

**What was wrong:** PostgreSQL does not apply RLS policies to a table's
**owner** by default — a *separate* exemption from the well-known
superuser bypass. `docker-compose.yml`'s `POSTGRES_USER` and
Terraform's RDS `master_username` are both `"promptflow"` — the SAME
role that runs `alembic upgrade head` (and therefore creates and owns
every table) AND the same role the application's `DATABASE_URL`
connects as at runtime. Verified empirically: connecting as this exact
role, with `app.current_department` set to a value that matched **none**
of the seeded rows, every row was still visible — the policies were
being silently skipped entirely, regardless of how correctly they were
written or how correctly the session context was set.

This means every fix in the first patch round (above) — the
`set_config()` fix, all of it — was necessary but would have been
**completely moot** in the actual deployed system as configured, because
the enforcement mechanism itself (RLS) was never actually active against
the role that matters.

Confirmed this is also true on real AWS RDS, not just local Docker:
RDS's master user is explicitly created `WITH LOGIN NOSUPERUSER` (per
AWS's own documentation on the `rds_superuser` role) — so it is *not* a
true PostgreSQL superuser, meaning the ownership-exemption fix below
(`FORCE ROW LEVEL SECURITY`) correctly applies to it. (A TRUE
superuser bypasses RLS unconditionally and FORCE cannot override that —
this only matters for local testing with a real Postgres `postgres`
superuser role, which is why the regression tests below specifically
use a `NOSUPERUSER` role to be meaningful.)

**The fix:** added `ALTER TABLE ... FORCE ROW LEVEL SECURITY;` for
`papers`, `paper_versions`, and `validation_issues` in
`migrations/versions/001_initial_schema.py`.

**Verification performed:**
- Built a minimal reproduction: created a table as a `NOSUPERUSER` role,
  enabled RLS with a simple policy, connected AS that same role with a
  deliberately-wrong session variable, confirmed all rows were visible
  (bug reproduced). Applied `FORCE ROW LEVEL SECURITY`, re-ran the exact
  same query, confirmed zero rows (bug fixed). Confirmed the correct
  department's rows are still visible (fix doesn't overcorrect).
- Ran the ACTUAL, complete `alembic upgrade head` migration (all
  partitions, triggers, indexes) against a real PostgreSQL 16 database,
  using a `NOSUPERUSER` role named `promptflow` (matching production
  exactly), then connected as that same role and repeated the
  wrong-department test against the real `papers` table — confirmed the
  leak is closed end-to-end, not just in an isolated reproduction.
- Added `tests/integration_real_db/test_papers_rls_policy_real_db.py`,
  which builds its test schema using the SAME role it queries with
  (so it's meaningless against a superuser `TEST_DATABASE_URL`, and
  explicitly documents that in its docstring), and proved this test
  actually catches a regression by temporarily removing
  `FORCE ROW LEVEL SECURITY` from the test setup and watching it fail.

## Finding 2: Two PERMISSIVE policies on `papers` combined to leak every department's data

**What was wrong:** the `papers` table had TWO separate `PERMISSIVE`
RLS policies: `dept_isolation_papers` (correct department check) and
`faculty_draft_access` (meant to additionally allow faculty to see
their own drafts). PostgreSQL combines multiple `PERMISSIVE` policies on
the same table with **OR**. The second policy's clause `status IN
('PUBLISHED', 'PENDING_REVIEW', 'REJECTED')` had **no department
qualifier at all** — being OR'd in as a separate policy, this granted
**every authenticated non-admin user visibility into every other
department's PUBLISHED/PENDING_REVIEW/REJECTED papers**, completely
bypassing department isolation for anything except `DRAFT` status.

Verified empirically: a correctly-scoped CSE coordinator (legitimate
session context, non-admin, everything else working exactly as
intended) could `SELECT` an ECE department's `PUBLISHED` paper.

This is a fundamental multi-tenancy failure, independent of Finding 1 —
even in an environment where FORCE ROW LEVEL SECURITY was somehow
already correctly applied (e.g. connecting as a merely-GRANTed
non-owner role), this bug alone would still leak cross-department data.

**The fix:** replaced both policies with a single
`dept_scoped_paper_access` policy where department membership is a hard
AND-ed requirement for everyone except admin, with the
"anyone-in-department can see published" and "I can see my own draft"
exceptions correctly nested INSIDE that department check:

```sql
CREATE POLICY dept_scoped_paper_access ON papers
USING (
    current_setting('app.current_role', true) = 'admin'
    OR (
        department_code = current_setting('app.current_department', true)
        AND (
            status IN ('PUBLISHED', 'PENDING_REVIEW', 'REJECTED')
            OR (
                status = 'DRAFT'
                AND faculty_id::text = current_setting('app.current_user_id', true)
            )
        )
    )
);
```

**Verification performed:** 5 scenarios tested against real PostgreSQL
(CSE coordinator can't see ECE published; own-department + own-draft
visibility preserved; admin sees everything; no-context sees nothing;
same-department-different-faculty can't see another's draft) — all
correct both in an isolated reproduction AND against the real,
completely migrated schema. `paper_versions` and `validation_issues`
were checked and confirmed to have only ONE policy each (not affected by
this specific multi-policy composition issue).

## Finding 3: Every data route defaulted to full admin access when auth headers were simply absent

**What was wrong:** `app/main.py`'s `rls_context()` FastAPI dependency —
used by every single data-touching route in this API — read
`X-Department-Code`/`X-Role`/`X-User-Id` headers with Python `dict.get()`
defaults of `"__admin__"`/`"admin"`/`"system"` respectively. Since these
headers are absent by default on any request that doesn't explicitly
set them, **any request missing these headers silently received full
admin access, bypassing RLS entirely** via `set_admin_context()`. The
old docstring literally said "For development, defaults to admin
bypass" — a convenience shortcut that was never locked down.

**The fix:** missing or empty headers now raise `401 Unauthorized`.
Invalid role values (anything outside the known
faculty/coordinator/hod/admin/system_worker set) also raise `401`. A
new `ALLOW_MISSING_AUTH_HEADERS` setting (default `False`) provides an
explicit, clearly-logged, local-dev-only escape hatch — mirroring
Module 5/6's `SKIP_JWT_VALIDATION` pattern for consistency — that must
never be `true` in a deployed environment.

**Verification performed:** `tests/test_rls_context_fail_closed.py`
(10 tests) — confirms missing/invalid headers raise 401, confirms
`set_admin_context()` is never called for a headerless request (the
literal regression this exists to prevent, verified via a spy rather
than just checking the HTTP status), confirms valid headers still work
correctly for both admin and non-admin roles, confirms the escape hatch
works when explicitly enabled and is `False` by default independent of
any test-time monkeypatching. Additionally reverted to the exact
original vulnerable code and confirmed these tests fail against it
before restoring the fix.

## Two unrelated bugs found and fixed while validating the above

While running the ACTUAL `alembic upgrade head` migration end-to-end
against real PostgreSQL (necessary to validate Findings 1 & 2 against
production-shaped data, not just an isolated reproduction), two
pre-existing, unrelated bugs blocked the migration from completing at
all:

1. **`papers_doi_unique` and `idx_papers_idempotency`** were missing the
   table's partition key (`created_at`) — PostgreSQL requires every
   unique constraint/index on a partitioned table to include all
   partitioning columns. Fixed by adding `created_at` to both. Same
   issue existed on `paper_versions_unique_version` (missing
   `changed_at`) — fixed identically.
2. **The GIN full-text search index expression** (`idx_papers_fts`) had
   a syntax error: `gin(expr1 || expr2)` without an extra wrapping pair
   of parens around the whole concatenated expression is a PostgreSQL
   syntax error (`syntax error at or near "||"`). Fixed by wrapping the
   concatenated `setweight(...) || setweight(...)` expression in parens.

Neither of these is an RLS/security issue — they're schema-correctness
bugs that happened to block full end-to-end testing of the RLS fixes
above. Flagging them here since fixing them was necessary to reach the
finding-3-verification stage, and because they'd otherwise have caused
the migration to fail on first deploy regardless of RLS correctness.

## Module 6's test fixture had the SAME owner-bypass blind spot

While auditing Module 5/6 for equivalent issues:

- Module 5 never had a real-database department-isolation test at all
  (only a syntax/round-trip check for `set_rls_context()`) — added
  `tests/integration_real_db/test_department_isolation_real_db.py`.
- Module 6's existing `fixture_schema.sql` (used by
  `tests/integration/test_rls_context.py`, 7 tests) enabled RLS
  correctly but never applied `FORCE ROW LEVEL SECURITY`, AND its
  documented `SETUP.md` instructions had a `postgres` superuser create
  the schema before GRANTing access to a separate, non-owner test role
  — meaning its 7 passing "department isolation" tests were, like
  Module 4's own tests before this sweep, giving false confidence: they
  would have passed even if the real Module 4 migration were still
  missing `FORCE ROW LEVEL SECURITY`. Fixed both the fixture (added
  `FORCE ROW LEVEL SECURITY`) and `SETUP.md` (test role now creates and
  therefore owns its own schema, matching production). Re-ran Module
  6's full 51-test suite under the corrected setup — still passes.

## Full test count after this sweep

| Module | Mocked/unit tests | Real-PostgreSQL tests | Total |
|---|---|---|---|
| Module 4 | 52 (42 original + 10 fail-closed) | 11 | 63 |
| Module 5 | 54 | 6 (3 original set_config + 3 new department-isolation) | 60 |
| Module 6 | 28 | 23 | 51 |

All 174 tests pass.
