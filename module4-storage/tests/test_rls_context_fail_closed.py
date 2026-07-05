"""
Module 4 – Regression Test: rls_context() Fail-Closed Behavior

ADDED after a critical vulnerability was found during a full cross-module
RLS security sweep: every data-touching route in this API used to
default to FULL ADMIN ACCESS (bypassing RLS entirely) whenever the
X-Department-Code / X-Role / X-User-Id headers were simply absent --
which is the default for any request that doesn't explicitly set them.

This test proves the fix: missing/invalid headers must be rejected with
401, and the local-dev escape hatch must be OFF by default.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.main import rls_context


def make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ASGI Request with the given headers, no body."""
    raw_headers = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


class FakeSession:
    """Stand-in for AsyncSession -- rls_context() only calls
    set_rls_context()/set_admin_context() on it, both of which are
    mocked via monkeypatch in these tests, so a real session isn't
    needed to test the header-validation logic in isolation."""
    pass


class TestRlsContextFailsClosedByDefault:
    @pytest.mark.asyncio
    async def test_missing_all_headers_raises_401_by_default(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", False)
        request = make_request(headers={})
        with pytest.raises(HTTPException) as exc_info:
            await rls_context(request, db=FakeSession())
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_department_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", False)
        request = make_request(headers={"X-Role": "coordinator", "X-User-Id": "u1"})
        with pytest.raises(HTTPException) as exc_info:
            await rls_context(request, db=FakeSession())
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_role_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", False)
        request = make_request(headers={"X-Department-Code": "CSE", "X-User-Id": "u1"})
        with pytest.raises(HTTPException) as exc_info:
            await rls_context(request, db=FakeSession())
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_user_id_header_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", False)
        request = make_request(headers={"X-Department-Code": "CSE", "X-Role": "coordinator"})
        with pytest.raises(HTTPException) as exc_info:
            await rls_context(request, db=FakeSession())
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_role_value_raises_401(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", False)
        request = make_request(headers={
            "X-Department-Code": "CSE", "X-Role": "superuser_typo", "X-User-Id": "u1",
        })
        with pytest.raises(HTTPException) as exc_info:
            await rls_context(request, db=FakeSession())
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_headers_no_longer_grants_admin_context(self, monkeypatch):
        """
        THE core regression guard. The old vulnerable code path would
        have called set_admin_context() here. Confirm it's never called
        for a request with no headers when ALLOW_MISSING_AUTH_HEADERS is
        false (the required-false-in-production default).
        """
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", False)
        admin_context_called = False

        async def spy_set_admin_context(db):
            nonlocal admin_context_called
            admin_context_called = True

        monkeypatch.setattr("app.main.set_admin_context", spy_set_admin_context)

        request = make_request(headers={})
        with pytest.raises(HTTPException):
            await rls_context(request, db=FakeSession())

        assert not admin_context_called, (
            "set_admin_context() must NEVER be called for a request with "
            "missing auth headers when ALLOW_MISSING_AUTH_HEADERS=false."
        )


class TestRlsContextValidHeadersStillWork:
    @pytest.mark.asyncio
    async def test_valid_non_admin_headers_call_set_rls_context(self, monkeypatch):
        calls = []

        async def spy_set_rls_context(db, department_code, role, user_id, actor_type):
            calls.append((department_code, role, user_id, actor_type))

        monkeypatch.setattr("app.main.set_rls_context", spy_set_rls_context)

        request = make_request(headers={
            "X-Department-Code": "CSE", "X-Role": "coordinator", "X-User-Id": "u1",
        })
        result = await rls_context(request, db="fake-db-sentinel")

        assert result == "fake-db-sentinel"
        assert calls == [("CSE", "coordinator", "u1", "user")]

    @pytest.mark.asyncio
    async def test_valid_admin_role_calls_set_admin_context(self, monkeypatch):
        calls = []

        async def spy_set_admin_context(db):
            calls.append(db)

        monkeypatch.setattr("app.main.set_admin_context", spy_set_admin_context)

        request = make_request(headers={
            "X-Department-Code": "CSE", "X-Role": "admin", "X-User-Id": "admin-1",
        })
        await rls_context(request, db="fake-db-sentinel")

        assert calls == ["fake-db-sentinel"]


class TestAllowMissingAuthHeadersEscapeHatch:
    """The local-dev-only opt-in still works when explicitly enabled --
    but must remain OFF by default (covered above)."""

    @pytest.mark.asyncio
    async def test_escape_hatch_when_explicitly_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_MISSING_AUTH_HEADERS", True)
        calls = []

        async def spy_set_rls_context(db, department_code, role, user_id, actor_type):
            calls.append((department_code, role, user_id))

        monkeypatch.setattr("app.main.set_rls_context", spy_set_rls_context)

        request = make_request(headers={})
        result = await rls_context(request, db="fake-db-sentinel")

        assert result == "fake-db-sentinel"
        assert len(calls) == 1

    def test_setting_defaults_to_false(self):
        """Config-level regression guard: the escape hatch's default
        value itself must be False, independent of any test monkeypatching."""
        from app.config import Settings
        fresh_settings = Settings(_env_file=None)
        assert fresh_settings.ALLOW_MISSING_AUTH_HEADERS is False
