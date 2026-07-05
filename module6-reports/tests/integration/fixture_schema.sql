-- Minimal test fixture schema: a faithful-enough subset of Module 4's
-- real schema (papers, validation_issues, audit_log with RLS) plus
-- Module 6's own tables (generated_reports, report_checksums), used
-- ONLY for integration testing Module 6's actual SQL queries against a
-- real PostgreSQL instance. NOT the full Module 4 migration -- no
-- partitioning, no triggers -- just enough structure + RLS to prove
-- Module 6's queries behave correctly, INCLUDING the RLS-context
-- requirement (a query that forgets to call set_rls_context() should
-- see zero rows, not an error -- that's the exact behavior being tested).

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE papers (
    paper_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title                TEXT NOT NULL,
    authors              JSONB NOT NULL DEFAULT '[]',
    venue                TEXT,
    year                 INTEGER NOT NULL,
    doi                  TEXT,
    paper_type           VARCHAR(20) NOT NULL,
    faculty_id           UUID NOT NULL,
    faculty_email        TEXT NOT NULL,
    department_code      VARCHAR(20) NOT NULL,
    status               VARCHAR(20) NOT NULL,
    overall_confidence   NUMERIC(4,3) NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE papers ENABLE ROW LEVEL SECURITY;

-- CRITICAL FIX (found via a full cross-module RLS security sweep,
-- verified empirically against real PostgreSQL 16): PostgreSQL RLS
-- policies do NOT apply to a table's OWNER by default. In production,
-- docker-compose.yml's POSTGRES_USER (and Terraform's RDS
-- master_username) is "promptflow" -- the SAME role that runs Alembic
-- (and therefore owns every table) AND the same role the application
-- connects as via DATABASE_URL. Without FORCE ROW LEVEL SECURITY, every
-- policy below is silently a no-op against the real deployed
-- connection. This fixture must include FORCE here too, or these tests
-- give false confidence by only exercising a "granted role, not owner"
-- scenario that does not match production -- see SETUP.md's note on
-- creating the test role as the SCHEMA OWNER, not just granting it
-- privileges after a superuser creates everything.
ALTER TABLE papers FORCE ROW LEVEL SECURITY;

CREATE POLICY dept_isolation_papers ON papers
    USING (
        department_code = current_setting('app.current_department', true)
        OR current_setting('app.current_role', true) = 'admin'
    );

CREATE TABLE validation_issues (
    issue_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    paper_id      UUID NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
    severity      VARCHAR(20) NOT NULL,
    message       TEXT,
    resolved_at   TIMESTAMPTZ
);

ALTER TABLE validation_issues ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_issues FORCE ROW LEVEL SECURITY;

CREATE POLICY dept_isolation_issues ON validation_issues
    USING (
        EXISTS (
            SELECT 1 FROM papers p
            WHERE p.paper_id = validation_issues.paper_id
              AND (
                p.department_code = current_setting('app.current_department', true)
                OR current_setting('app.current_role', true) = 'admin'
              )
        )
    );

CREATE TABLE audit_log (
    audit_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    action          VARCHAR(100) NOT NULL,
    actor_type       VARCHAR(20) NOT NULL,
    actor_id         VARCHAR(200) NOT NULL,
    resource_type    VARCHAR(50) NOT NULL,
    resource_id      VARCHAR(200) NOT NULL,
    details          JSONB,
    logged_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Module 6's own tables (exact copy of migrations/002_module6_reports.py)

CREATE TABLE generated_reports (
    report_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_type         VARCHAR(50) NOT NULL
                         CHECK (report_type IN ('NAAC_CRITERIA_III', 'FACULTY_PROFILE')),
    department_code     VARCHAR(20) NOT NULL,
    academic_year       VARCHAR(9) NOT NULL,
    output_format       VARCHAR(10) NOT NULL DEFAULT 'pdf'
                         CHECK (output_format IN ('pdf', 'xlsx')),
    faculty_id          UUID,

    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                         CHECK (status IN ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED')),

    s3_uri              TEXT,
    checksum_sha256     VARCHAR(64),
    total_papers        INTEGER,
    error_message       TEXT,

    generated_by        VARCHAR(200) NOT NULL,
    requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    generated_at        TIMESTAMPTZ
);

CREATE TABLE report_checksums (
    checksum_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    report_id           UUID NOT NULL REFERENCES generated_reports(report_id) ON DELETE CASCADE,
    checksum_sha256     VARCHAR(64) NOT NULL,
    event_type          VARCHAR(20) NOT NULL DEFAULT 'GENERATED'
                         CHECK (event_type IN ('GENERATED', 'VERIFIED', 'MISMATCH')),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
