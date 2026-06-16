"""
Ingestion job management API.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import IngestionJob

router = APIRouter()


class IngestTickersRequest(BaseModel):
    tickers: List[str] = Field(..., min_length=1, max_length=100)
    years: Optional[List[int]] = None
    provider_priority: Optional[List[str]] = None


class IngestSectorRequest(BaseModel):
    sector: str
    limit: int = Field(default=100, le=200)
    selected_tickers: Optional[List[str]] = None   # After user review


class JobOut(BaseModel):
    job_id: uuid.UUID
    status: str
    progress_pct: float
    summary: Optional[Dict[str, Any]]
    created_at: str

    class Config:
        from_attributes = True


@router.post("/ingest/tickers", response_model=JobOut)
async def ingest_tickers(
    payload: IngestTickersRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a background ingestion job for a list of tickers.
    Returns immediately with a job_id to poll for progress.
    """
    job = IngestionJob(
        company_ids=[],  # resolved during ingestion
        requested_years=payload.years,
        status="pending",
        provider_priority=payload.provider_priority,
    )
    db.add(job)
    await db.flush()
    job_id = job.job_id

    async def _run():
        from app.etl.orchestrator import ETLOrchestrator
        from app.database import get_db_context

        orchestrator = ETLOrchestrator(
            provider_priority=payload.provider_priority,
            years=payload.years,
        )
        try:
            await orchestrator.ingest_companies(payload.tickers, job_id=job_id)
        finally:
            await orchestrator.close()

    background_tasks.add_task(_run)

    return JobOut(
        job_id=job.job_id,
        status=job.status,
        progress_pct=float(job.progress_pct),
        summary=job.summary,
        created_at=str(job.created_at),
    )


@router.post("/ingest/sector")
async def ingest_sector(
    payload: IngestSectorRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Two-phase sector ingestion:
    1. If no selected_tickers provided, returns top N companies for user review.
    2. If selected_tickers provided, starts ingestion job.
    """
    if not payload.selected_tickers:
        # Phase 1: Return company list for review
        from app.etl.orchestrator import ETLOrchestrator
        orchestrator = ETLOrchestrator()
        companies = await orchestrator.resolve_and_ingest_sector(
            payload.sector, limit=payload.limit
        )
        await orchestrator.close()
        return {
            "phase": "review",
            "sector": payload.sector,
            "companies": [
                {
                    "ticker": c.nse_ticker or c.ticker,
                    "name": c.name,
                    "market_cap_inr_cr": c.market_cap_inr_cr,
                    "exchange": c.exchange,
                }
                for c in companies
            ],
        }
    else:
        # Phase 2: Start ingestion with user-selected tickers
        job = IngestionJob(status="pending")
        db.add(job)
        await db.flush()
        job_id = job.job_id

        async def _run():
            from app.etl.orchestrator import ETLOrchestrator
            from app.database import get_db_context
            orchestrator = ETLOrchestrator()
            try:
                await orchestrator.ingest_companies(
                    payload.selected_tickers, job_id=job_id
                )
            finally:
                await orchestrator.close()

        background_tasks.add_task(_run)
        return {"phase": "ingestion_started", "job_id": str(job_id)}


@router.get("/{job_id}", response_model=JobOut)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Poll a job's progress."""
    result = await db.execute(
        select(IngestionJob).where(IngestionJob.job_id == job_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut(
        job_id=job.job_id,
        status=job.status,
        progress_pct=float(job.progress_pct),
        summary=job.summary,
        created_at=str(job.created_at),
    )


@router.get("/", response_model=List[JobOut])
async def list_jobs(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IngestionJob)
        .order_by(IngestionJob.created_at.desc())
        .limit(limit)
    )
    return [
        JobOut(
            job_id=j.job_id,
            status=j.status,
            progress_pct=float(j.progress_pct),
            summary=j.summary,
            created_at=str(j.created_at),
        )
        for j in result.scalars().all()
    ]
