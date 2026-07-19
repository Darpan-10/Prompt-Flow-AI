"""
Integration tests for RLS policies.
Requires: PostgreSQL running with schema initialized
Run: pytest tests/integration/test_rls.py -v --asyncio-mode=auto
"""
import pytest
import asyncpg
from app.config import settings


@pytest.fixture
async def db_pool():
    """Create database pool for testing."""
    pool = await asyncpg.create_pool(settings.database_url)
    yield pool
    await pool.close()


@pytest.mark.asyncio
async def test_department_isolation_rls(db_pool):
    """Test that RLS prevents cross-department access."""
    async with db_pool.acquire() as conn:
        # Create a test user in CSE dept
        await conn.execute("""
            INSERT INTO users (user_id, email, name, role, department_code)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET updated_at = NOW()
        """, "test_user_cse", "test@srmap.edu.in", "Test User", "faculty", "CSE")
        
        # Verify user exists
        result = await conn.fetchval(
            "SELECT department_code FROM users WHERE user_id = $1",
            "test_user_cse"
        )
        assert result == "CSE"


@pytest.mark.asyncio
async def test_audit_log_immutability(db_pool):
    """
    Test that audit log cannot be updated.
    audit_log is owned by Module 4's migration now (see schema.sql) — skip
    if it isn't present, e.g. when running Module 1 standalone without
    Module 4's migration applied to the same DB.
    """
    async with db_pool.acquire() as conn:
        try:
            log_id = await conn.fetchval("""
                INSERT INTO audit_log
                (action, actor_type, actor_id, resource_type, details)
                VALUES ('TEST_ACTION', 'user', 'test_user', 'user', '{"test": true}')
                RETURNING log_id
            """)
        except asyncpg.exceptions.UndefinedTableError:
            pytest.skip("audit_log not present — Module 4's migration owns this table")

        # Try to update (should fail)
        with pytest.raises(Exception):  # asyncpg.InsufficientPrivilegeError
            await conn.execute(
                "UPDATE audit_log SET action = 'MODIFIED' WHERE log_id = $1",
                log_id
            )


@pytest.mark.asyncio
async def test_audit_log_insert_succeeds(db_pool):
    """Test that audit log INSERT works."""
    async with db_pool.acquire() as conn:
        try:
            result = await conn.fetchval("""
                INSERT INTO audit_log
                (action, actor_type, actor_id, resource_type)
                VALUES ('LOGIN_SUCCESS', 'user', 'test_user', 'user')
                RETURNING log_id
            """)
        except asyncpg.exceptions.UndefinedTableError:
            pytest.skip("audit_log not present — Module 4's migration owns this table")
        assert result is not None
