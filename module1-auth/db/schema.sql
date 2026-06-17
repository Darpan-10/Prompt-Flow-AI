-- Module 1: Auth & Access Control Database Schema
-- PostgreSQL 15+

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Users table
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(100) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255),
    role VARCHAR(20) NOT NULL DEFAULT 'faculty'
        CHECK (role IN ('faculty', 'coordinator', 'hod', 'admin', 'system_worker')),
    department_code VARCHAR(10),
    is_active BOOLEAN DEFAULT TRUE,
    mfa_enabled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Audit log table (partitioned)
CREATE TABLE IF NOT EXISTS audit_log (
    log_id UUID DEFAULT uuid_generate_v4(),
    action VARCHAR(50) NOT NULL,
    actor_type VARCHAR(20) NOT NULL
        CHECK (actor_type IN ('user', 'system', 'm2m')),
    actor_id VARCHAR(100),
    resource_id VARCHAR(100),
    details JSONB,
    ip_address INET,
    trace_id UUID,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (log_id, logged_at)
) PARTITION BY RANGE (logged_at);

-- Create yearly partitions
CREATE TABLE IF NOT EXISTS audit_log_y2024 PARTITION OF audit_log
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');

CREATE TABLE IF NOT EXISTS audit_log_y2025 PARTITION OF audit_log
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');

CREATE TABLE IF NOT EXISTS audit_log_y2026 PARTITION OF audit_log
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

-- Performance indices
CREATE INDEX IF NOT EXISTS idx_audit_log_time 
    ON audit_log USING BRIN(logged_at);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor 
    ON audit_log(actor_id, logged_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_action 
    ON audit_log(action, logged_at DESC);

-- Role (safe create)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_writer') THEN
        CREATE ROLE audit_writer;
    END IF;
END
$$;

-- Permissions
GRANT INSERT ON audit_log TO audit_writer;
GRANT INSERT ON audit_log_y2024, audit_log_y2025, audit_log_y2026 TO audit_writer;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC, audit_writer;

-- Indices for users
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_department ON users(department_code);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = TRUE;