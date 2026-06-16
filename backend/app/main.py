"""
FastAPI application entry point.
"""
from __future__ import annotations

import structlog
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import engine, Base
from app.api import companies, financials, analytics, exports, jobs
from app.worker import refresh_market_caps, refresh_materialized_views

logger = structlog.get_logger(__name__)

# In-process scheduler — replaces Celery beat. Runs inside the same FastAPI
# process, so no extra worker/beat/Flower/Redis containers are needed.
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("startup", app=settings.app_name, version=settings.app_version)
    # Create tables (Alembic handles migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    scheduler.add_job(
        refresh_market_caps, CronTrigger(hour=8, minute=0),
        id="refresh_market_caps", replace_existing=True,
    )
    scheduler.add_job(
        refresh_materialized_views, CronTrigger(hour=6, minute=0),
        id="refresh_materialized_views", replace_existing=True,
    )
    scheduler.start()

    yield

    scheduler.shutdown(wait=False)
    await engine.dispose()
    logger.info("shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Production-grade Indian public company financial analysis platform. "
        "Ingests, standardizes, and benchmarks multi-year financial statements "
        "for NSE/BSE listed companies."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(companies.router,  prefix="/api/v1/companies",  tags=["Companies"])
app.include_router(financials.router, prefix="/api/v1/financials", tags=["Financials"])
app.include_router(analytics.router,  prefix="/api/v1/analytics",  tags=["Analytics"])
app.include_router(exports.router,    prefix="/api/v1/exports",    tags=["Exports"])
app.include_router(jobs.router,       prefix="/api/v1/jobs",       tags=["Jobs"])


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "version": settings.app_version}


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("unhandled_exception", error=str(exc), path=str(request.url))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )
