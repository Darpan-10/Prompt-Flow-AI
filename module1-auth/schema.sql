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
faculty_id      UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
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
-- AUDIT LOG TABLE — OWNED BY MODULE 4
-- ============================================================
-- Module 1 does NOT create audit_log. It is a single shared table
-- owned exclusively by Module 4's Alembic migration
-- (module4-storage/migrations/versions/001_initial_schema.py),
-- which partitions it MONTHLY and includes a NOT NULL
-- resource_type column that Module 1's old copy of this table
-- never had. Two independent CREATE TABLE definitions for the
-- same shared table is exactly the conflict that broke
-- cross-module audit writes — so Module 1 only ever INSERTs into
-- it (see app/services/audit.py) and never defines its schema.
--
-- In a full integrated run, Module 1 must point DATABASE_URL at
-- the same Postgres instance as Module 4 (see docker-compose.yml)
-- so this table already exists by the time Module 1 starts.

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

-- ============================================================
-- SERVICE ACCOUNTS
-- ============================================================
CREATE TABLE IF NOT EXISTS service_accounts (
client_id          VARCHAR(100) PRIMARY KEY,
client_name        VARCHAR(255) NOT NULL,
description         TEXT,
-- PBKDF2-HMAC-SHA256, format "<hex salt>$<hex hash>" — see
-- app/services/service_accounts.py. Only consulted in non-production
-- environments (see verify_m2m_client in app/services/cognito.py);
-- production M2M auth goes through Cognito as before.
client_secret_hash VARCHAR(200),
is_active           BOOLEAN NOT NULL DEFAULT TRUE,
created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
