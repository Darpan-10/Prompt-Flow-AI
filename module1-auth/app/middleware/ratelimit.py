"""
Redis sliding-window rate limiter.
Limits: 5 auth attempts per IP per 60 seconds.
"""
import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings
from app import state

AUTH_PATHS = {"/auth/login", "/auth/callback", "/auth/m2m/token"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only rate-limit auth endpoints
        if request.url.path not in AUTH_PATHS:
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{ip}:{request.url.path}"
        now = time.time()
        window_start = now - settings.rate_limit_window_seconds

        pipe = state.redis_client.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, settings.rate_limit_window_seconds)
        results = await pipe.execute()

        attempt_count = results[2]

        if attempt_count > settings.rate_limit_attempts:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "too_many_attempts",
                    "detail": f"Rate limit exceeded. Max {settings.rate_limit_attempts} requests per {settings.rate_limit_window_seconds}s.",
                },
                headers={"Retry-After": str(settings.rate_limit_window_seconds)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, settings.rate_limit_attempts - attempt_count)
        )
        return response
