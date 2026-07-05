"""
Module 6 – Main Application Entrypoint
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import dispose_engine
from app.routes import health, reports

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Module 6 Report Generator service starting...")
    log.info("S3 reports bucket: %s", settings.S3_REPORTS_BUCKET)
    yield
    log.info("Module 6 Report Generator service shutting down...")
    await dispose_engine()
    log.info("Shutdown complete.")


app = FastAPI(
    title="PromptFlow AI — Module 6: NAAC Report Generator",
    description=(
        "Deterministic, tamper-proof NAAC compliance report generation. "
        "NO LLM calls -- every report is rendered from verified PostgreSQL "
        "data via Jinja2 + WeasyPrint/openpyxl, SHA-256 checksummed, and "
        "stored in S3. Generation runs as a background task; this API "
        "only returns status and pre-signed download URLs."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(reports.router)
