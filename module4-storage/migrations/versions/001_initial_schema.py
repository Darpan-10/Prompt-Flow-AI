"""001_initial_schema

Complete Module 4 schema:
- papers (partitioned by year)
- paper_versions (partitioned by year)
- validation_issues
- audit_log (partitioned by month, immutable)
- All indexes: B-tree, GIN, HNSW, BRIN
- RLS policies
- Versioning triggers
- Audit triggers
- Roles

Revision ID: 001
"""

from alembic import op
import sqlalchemy as sa


revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── Extensions ───────────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')  # pgvector

    # ── Roles ─────────────────────────────────────────────────────────────
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_writer') THEN
                CREATE ROLE audit_writer;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
                CREATE ROLE app_user;
            END IF;
        END
        $$;
    """)

    # ── papers (partitioned RANGE by created_at) ─────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            paper_id                   UUID         NOT NULL DEFAULT gen_random_uuid(),
            ingestion_idempotency_key  VARCHAR(64)  NOT NULL,
            extraction_id              UUID         NOT NULL,

            title                      TEXT         NOT NULL,
            authors                    JSONB        NOT NULL,
            venue                      VARCHAR(500),
            year                       INTEGER      NOT NULL
                                           CHECK (year >= 2000 AND year <= EXTRACT(YEAR FROM NOW()) + 1),
            doi                        VARCHAR(200),
            paper_type                 VARCHAR(50)  NOT NULL
                                           CHECK (paper_type IN ('journal','conference','thesis','book_chapter','unknown')),

            faculty_id                 UUID         NOT NULL,
            faculty_email              VARCHAR(200) NOT NULL,
            department_code            VARCHAR(20)  NOT NULL,

            status                     VARCHAR(20)  NOT NULL
                                           CHECK (status IN ('PUBLISHED','DRAFT','REJECTED','PENDING_REVIEW')),
            overall_confidence         NUMERIC(3,2) NOT NULL
                                           CHECK (overall_confidence >= 0.0 AND overall_confidence <= 1.0),

            raw_text_hash              CHAR(64)     NOT NULL,
            attachment_uris            JSONB        NOT NULL DEFAULT '[]'::jsonb,

            embedding                  vector(768),

            created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            CONSTRAINT papers_doi_unique UNIQUE (doi, department_code, created_at),
            PRIMARY KEY (paper_id, created_at)
        ) PARTITION BY RANGE (created_at);
    """)

    # Yearly partitions for papers
    for year, next_year in [("2023","2024"), ("2024","2025"), ("2025","2026"), ("2026","2027")]:
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS papers_y{year}
            PARTITION OF papers
            FOR VALUES FROM ('{year}-01-01') TO ('{next_year}-01-01');
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS papers_default
        PARTITION OF papers DEFAULT;
    """)

    # Unique constraint on idempotency key (applied on parent)
    # NOTE: created_at (the partition key) must be part of this unique
    # index -- PostgreSQL requires every unique constraint/index on a
    # partitioned table to include all partitioning columns. Found while
    # test-running this migration against a real PostgreSQL 16 instance
    # during the cross-module RLS security sweep (unrelated to RLS
    # itself, but blocked full end-to-end migration testing until fixed).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_idempotency
        ON papers (ingestion_idempotency_key, created_at);
    """)

    # ── B-tree indexes on papers ──────────────────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_doi
        ON papers (doi) WHERE doi IS NOT NULL;
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_dept_status
        ON papers (department_code, status);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_faculty
        ON papers (faculty_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_year
        ON papers (year);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_dashboard
        ON papers (department_code, status, created_at DESC)
        INCLUDE (title, overall_confidence, faculty_email);
    """)

    # -- GIN full-text search index ----------------------------------------
    # NOTE: the concatenated tsvector expression MUST be wrapped in an
    # extra pair of parens -- `gin(expr1 || expr2)` is a syntax error
    # ("syntax error at or near '||'"), Postgres's CREATE INDEX grammar
    # parses the USING gin(...) argument list as comma-separated
    # expressions and does not accept a bare top-level `||` there without
    # the outer parens making it unambiguously one expression. Found and
    # fixed while test-running this migration against real PostgreSQL 16
    # during the cross-module RLS security sweep (unrelated to RLS
    # itself, but blocked full end-to-end migration testing until fixed).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_fts
        ON papers USING gin(
            (setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(venue, '')), 'B'))
        )
        WHERE status = 'PUBLISHED';
    """)

    # ── HNSW semantic search index (pgvector) ─────────────────────────────
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_vec
        ON papers USING hnsw (embedding vector_cosine_ops)
        WHERE status = 'PUBLISHED' AND embedding IS NOT NULL;
    """)

    # ── paper_versions (partitioned RANGE by changed_at) ─────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_versions (
            version_id     UUID        NOT NULL DEFAULT gen_random_uuid(),
            paper_id       UUID        NOT NULL,
            version_number INTEGER     NOT NULL,

            changed_by     VARCHAR(100) NOT NULL,
            changed_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            change_reason  VARCHAR(500),

            before_state   JSONB,
            after_state    JSONB       NOT NULL,

            CONSTRAINT paper_versions_unique_version UNIQUE (paper_id, version_number, changed_at),
            PRIMARY KEY (version_id, changed_at)
        ) PARTITION BY RANGE (changed_at);
    """)

    for year, next_year in [("2023","2024"), ("2024","2025"), ("2025","2026"), ("2026","2027")]:
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS paper_versions_y{year}
            PARTITION OF paper_versions
            FOR VALUES FROM ('{year}-01-01') TO ('{next_year}-01-01');
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_versions_default
        PARTITION OF paper_versions DEFAULT;
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_paper_versions_paper_id
        ON paper_versions (paper_id);
    """)

    # ── validation_issues ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS validation_issues (
            issue_id        UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            paper_id        UUID        NOT NULL,

            issue_code      VARCHAR(50) NOT NULL,
            severity        VARCHAR(10) NOT NULL CHECK (severity IN ('error','warning','info')),
            action          VARCHAR(20) NOT NULL CHECK (action IN ('AUTO_SAVE','REVIEW_REQUIRED','BLOCK')),

            json_path       VARCHAR(200),
            extracted_value TEXT,
            confidence      NUMERIC(4,3),
            threshold       NUMERIC(4,3),
            source          VARCHAR(50) NOT NULL,
            message         TEXT        NOT NULL,

            resolved_at     TIMESTAMPTZ,
            resolved_by     VARCHAR(100),

            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_issues_paper_id
        ON validation_issues (paper_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_issues_severity
        ON validation_issues (severity);
    """)

    # ── audit_log (partitioned RANGE by logged_at, MONTHLY, IMMUTABLE) ───
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
            logged_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            action        VARCHAR(100) NOT NULL,
            actor_type    VARCHAR(50)  NOT NULL,
            actor_id      VARCHAR(200),
            resource_type VARCHAR(50)  NOT NULL,
            resource_id   VARCHAR(200),

            details       JSONB,
            ip_address    VARCHAR(45),
            trace_id      UUID,

            PRIMARY KEY (log_id, logged_at)
        ) PARTITION BY RANGE (logged_at);
    """)

    # Monthly partitions for audit_log (2024 + 2025 + 2026)
    months = [
        ("2024","01","02"),("2024","02","03"),("2024","03","04"),
        ("2024","04","05"),("2024","05","06"),("2024","06","07"),
        ("2024","07","08"),("2024","08","09"),("2024","09","10"),
        ("2024","10","11"),("2024","11","12"),("2024","12","2025-01"),
        ("2025","01","02"),("2025","02","03"),("2025","03","04"),
        ("2025","04","05"),("2025","05","06"),("2025","06","07"),
        ("2025","07","08"),("2025","08","09"),("2025","09","10"),
        ("2025","10","11"),("2025","11","12"),("2025","12","2026-01"),
        ("2026","01","02"),("2026","02","03"),("2026","03","04"),
        ("2026","04","05"),("2026","05","06"),("2026","06","07"),
        ("2026","07","08"),("2026","08","09"),("2026","09","10"),
        ("2026","10","11"),("2026","11","12"),("2026","12","2027-01"),
    ]
    for year, m_start, m_end in months:
        end_year = year if "-" not in m_end else m_end.split("-")[0]
        end_m    = m_end if "-" not in m_end else m_end.split("-")[1]
        from_dt  = f"{year}-{m_start}-01"
        to_dt    = f"{end_year}-{end_m}-01"
        tbl      = f"audit_log_y{year}_m{m_start}"
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS {tbl}
            PARTITION OF audit_log
            FOR VALUES FROM ('{from_dt}') TO ('{to_dt}');
        """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log_default
        PARTITION OF audit_log DEFAULT;
    """)

    # BRIN time-series index on audit_log
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_time
        ON audit_log USING brin (logged_at);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_resource
        ON audit_log (resource_type, resource_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_actor
        ON audit_log (actor_id);
    """)

    # ── Audit immutability ────────────────────────────────────────────────
    op.execute("GRANT INSERT ON audit_log TO audit_writer;")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC;")

    # ── RLS: Enable ───────────────────────────────────────────────────────
    op.execute("ALTER TABLE papers ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE paper_versions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE validation_issues ENABLE ROW LEVEL SECURITY;")

    # CRITICAL FIX (found via a full cross-module RLS security sweep,
    # verified empirically against real PostgreSQL 16): PostgreSQL RLS
    # policies do NOT apply to a table's OWNER by default -- only to
    # other roles. docker-compose.yml's POSTGRES_USER (and the RDS
    # master user in Terraform) is "promptflow", which is the SAME role
    # that runs `alembic upgrade head` (and therefore CREATEs, and
    # therefore OWNS, every table here) AND the same role the
    # application's DATABASE_URL connects as. Without FORCE ROW LEVEL
    # SECURITY, every policy above is silently a no-op for the
    # application's actual database connection -- confirmed empirically:
    # connecting as the table owner with a WRONG department in
    # app.current_department still returned every row, regardless of
    # how correctly the policies themselves were written. This single
    # missing statement would have made every other RLS fix in this
    # migration moot in the real deployed system.
    op.execute("ALTER TABLE papers FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE paper_versions FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE validation_issues FORCE ROW LEVEL SECURITY;")

    # -- RLS: papers (single policy -- see CRITICAL note below) -----------
    #
    # CRITICAL FIX (found via a full cross-module RLS security sweep,
    # verified empirically against real PostgreSQL 16 -- see
    # CRITICAL_PATCH_NOTES.md): this used to be TWO separate PERMISSIVE
    # policies (dept_isolation_papers + faculty_draft_access).
    #
    # PostgreSQL combines multiple PERMISSIVE policies on the same table
    # with OR. The old faculty_draft_access policy's second clause --
    # `status IN ('PUBLISHED', 'PENDING_REVIEW', 'REJECTED')` -- had NO
    # department qualifier. Being OR'd in as a separate permissive
    # policy, this silently granted every authenticated non-admin user
    # visibility into every OTHER department's PUBLISHED/PENDING_REVIEW/
    # REJECTED papers, completely bypassing department isolation for
    # every paper except DRAFTs. Confirmed with a live test: a
    # correctly-scoped CSE coordinator (app.current_department='CSE',
    # role='coordinator', legitimate non-admin session) could SELECT an
    # ECE department's PUBLISHED paper.
    #
    # This single policy fixes it by making department membership a hard
    # AND-ed requirement for everyone except admin, with the
    # status-based "everyone in my department can see published papers"
    # and "I can see my own drafts" exceptions correctly NESTED INSIDE
    # that department check rather than OR'd in globally by a second
    # policy.
    op.execute("""
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
    """)

    # ── RLS: paper_versions (via papers join) ─────────────────────────────
    op.execute("""
        CREATE POLICY dept_isolation_versions ON paper_versions
        USING (
            EXISTS (
                SELECT 1 FROM papers p
                WHERE p.paper_id = paper_versions.paper_id
                AND (
                    p.department_code = current_setting('app.current_department', true)
                    OR current_setting('app.current_role', true) = 'admin'
                )
            )
        );
    """)

    # ── RLS: validation_issues (via papers join) ──────────────────────────
    op.execute("""
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
    """)

    # ── Trigger: initial version on INSERT ────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION create_initial_paper_version()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO paper_versions (
                paper_id, version_number, changed_by,
                change_reason, before_state, after_state, changed_at
            ) VALUES (
                NEW.paper_id,
                1,
                COALESCE(current_setting('app.current_user_id', true), 'system'),
                'Initial creation',
                NULL,
                to_jsonb(NEW),
                NOW()
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)

    op.execute("""
        CREATE TRIGGER trg_paper_initial_version
        AFTER INSERT ON papers
        FOR EACH ROW
        EXECUTE FUNCTION create_initial_paper_version();
    """)

    # ── Trigger: new version on UPDATE ───────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION create_paper_version()
        RETURNS TRIGGER AS $$
        DECLARE
            max_version INTEGER;
        BEGIN
            SELECT COALESCE(MAX(version_number), 1) + 1
            INTO max_version
            FROM paper_versions
            WHERE paper_id = NEW.paper_id;

            INSERT INTO paper_versions (
                paper_id, version_number, changed_by,
                change_reason, before_state, after_state, changed_at
            ) VALUES (
                NEW.paper_id,
                max_version,
                COALESCE(current_setting('app.current_user_id', true), 'system'),
                COALESCE(current_setting('app.change_reason', true), 'update'),
                to_jsonb(OLD),
                to_jsonb(NEW),
                NOW()
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)

    op.execute("""
        CREATE TRIGGER trg_paper_versioning
        AFTER UPDATE ON papers
        FOR EACH ROW
        EXECUTE FUNCTION create_paper_version();
    """)

    # ── Trigger: audit_log on INSERT / UPDATE / DELETE ───────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION log_paper_audit()
        RETURNS TRIGGER AS $$
        DECLARE
            _trace UUID;
        BEGIN
            BEGIN
                _trace := current_setting('app.trace_id', true)::UUID;
            EXCEPTION WHEN OTHERS THEN
                _trace := gen_random_uuid();
            END;

            INSERT INTO audit_log (
                action, actor_type, actor_id,
                resource_type, resource_id,
                details, ip_address, trace_id, logged_at
            ) VALUES (
                CASE
                    WHEN TG_OP = 'INSERT' THEN 'paper_created'
                    WHEN TG_OP = 'UPDATE' THEN 'paper_updated'
                    WHEN TG_OP = 'DELETE' THEN 'paper_deleted'
                END,
                COALESCE(current_setting('app.current_actor_type', true), 'system'),
                COALESCE(current_setting('app.current_user_id',   true), 'system'),
                'paper',
                COALESCE(NEW.paper_id::text, OLD.paper_id::text),
                jsonb_build_object(
                    'title',           COALESCE(NEW.title,           OLD.title),
                    'status',          COALESCE(NEW.status,          OLD.status),
                    'department_code', COALESCE(NEW.department_code, OLD.department_code),
                    'overall_confidence', COALESCE(NEW.overall_confidence, OLD.overall_confidence)
                ),
                NULL,
                _trace,
                NOW()
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)

    op.execute("""
        CREATE TRIGGER trg_paper_audit
        AFTER INSERT OR UPDATE OR DELETE ON papers
        FOR EACH ROW
        EXECUTE FUNCTION log_paper_audit();
    """)

    # ── auto-update updated_at ────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_papers_updated_at
        BEFORE UPDATE ON papers
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_papers_updated_at ON papers;")
    op.execute("DROP TRIGGER IF EXISTS trg_paper_audit ON papers;")
    op.execute("DROP TRIGGER IF EXISTS trg_paper_versioning ON papers;")
    op.execute("DROP TRIGGER IF EXISTS trg_paper_initial_version ON papers;")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at();")
    op.execute("DROP FUNCTION IF EXISTS log_paper_audit();")
    op.execute("DROP FUNCTION IF EXISTS create_paper_version();")
    op.execute("DROP FUNCTION IF EXISTS create_initial_paper_version();")
    op.execute("DROP TABLE IF EXISTS validation_issues CASCADE;")
    op.execute("DROP TABLE IF EXISTS paper_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS papers CASCADE;")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE;")
    op.execute("DROP EXTENSION IF EXISTS vector;")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
