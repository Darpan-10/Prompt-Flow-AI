"""002_module6_reports

Adds Module 6's two new tables to the SAME shared database Module 4
already manages (per the locked architecture: Module 4 owns `papers`,
`paper_versions`, `validation_issues`, `audit_log`; Module 6 extends the
same schema with `generated_reports` and `report_checksums`, and writes
audit entries into Module 4's existing `audit_log` table -- it does NOT
create a parallel audit table).

This migration is chained onto Module 4's head revision ("001"). To run
it: copy this file into Module 4's migrations/versions/ directory
alongside 001_initial_schema.py, then run `alembic upgrade head` from
Module 4's project (which already has the working alembic.ini /
migrations/env.py pointed at the shared database). See
migrations/README.md for the exact step-by-step -- this is intentional,
not a missing piece: Module 6 does not maintain its own independent
Alembic environment, because that would risk two separate
`alembic_version` tracking rows against the same physical database,
which is a known footgun.

Revision ID: 002_module6_reports
"""

from alembic import op
import sqlalchemy as sa


revision = "002_module6_reports"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # -- generated_reports ----------------------------------------------
    # One row per report generation REQUEST (not per file) -- status
    # tracks the background task lifecycle: PENDING -> GENERATING ->
    # COMPLETED | FAILED. s3_uri/checksum_sha256/total_papers are NULL
    # until the background task reaches COMPLETED.
    op.execute("""
        CREATE TABLE IF NOT EXISTS generated_reports (
            report_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            report_type         VARCHAR(50) NOT NULL
                                 CHECK (report_type IN ('NAAC_CRITERIA_III', 'FACULTY_PROFILE')),
            department_code     VARCHAR(20) NOT NULL,
            academic_year       VARCHAR(9) NOT NULL,
            output_format       VARCHAR(10) NOT NULL DEFAULT 'pdf'
                                 CHECK (output_format IN ('pdf', 'xlsx')),
            faculty_id          UUID,  -- only populated for FACULTY_PROFILE

            status              VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN ('PENDING', 'GENERATING', 'COMPLETED', 'FAILED')),

            s3_uri              TEXT,
            checksum_sha256     VARCHAR(64),
            total_papers        INTEGER,
            error_message       TEXT,

            generated_by        VARCHAR(200) NOT NULL,  -- user_id from JWT
            requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            generated_at        TIMESTAMPTZ  -- set when status becomes COMPLETED
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_generated_reports_dept_year
        ON generated_reports (department_code, academic_year)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_generated_reports_status
        ON generated_reports (status)
        WHERE status IN ('PENDING', 'GENERATING')
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_generated_reports_generated_by
        ON generated_reports (generated_by)
    """)

    # -- report_checksums -------------------------------------------------
    # Audit trail of checksum events for a report: the INITIAL checksum
    # computed at generation time, plus any LATER re-verification events
    # (e.g. re-downloading from S3 and re-hashing to confirm no tampering
    # -- ChecksumService.verify_checksum() is built for this, even though
    # no public endpoint exposes it yet per the locked output requirements
    # -- see SETUP.md for the suggested follow-up endpoint).
    op.execute("""
        CREATE TABLE IF NOT EXISTS report_checksums (
            checksum_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            report_id           UUID NOT NULL REFERENCES generated_reports(report_id) ON DELETE CASCADE,
            checksum_sha256     VARCHAR(64) NOT NULL,
            event_type          VARCHAR(20) NOT NULL DEFAULT 'GENERATED'
                                 CHECK (event_type IN ('GENERATED', 'VERIFIED', 'MISMATCH')),
            recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_report_checksums_report_id
        ON report_checksums (report_id)
    """)

    # Immutability: same pattern as Module 4's audit_log -- nobody should
    # ever UPDATE or DELETE a checksum record after the fact. If a report
    # needs to be regenerated, generate a NEW report_id rather than
    # mutating history.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
                REVOKE UPDATE, DELETE ON report_checksums FROM PUBLIC;
                GRANT SELECT, INSERT ON report_checksums TO audit_writer;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS report_checksums")
    op.execute("DROP TABLE IF EXISTS generated_reports")
