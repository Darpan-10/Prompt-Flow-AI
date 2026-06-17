-- ============================================================
-- Prompt Flow AI — Module 1: Database Schema (FINAL)
-- PostgreSQL 15+
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- USERS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
user_id         VARCHAR(100) PRIMARY KEY,
email           VARCHAR(255) NOT NULL UNIQUE,
name            VARCHAR(255),
role            VARCHAR(20)  NOT NULL DEFAULT 'faculty'
CHECK (role IN ('faculty','coordinator','hod','admin','system_worker')),
department_code VARCHAR(20),
is_active       BOOLEAN NOT NULL DEFAULT TRUE,
mfa_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
last_login_at   TIMESTAMPTZ,
created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email      ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_department ON users(department_code);
CREATE INDEX IF NOT EXISTS idx_users_role       ON users(role);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
NEW.updated_at = NOW();
RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS users_updated_at ON users;
CREATE TRIGGER users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- AUDIT LOG TABLE (IMMUTABLE, PARTITIONED)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
log_id      UUID DEFAULT gen_random_uuid(),
action      VARCHAR(50)  NOT NULL,
actor_type  VARCHAR(20)  NOT NULL
CHECK (actor_type IN ('user', 'system', 'm2m')),
actor_id    VARCHAR(100),
resource_id VARCHAR(100),
details     JSONB,
ip_address  INET,
trace_id    UUID,
logged_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
PRIMARY KEY (log_id, logged_at)
) PARTITION BY RANGE (logged_at);

-- Partitions (7-year retention)
CREATE TABLE IF NOT EXISTS audit_log_y2024 PARTITION OF audit_log
FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2025 PARTITION OF audit_log
FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2026 PARTITION OF audit_log
FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2027 PARTITION OF audit_log
FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2028 PARTITION OF audit_log
FOR VALUES FROM ('2028-01-01') TO ('2029-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2029 PARTITION OF audit_log
FOR VALUES FROM ('2029-01-01') TO ('2030-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2030 PARTITION OF audit_log
FOR VALUES FROM ('2030-01-01') TO ('2031-01-01');
CREATE TABLE IF NOT EXISTS audit_log_y2031 PARTITION OF audit_log
FOR VALUES FROM ('2031-01-01') TO ('2032-01-01');

-- Indexes
CREATE INDEX IF NOT EXISTS idx_audit_log_time
ON audit_log USING brin(logged_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor
ON audit_log(actor_id, logged_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action
ON audit_log(action, logged_at);

-- ============================================================
-- IMMUTABILITY (HARD ENFORCEMENT)
-- ============================================================
CREATE OR REPLACE FUNCTION prevent_audit_log_modifications()
RETURNS trigger AS $$
BEGIN
RAISE EXCEPTION 'audit_log is immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update
BEFORE UPDATE ON audit_log
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_modifications();

DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;
CREATE TRIGGER audit_log_no_delete
BEFORE DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_modifications();

-- ============================================================
-- ROLE PERMISSIONS
-- ============================================================
DO $$
BEGIN
IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_writer') THEN
CREATE ROLE audit_writer;
END IF;
END$$;

GRANT INSERT ON audit_log TO audit_writer;
GRANT INSERT ON ALL TABLES IN SCHEMA public TO audit_writer;

REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC, audit_writer;

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;

CREATE POLICY users_dept_isolation ON users
USING (
department_code = current_setting('app.current_department', TRUE)
OR current_setting('app.current_role', TRUE) = 'admin'
);

CREATE POLICY users_self_access ON users
USING (user_id = current_setting('app.current_user_id', TRUE));

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;

CREATE POLICY audit_readonly ON audit_log
FOR SELECT
USING (
current_setting('app.current_role', TRUE) IN ('admin','hod','auditor')
);

CREATE POLICY audit_insert ON audit_log
FOR INSERT
WITH CHECK (TRUE);

-- ============================================================
-- SERVICE ACCOUNTS
-- ============================================================
CREATE TABLE IF NOT EXISTS service_accounts (
client_id     VARCHAR(100) PRIMARY KEY,
client_name   VARCHAR(255) NOT NULL,
description   TEXT,
is_active     BOOLEAN NOT NULL DEFAULT TRUE,
created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- DEPARTMENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS departments (
department_code VARCHAR(20) PRIMARY KEY,
department_name VARCHAR(255) NOT NULL,
is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO departments (department_code, department_name) VALUES
('CSE','Computer Science & Engineering'),
('MECH','Mechanical Engineering'),
('ECE','Electronics & Communication Engineering'),
('CIVIL','Civil Engineering'),
('EEE','Electrical & Electronics Engineering'),
('IT','Information Technology'),
('MBA','Master of Business Administration'),
('MCA','Master of Computer Applications')
ON CONFLICT DO NOTHING;



-- ============================================================
-- END
-- ============================================================
