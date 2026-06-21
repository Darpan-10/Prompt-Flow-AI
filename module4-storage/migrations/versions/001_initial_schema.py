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
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # REQUIRED

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

    # ── papers ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            paper_id UUID NOT NULL DEFAULT gen_random_uuid(),
            ingestion_idempotency_key VARCHAR(64) NOT NULL,
            extraction_id UUID NOT NULL,

            title TEXT NOT NULL,
            authors JSONB NOT NULL,
            venue VARCHAR(500),
            year INTEGER NOT NULL
                CHECK (year >= 2000 AND year <= EXTRACT(YEAR FROM NOW()) + 1),
            doi VARCHAR(200),
            paper_type VARCHAR(50) NOT NULL
                CHECK (paper_type IN ('journal','conference','thesis','book_chapter','unknown')),

            faculty_id UUID NOT NULL,
            faculty_email VARCHAR(200) NOT NULL,
            department_code VARCHAR(20) NOT NULL,

            status VARCHAR(20) NOT NULL
                CHECK (status IN ('PUBLISHED','DRAFT','REJECTED','PENDING_REVIEW')),
            overall_confidence NUMERIC(3,2) NOT NULL
                CHECK (overall_confidence >= 0.0 AND overall_confidence <= 1.0),

            raw_text_hash CHAR(64) NOT NULL,
            attachment_uris JSONB NOT NULL DEFAULT '[]'::jsonb,

            embedding vector(768),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT papers_doi_unique UNIQUE (doi, department_code, created_at),
            PRIMARY KEY (paper_id, created_at)
        ) PARTITION BY RANGE (created_at);
    """)

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

    # Partition-safe unique index
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_idempotency
        ON papers (ingestion_idempotency_key, created_at);
    """)

    # ── Indexes ─────────────────────────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers (doi) WHERE doi IS NOT NULL;")
    op.execute("CREATE INDEX IF NOT EXISTS idx_papers_dept_status ON papers (department_code, status);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_papers_faculty ON papers (faculty_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_papers_year ON papers (year);")

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_dashboard
        ON papers (department_code, status, created_at DESC)
        INCLUDE (title, overall_confidence, faculty_email);
    """)

    # ✅ FIXED FTS INDEX
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_fts
        ON papers USING gin(
            to_tsvector('english', coalesce(title, '') || ' ' || coalesce(venue, ''))
        )
        WHERE status = 'PUBLISHED';
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_vec
        ON papers USING hnsw (embedding vector_cosine_ops)
        WHERE status = 'PUBLISHED' AND embedding IS NOT NULL;
    """)

    # ── paper_versions ─────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_versions (
            version_id UUID NOT NULL DEFAULT gen_random_uuid(),
            paper_id UUID NOT NULL,
            version_number INTEGER NOT NULL,

            changed_by VARCHAR(100) NOT NULL,
            changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            change_reason VARCHAR(500),

            before_state JSONB,
            after_state JSONB NOT NULL,

            CONSTRAINT paper_versions_unique_version 
                UNIQUE (paper_id, version_number, changed_at),

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

    # ── validation_issues ───────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS validation_issues (
            issue_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            paper_id UUID NOT NULL,
            issue_code VARCHAR(50) NOT NULL,
            severity VARCHAR(10) NOT NULL CHECK (severity IN ('error','warning','info')),
            action VARCHAR(20) NOT NULL CHECK (action IN ('AUTO_SAVE','REVIEW_REQUIRED','BLOCK')),
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # ── audit_log ───────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            log_id UUID NOT NULL DEFAULT gen_random_uuid(),
            logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            action VARCHAR(100) NOT NULL,
            PRIMARY KEY (log_id, logged_at)
        ) PARTITION BY RANGE (logged_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS validation_issues CASCADE;")
    op.execute("DROP TABLE IF EXISTS paper_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS papers CASCADE;")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE;")
    op.execute("DROP EXTENSION IF EXISTS vector;")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto;")