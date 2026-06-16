"""
Financials API — raw financial statements and line items.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import FinancialStatement, FinancialLineItem

router = APIRouter()


class LineItemOut(BaseModel):
    line_item_id: uuid.UUID
    original_label: str
    standardized_label: Optional[str]
    label_category: Optional[str]
    label_subcategory: Optional[str]
    parent_label: Optional[str]
    hierarchy_level: int
    sort_order: Optional[int]
    reported_value: Optional[float]
    std_value_inr_cr: Optional[float]
    mapping_confidence: Optional[float]
    is_derived: bool

    class Config:
        from_attributes = True


class StatementOut(BaseModel):
    statement_id: uuid.UUID
    fiscal_year: int
    statement_type: str
    consolidation: str
    source_type: Optional[str]
    source_document: Optional[str]
    data_quality_score: Optional[float]
    line_items: List[LineItemOut]

    class Config:
        from_attributes = True


@router.get("/{company_id}/statements", response_model=List[StatementOut])
async def get_statements(
    company_id: uuid.UUID,
    fiscal_year: Optional[int] = None,
    statement_type: Optional[str] = None,
    consolidation: str = "consolidated",
    db: AsyncSession = Depends(get_db),
):
    """Return financial statements with all line items for a company."""
    filters = [
        FinancialStatement.company_id == company_id,
        FinancialStatement.consolidation == consolidation,
    ]
    if fiscal_year:
        filters.append(FinancialStatement.fiscal_year == fiscal_year)
    if statement_type:
        filters.append(FinancialStatement.statement_type == statement_type)

    result = await db.execute(
        select(FinancialStatement)
        .where(and_(*filters))
        .order_by(FinancialStatement.fiscal_year.desc())
    )
    return result.scalars().all()


@router.get("/{company_id}/line-items")
async def get_line_items(
    company_id: uuid.UUID,
    fiscal_year: Optional[int] = None,
    standardized_label: Optional[str] = None,
    category: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> List[LineItemOut]:
    """Return individual line items with full traceability metadata."""
    filters = [FinancialLineItem.company_id == company_id]
    if fiscal_year:
        filters.append(FinancialLineItem.fiscal_year == fiscal_year)
    if standardized_label:
        filters.append(FinancialLineItem.standardized_label == standardized_label)
    if category:
        filters.append(FinancialLineItem.label_category == category)

    result = await db.execute(
        select(FinancialLineItem)
        .where(and_(*filters))
        .order_by(
            FinancialLineItem.fiscal_year,
            FinancialLineItem.hierarchy_level,
            FinancialLineItem.sort_order,
        )
    )
    return result.scalars().all()


@router.get("/{company_id}/available-years")
async def get_available_years(
    company_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> List[int]:
    from sqlalchemy import distinct
    result = await db.execute(
        select(distinct(FinancialStatement.fiscal_year))
        .where(FinancialStatement.company_id == company_id)
        .order_by(FinancialStatement.fiscal_year)
    )
    return [row[0] for row in result]
