"""
Analytics API — common-size, peer-group, time-series endpoints.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    CommonSizeMetric, PeerGroupMetric, TimeSeriesMetric,
    Company, PeerGroupMember,
)
from app.services.analytics import (
    CommonSizeService, PeerGroupAnalyticsService, TimeSeriesService
)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────

class CommonSizeRow(BaseModel):
    fiscal_year: int
    metric_name: str
    original_label: Optional[str]
    raw_value_inr_cr: Optional[float]
    common_size_pct: Optional[float]
    is_derived: bool

    class Config:
        from_attributes = True


class PeerMetricRow(BaseModel):
    fiscal_year: int
    metric_name: str
    equal_weight_avg: Optional[float]
    mktcap_weight_avg: Optional[float]
    median_val: Optional[float]
    std_dev: Optional[float]
    p25: Optional[float]
    p75: Optional[float]
    count_companies: Optional[int]

    class Config:
        from_attributes = True


class TimeSeriesRow(BaseModel):
    metric_name: str
    base_year: Optional[int]
    end_year: Optional[int]
    yoy_growth_pct: Optional[float]
    cagr_pct: Optional[float]
    cagr_years: Optional[int]
    rolling_3yr_avg: Optional[float]
    rolling_5yr_avg: Optional[float]
    volatility_std_dev: Optional[float]
    annual_values: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


class CommonSizeCompareRow(BaseModel):
    company_id: uuid.UUID
    company_name: str
    nse_ticker: Optional[str]
    fiscal_year: int
    metric_name: str
    common_size_pct: Optional[float]


# ── Common-size endpoints ─────────────────────────────────────────────────

@router.get("/common-size/{company_id}", response_model=List[CommonSizeRow])
async def get_common_size(
    company_id: uuid.UUID,
    years: Optional[str] = Query(None, description="Comma-separated fiscal years, e.g. '2022,2023,2024'"),
    metrics: Optional[str] = Query(None, description="Comma-separated metric names"),
    db: AsyncSession = Depends(get_db),
):
    """
    Return common-size metrics for a company.
    If no years specified, returns all available years.
    """
    filters = [CommonSizeMetric.company_id == company_id]

    if years:
        year_list = [int(y.strip()) for y in years.split(",")]
        filters.append(CommonSizeMetric.fiscal_year.in_(year_list))

    if metrics:
        metric_list = [m.strip() for m in metrics.split(",")]
        filters.append(CommonSizeMetric.metric_name.in_(metric_list))

    result = await db.execute(
        select(CommonSizeMetric)
        .where(and_(*filters))
        .order_by(CommonSizeMetric.fiscal_year, CommonSizeMetric.metric_name)
    )
    return result.scalars().all()


@router.get("/common-size/compare/peer-group/{peer_group_id}", response_model=List[CommonSizeCompareRow])
async def compare_common_size_peer_group(
    peer_group_id: uuid.UUID,
    fiscal_year: int = Query(...),
    metrics: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Return common-size values for all companies in a peer group for a given year.
    Useful for side-by-side comparison.
    """
    # Get member company IDs
    members_result = await db.execute(
        select(PeerGroupMember.company_id)
        .where(PeerGroupMember.peer_group_id == peer_group_id)
    )
    company_ids = [row[0] for row in members_result]

    if not company_ids:
        return []

    filters = [
        CommonSizeMetric.company_id.in_(company_ids),
        CommonSizeMetric.fiscal_year == fiscal_year,
    ]
    if metrics:
        metric_list = [m.strip() for m in metrics.split(",")]
        filters.append(CommonSizeMetric.metric_name.in_(metric_list))

    result = await db.execute(
        select(
            CommonSizeMetric.company_id,
            Company.name,
            Company.nse_ticker,
            CommonSizeMetric.fiscal_year,
            CommonSizeMetric.metric_name,
            CommonSizeMetric.common_size_pct,
        )
        .join(Company, Company.company_id == CommonSizeMetric.company_id)
        .where(and_(*filters))
        .order_by(CommonSizeMetric.metric_name, Company.name)
    )
    rows = result.all()

    return [
        CommonSizeCompareRow(
            company_id=row[0],
            company_name=row[1],
            nse_ticker=row[2],
            fiscal_year=row[3],
            metric_name=row[4],
            common_size_pct=float(row[5]) if row[5] is not None else None,
        )
        for row in rows
    ]


@router.post("/common-size/{company_id}/recompute")
async def recompute_common_size(
    company_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Trigger recomputation of common-size metrics for a company."""
    async def _recompute():
        async with __import__("app.database", fromlist=["get_db_context"]).get_db_context() as session:
            svc = CommonSizeService()
            await svc.compute_for_company(session, company_id)

    background_tasks.add_task(_recompute)
    return {"status": "recompute_queued", "company_id": str(company_id)}


# ── Peer group endpoints ──────────────────────────────────────────────────

@router.get("/peer-group/{peer_group_id}", response_model=List[PeerMetricRow])
async def get_peer_group_metrics(
    peer_group_id: uuid.UUID,
    years: Optional[str] = Query(None),
    metrics: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return peer-group aggregate metrics (avg, median, std dev, percentiles)."""
    filters = [PeerGroupMetric.peer_group_id == peer_group_id]

    if years:
        year_list = [int(y.strip()) for y in years.split(",")]
        filters.append(PeerGroupMetric.fiscal_year.in_(year_list))

    if metrics:
        metric_list = [m.strip() for m in metrics.split(",")]
        filters.append(PeerGroupMetric.metric_name.in_(metric_list))

    result = await db.execute(
        select(PeerGroupMetric)
        .where(and_(*filters))
        .order_by(PeerGroupMetric.fiscal_year, PeerGroupMetric.metric_name)
    )
    return result.scalars().all()


@router.post("/peer-group/{peer_group_id}/recompute")
async def recompute_peer_group(
    peer_group_id: uuid.UUID,
    background_tasks: BackgroundTasks,
):
    """Trigger recomputation of peer-group aggregate metrics."""
    async def _recompute():
        from app.database import get_db_context
        async with get_db_context() as session:
            svc = PeerGroupAnalyticsService()
            await svc.compute_peer_group_metrics(session, peer_group_id)

    background_tasks.add_task(_recompute)
    return {"status": "recompute_queued", "peer_group_id": str(peer_group_id)}


# ── Time-series endpoints ─────────────────────────────────────────────────

@router.get("/time-series/{company_id}", response_model=List[TimeSeriesRow])
async def get_time_series(
    company_id: uuid.UUID,
    metrics: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return time-series analytics (CAGR, YoY, rolling averages) for a company."""
    filters = [TimeSeriesMetric.company_id == company_id]
    if metrics:
        metric_list = [m.strip() for m in metrics.split(",")]
        filters.append(TimeSeriesMetric.metric_name.in_(metric_list))

    result = await db.execute(
        select(TimeSeriesMetric)
        .where(and_(*filters))
        .order_by(TimeSeriesMetric.metric_name)
    )
    return result.scalars().all()


@router.get("/available-metrics/{company_id}")
async def get_available_metrics(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    """Return list of all metric names available for a company."""
    from sqlalchemy import distinct
    result = await db.execute(
        select(distinct(CommonSizeMetric.metric_name))
        .where(CommonSizeMetric.company_id == company_id)
        .order_by(CommonSizeMetric.metric_name)
    )
    return [row[0] for row in result]


@router.get("/available-years/{company_id}")
async def get_available_years(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> List[int]:
    """Return list of fiscal years available for a company."""
    from sqlalchemy import distinct
    result = await db.execute(
        select(distinct(CommonSizeMetric.fiscal_year))
        .where(CommonSizeMetric.company_id == company_id)
        .order_by(CommonSizeMetric.fiscal_year)
    )
    return [row[0] for row in result]
