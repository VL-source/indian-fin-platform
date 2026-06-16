"""
Background data-refresh jobs.

Originally implemented as Celery tasks requiring separate worker/beat/Flower
containers plus Redis. Simplified to plain async functions that are either
called directly from API routes (via FastAPI BackgroundTasks, see app.api.jobs)
or scheduled in-process via APScheduler (see app.main's lifespan). This lets
the whole platform run as three services (db, backend, frontend) instead of
seven, which is far cheaper and simpler to deploy and host.
"""
from __future__ import annotations

from typing import List, Optional

import structlog

logger = structlog.get_logger(__name__)


async def ingest_tickers_task(tickers: List[str], years: Optional[List[int]] = None):
    """Ingest financial data for a list of tickers."""
    from app.etl.orchestrator import ETLOrchestrator

    orchestrator = ETLOrchestrator(years=years)
    try:
        return await orchestrator.ingest_companies(tickers, job_id=None)
    finally:
        await orchestrator.close()


async def compute_analytics_task(company_id: str):
    """Post-ingestion: compute common-size, time-series for a company."""
    from app.database import get_db_context
    from app.services.analytics import CommonSizeService, TimeSeriesService

    async with get_db_context() as db:
        cs_svc = CommonSizeService()
        ts_svc = TimeSeriesService()
        await cs_svc.compute_for_company(db, company_id)
        await ts_svc.compute_for_company(db, company_id)


async def compute_peer_group_task(peer_group_id: str):
    """Compute peer-group aggregates."""
    from app.database import get_db_context
    from app.services.analytics import PeerGroupAnalyticsService

    async with get_db_context() as db:
        svc = PeerGroupAnalyticsService()
        await svc.compute_peer_group_metrics(db, peer_group_id)


async def refresh_market_caps():
    """Daily: refresh market cap data from NSE."""
    from datetime import datetime

    from sqlalchemy import select, update

    from app.database import get_db_context
    from app.etl.nse_provider import NSEProvider
    from app.models import Company

    provider = NSEProvider()
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(Company.company_id, Company.nse_ticker)
                .where(Company.nse_ticker.isnot(None))
            )
            companies = result.all()
            for company_id, ticker in companies:
                mktcap = await provider.get_market_cap(ticker)
                if mktcap:
                    await db.execute(
                        update(Company)
                        .where(Company.company_id == company_id)
                        .values(
                            market_cap_inr_cr=mktcap,
                            market_cap_date=datetime.now().date(),
                        )
                    )
    finally:
        await provider.close()


async def refresh_materialized_views():
    """Daily: refresh PostgreSQL materialized views."""
    from sqlalchemy import text

    from app.database import get_db_context

    async with get_db_context() as db:
        await db.execute(text("SELECT refresh_materialized_views()"))
