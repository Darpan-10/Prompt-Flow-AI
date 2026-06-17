"""
Pytest configuration and shared fixtures.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from app import state
import asyncpg
import redis.asyncio as redis
from app.config import settings


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def mock_db_redis():
    """Mock DB pool and Redis for unit tests."""
    # Mock asyncpg pool
    state.db_pool = AsyncMock(spec=asyncpg.Pool)
    state.db_pool.acquire = AsyncMock()
    
    # Mock redis client
    state.redis_client = AsyncMock(spec=redis.Redis)
    state.redis_client.exists = AsyncMock(return_value=False)
    state.redis_client.setex = AsyncMock()
    state.redis_client.delete = AsyncMock()
    
    yield
    
    # Cleanup
    state.db_pool = None
    state.redis_client = None


@pytest.fixture
def sample_jwt_claims():
    """Sample JWT claims for testing."""
    return {
        "sub": "user_8821",
        "name": "Dr. Jane Smith",
        "email": "jane@srmap.edu.in",
        "role": "faculty",
        "department_code": "CSE",
        "permissions": ["paper.view.self", "paper.submit", "search.dept"],
        "iss": "https://auth.promptflow.ai",
        "exp": 1719000000,
        "iat": 1718999100,
        "auth_type": "user",
        "trace_id": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01",
    }


@pytest.fixture
def sample_admin_claims():
    """Sample admin JWT claims for testing."""
    return {
        "sub": "admin_001",
        "name": "System Admin",
        "email": "admin@srmap.edu.in",
        "role": "admin",
        "department_code": None,
        "permissions": ["system.config", "user.delete", "export.global"],
        "iss": "https://auth.promptflow.ai",
        "exp": 1719000000,
        "iat": 1718999100,
        "auth_type": "user",
        "trace_id": "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01",
    }
