# Module 5: Critical Patch — RLS Context Setting Was Broken

**Date discovered:** while building Module 6 (NAAC Report Generator)
**Severity:** Critical. Every search request (keyword, semantic, hybrid)
and every facets/suggestions request would have raised a runtime error
the moment it tried to set RLS context against a real PostgreSQL
connection via asyncpg.

## What was wrong

`app/repositories/paper_repository.py`'s `set_rls_context()` used this
pattern:

```python
await session.execute(text("SET LOCAL app.current_department = :dept"), {"dept": department_code})
await session.execute(text("SET LOCAL app.current_role = :role"), {"role": role})
```

This is the exact same bug found in Module 4 (which Module 5 originally
copied this pattern from). Two independent, real bugs, both verified
empirically against a real PostgreSQL 16 instance:

1. **`SET LOCAL ... = :param` does not accept bind parameters** —
   PostgreSQL's `SET` is a utility statement, not regular DML, and
   raises `PostgresSyntaxError: syntax error at or near "$1"` when the
   value is a protocol-level bind parameter rather than a literal.
2. **`SET LOCAL app.current_role = ...` fails independently**, even with
   a literal value, because `current_role` is a SQL-reserved keyword
   that PostgreSQL's grammar special-cases.

Neither bug was caught by the existing 54-test suite (`tests/unit/` +
`tests/integration/`), because every one of those tests mocks the
database session entirely — none execute real SQL against a real
PostgreSQL instance.

## The fix

```python
await session.execute(text("SELECT set_config('app.current_department', :dept, true)"), {"dept": department_code})
await session.execute(text("SELECT set_config('app.current_role', :role, true)"), {"role": role})
```

Same fix as Module 4: `set_config()` is a regular function call (bind
parameters work normally) and takes the variable name as a string
argument (sidesteps the `current_role` reserved-keyword restriction
entirely). The third argument `true` matches `SET LOCAL`'s
transaction-scoping. No changes needed to Module 4's RLS `POLICY`
definitions, which Module 5 reads via the same `current_setting(...)`
mechanism, unaffected by either bug.

## Files changed

- `app/repositories/paper_repository.py` — `set_rls_context()`

## New test added

- `tests/integration_real_db/test_set_rls_context_real_db.py` — runs
  against a real PostgreSQL instance (skipped automatically if
  `TEST_DATABASE_URL` isn't set). Confirms the fix doesn't raise for any
  role, that values round-trip correctly via `current_setting()`, and
  specifically tests calling `set_rls_context()` multiple times within
  one transaction (mirroring hybrid search's internal pattern of calling
  both `search_keyword()` and `search_semantic()`, each independently
  re-setting context before its own query).

To run it:

```bash
createdb module5_rls_test
psql module5_rls_test -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'
TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost/module5_rls_test \
    pytest tests/integration_real_db/ -v
```

## Verification performed

- Patched `set_rls_context()`, confirmed via `ast.parse()` the file is
  syntactically valid.
- Ran the new real-PostgreSQL regression test against an actual
  PostgreSQL 16 instance — **3/3 passed**, including a round-trip check
  and a repeated-calls-in-one-transaction check.
- Re-ran the original `tests/unit/` + `tests/integration/` suites after
  the patch — still **54/54 passed** (unaffected, as expected).
- Confirmed `app/main.py`'s FastAPI app still imports cleanly with 9
  routes registered after the patch.

## Why Module 5 needed no other fixes

Module 6's investigation also found two OTHER bugs while building that
module: a "commit mid-pipeline wipes transaction-scoped RLS context"
bug, and a "raw dict not JSON-serialized for JSONB column" bug. **Module
5 is unaffected by both, by design**: it is strictly read-only (`app/
database.py`'s `get_db()` only ever calls `session.rollback()`, never
`session.commit()`), so there is no commit anywhere in a request's
lifecycle that could wipe context mid-flow, and it performs zero
`INSERT`/`UPDATE` statements (confirmed via `grep -rn "INSERT INTO"
app/` returning no results), so the JSONB-serialization-on-write bug
class doesn't apply at all.

---

# SECOND PATCH ROUND: Full Cross-Module RLS Security Sweep

**Date:** following a requested systematic audit of Modules 4, 5, and 6
against 5 RLS failure classes. See Module 4's `CRITICAL_PATCH_NOTES.md`
for the full writeup of two additional critical findings from this
sweep — both live in Module 4's migration (which Module 5 queries
against, since Module 5 owns no schema of its own):

1. **RLS was completely inert against the real deployed connection
   role** (missing `FORCE ROW LEVEL SECURITY` on `papers`/
   `paper_versions`/`validation_issues` — PostgreSQL does not apply RLS
   to a table's owner by default, and the same `promptflow` role both
   owns every table and is what every module's `DATABASE_URL` connects
   as). Fixed in Module 4's migration; Module 5 automatically benefits
   once that migration is applied, no code changes needed here.
2. **Two PERMISSIVE policies on `papers` combined via OR to leak every
   department's PUBLISHED/PENDING_REVIEW/REJECTED papers** to any
   authenticated non-admin user, regardless of department. Fixed by
   replacing both with a single, correctly-scoped policy in Module 4's
   migration. Same automatic benefit for Module 5.

## What changed in Module 5 itself

Module 5 had a real test-coverage gap (not a code bug): its only
real-database RLS test checked that `set_rls_context()` executes
without raising -- it never created an actual `papers` table with the
real policy and confirmed department isolation. Added
`tests/integration_real_db/test_department_isolation_real_db.py` (3
tests), using the corrected policy SQL and, critically, creating the
test schema AS the same role it queries with (matching production's
single-role model exactly -- a test using a merely-GRANTed non-owner
role would give false confidence, since RLS behaves differently for
owners vs. granted roles).

## Verification performed

Ran Module 5's full test suite (54 existing + 3 new = 57... plus the 3
original `test_set_rls_context_real_db.py` real-DB tests = 60 total)
against a `NOSUPERUSER` role that owns its own test schema, matching
production. All 60 pass.
