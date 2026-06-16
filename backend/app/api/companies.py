"""
Company management API — search, CRUD, sector lookup.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.etl.screener_provider import ScreenerProvider
from app.etl.nse_provider import NSEProvider
from app.models import Company, PeerGroup, PeerGroupMember

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────

class CompanyOut(BaseModel):
    company_id: uuid.UUID
    nse_ticker: Optional[str]
    bse_code: Optional[str]
    isin: Optional[str]
    name: str
    sector: Optional[str]
    industry: Optional[str]
    market_cap_inr_cr: Optional[float]
    listing_exchange: Optional[str]

    class Config:
        from_attributes = True


class PeerGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    group_type: str = Field(default="custom")
    sector: Optional[str] = None
    company_ids: List[uuid.UUID] = Field(default_factory=list)


class PeerGroupOut(BaseModel):
    peer_group_id: uuid.UUID
    name: str
    description: Optional[str]
    group_type: Optional[str]
    sector: Optional[str]
    member_count: int

    class Config:
        from_attributes = True


class SectorSearchResult(BaseModel):
    ticker: str
    name: str
    exchange: str
    sector: Optional[str]
    market_cap_inr_cr: Optional[float]
    isin: Optional[str]


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/search", response_model=List[CompanyOut])
async def search_companies(
    q: str = Query(..., min_length=1, description="Company name, NSE ticker, or BSE code"),
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Search companies in the database by name or ticker."""
    result = await db.execute(
        select(Company)
        .where(
            or_(
                func.lower(Company.name).contains(q.lower()),
                func.lower(Company.nse_ticker).contains(q.lower()),
                Company.bse_code == q.upper(),
                Company.isin == q.upper(),
            )
        )
        .limit(limit)
    )
    companies = result.scalars().all()
    return companies


@router.get("/search/external", response_model=List[SectorSearchResult])
async def search_companies_external(
    q: str = Query(..., min_length=1),
):
    """Search for a company using external providers (Screener + NSE)."""
    provider = ScreenerProvider()
    results = await provider.search_company(q)
    await provider.close()
    return [
        SectorSearchResult(
            ticker=r.nse_ticker or r.bse_code or r.ticker,
            name=r.name,
            exchange=r.exchange,
            sector=r.sector,
            market_cap_inr_cr=r.market_cap_inr_cr,
            isin=r.isin,
        )
        for r in results
        if (r.nse_ticker or r.bse_code or r.ticker)  # never return an entry with no usable identifier
    ]


@router.get("/sector/{sector}", response_model=List[SectorSearchResult])
async def get_top_by_sector(
    sector: str,
    limit: int = Query(default=100, le=200),
):
    """
    Fetch top companies in a sector by market cap.
    Returns a list for user review/editing before ingestion.
    """
    provider = ScreenerProvider()
    results = await provider.get_top_companies_by_sector(sector, limit)
    await provider.close()
    return [
        SectorSearchResult(
            ticker=r.nse_ticker or r.bse_code or r.ticker,
            name=r.name,
            exchange=r.exchange,
            sector=r.sector,
            market_cap_inr_cr=r.market_cap_inr_cr,
            isin=r.isin,
        )
        for r in results
        if (r.nse_ticker or r.bse_code or r.ticker)
    ]


@router.get("/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Company).where(Company.company_id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/", response_model=List[CompanyOut])
async def list_companies(
    sector: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List all ingested companies, optionally filtered by sector."""
    q = select(Company).where(Company.is_active.is_(True))
    if sector:
        q = q.where(func.lower(Company.sector) == sector.lower())
    q = q.order_by(Company.market_cap_inr_cr.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


# ── Peer Groups ────────────────────────────────────────────────────────────

@router.post("/peer-groups", response_model=PeerGroupOut)
async def create_peer_group(
    payload: PeerGroupCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new peer group and add companies to it."""
    pg = PeerGroup(
        name=payload.name,
        description=payload.description,
        group_type=payload.group_type,
        sector=payload.sector,
        created_by="api",
    )
    db.add(pg)
    await db.flush()

    for cid in payload.company_ids:
        db.add(PeerGroupMember(peer_group_id=pg.peer_group_id, company_id=cid))

    await db.flush()

    member_count_result = await db.execute(
        select(func.count(PeerGroupMember.id))
        .where(PeerGroupMember.peer_group_id == pg.peer_group_id)
    )
    member_count = member_count_result.scalar() or 0

    return PeerGroupOut(
        peer_group_id=pg.peer_group_id,
        name=pg.name,
        description=pg.description,
        group_type=pg.group_type,
        sector=pg.sector,
        member_count=member_count,
    )


@router.get("/peer-groups/{peer_group_id}/members", response_model=List[CompanyOut])
async def get_peer_group_members(
    peer_group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Company)
        .join(PeerGroupMember, PeerGroupMember.company_id == Company.company_id)
        .where(PeerGroupMember.peer_group_id == peer_group_id)
        .order_by(Company.market_cap_inr_cr.desc())
    )
    return result.scalars().all()


@router.delete("/peer-groups/{peer_group_id}/members/{company_id}")
async def remove_from_peer_group(
    peer_group_id: uuid.UUID,
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import delete
    await db.execute(
        delete(PeerGroupMember).where(
            PeerGroupMember.peer_group_id == peer_group_id,
            PeerGroupMember.company_id == company_id,
        )
    )
    return {"status": "removed"}
