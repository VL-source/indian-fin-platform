"""
Export API — Excel, CSV, and PDF report generation.
"""
from __future__ import annotations

import io
import uuid
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import CommonSizeMetric, PeerGroupMetric, Company

router = APIRouter()


@router.get("/excel/company/{company_id}")
async def export_company_excel(
    company_id: uuid.UUID,
    years: Optional[str] = Query(None, description="Comma-separated years"),
    db: AsyncSession = Depends(get_db),
):
    """Export full company analysis (raw + common-size + time-series) as Excel."""
    # Fetch company info
    comp_result = await db.execute(
        select(Company).where(Company.company_id == company_id)
    )
    company = comp_result.scalar_one_or_none()
    if not company:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Company not found")

    year_filter = []
    if years:
        year_list = [int(y.strip()) for y in years.split(",")]
        year_filter = [CommonSizeMetric.fiscal_year.in_(year_list)]

    # Fetch common-size metrics
    cs_result = await db.execute(
        select(CommonSizeMetric)
        .where(and_(CommonSizeMetric.company_id == company_id, *year_filter))
        .order_by(CommonSizeMetric.fiscal_year, CommonSizeMetric.metric_name)
    )
    cs_rows = cs_result.scalars().all()

    # Build DataFrames
    cs_data = [
        {
            "Fiscal Year": row.fiscal_year,
            "Metric": row.metric_name,
            "Original Label": row.original_label,
            "Raw Value (INR Cr)": float(row.raw_value_inr_cr) if row.raw_value_inr_cr else None,
            "Common Size %": float(row.common_size_pct) if row.common_size_pct else None,
            "Is Derived": row.is_derived,
        }
        for row in cs_rows
    ]

    # Pivot for display
    cs_df = pd.DataFrame(cs_data)
    if not cs_df.empty:
        cs_pivot = cs_df.pivot_table(
            index="Metric",
            columns="Fiscal Year",
            values="Common Size %",
            aggfunc="first",
        ).reset_index()
    else:
        cs_pivot = pd.DataFrame()

    # Write to Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book

        # Formats
        header_fmt = workbook.add_format({
            "bold": True, "bg_color": "#1F3864", "font_color": "white",
            "border": 1, "align": "center",
        })
        pct_fmt = workbook.add_format({"num_format": "0.00%", "border": 1})
        num_fmt = workbook.add_format({"num_format": "#,##0.00", "border": 1})
        text_fmt = workbook.add_format({"border": 1})
        title_fmt = workbook.add_format({"bold": True, "font_size": 14})

        # Sheet 1: Common-Size P&L
        if not cs_pivot.empty:
            cs_pivot.to_excel(writer, sheet_name="Common-Size", index=False)
            ws = writer.sheets["Common-Size"]
            ws.write(0, 0, f"{company.name} — Common-Size Analysis", title_fmt)
            ws.set_column("A:A", 35)
            ws.set_column("B:Z", 12)

        # Sheet 2: Raw Data
        raw_df = pd.DataFrame(cs_data)
        if not raw_df.empty:
            raw_df.to_excel(writer, sheet_name="Raw Data", index=False)
            ws2 = writer.sheets["Raw Data"]
            ws2.set_column("A:G", 20)

    output.seek(0)
    filename = f"{company.nse_ticker or company.name}_financial_analysis.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/excel/peer-group/{peer_group_id}")
async def export_peer_group_excel(
    peer_group_id: uuid.UUID,
    fiscal_year: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Export peer-group benchmark comparison as Excel."""
    from app.models import PeerGroup
    pg_result = await db.execute(
        select(PeerGroup).where(PeerGroup.peer_group_id == peer_group_id)
    )
    pg = pg_result.scalar_one_or_none()

    # Fetch all common-size data for peer group members
    from app.models import PeerGroupMember
    members_result = await db.execute(
        select(PeerGroupMember.company_id)
        .where(PeerGroupMember.peer_group_id == peer_group_id)
    )
    company_ids = [row[0] for row in members_result]

    cs_result = await db.execute(
        select(
            Company.name,
            Company.nse_ticker,
            CommonSizeMetric.metric_name,
            CommonSizeMetric.common_size_pct,
        )
        .join(Company, Company.company_id == CommonSizeMetric.company_id)
        .where(
            CommonSizeMetric.company_id.in_(company_ids),
            CommonSizeMetric.fiscal_year == fiscal_year,
        )
        .order_by(Company.name, CommonSizeMetric.metric_name)
    )
    rows = cs_result.all()

    data = [
        {
            "Company": r[0],
            "Ticker": r[1],
            "Metric": r[2],
            "Common Size %": float(r[3]) if r[3] else None,
        }
        for r in rows
    ]

    df = pd.DataFrame(data)
    pivot = pd.DataFrame()
    if not df.empty:
        pivot = df.pivot_table(
            index="Metric",
            columns="Company",
            values="Common Size %",
            aggfunc="first",
        ).reset_index()

    # Fetch peer group aggregates
    pgm_result = await db.execute(
        select(PeerGroupMetric)
        .where(
            PeerGroupMetric.peer_group_id == peer_group_id,
            PeerGroupMetric.fiscal_year == fiscal_year,
        )
        .order_by(PeerGroupMetric.metric_name)
    )
    pgm_rows = pgm_result.scalars().all()
    agg_data = [
        {
            "Metric": r.metric_name,
            "Equal-Weight Avg %": float(r.equal_weight_avg) if r.equal_weight_avg else None,
            "Mkt-Cap Weighted Avg %": float(r.mktcap_weight_avg) if r.mktcap_weight_avg else None,
            "Median %": float(r.median_val) if r.median_val else None,
            "Std Dev": float(r.std_dev) if r.std_dev else None,
            "P25": float(r.p25) if r.p25 else None,
            "P75": float(r.p75) if r.p75 else None,
            "# Companies": r.count_companies,
        }
        for r in pgm_rows
    ]
    agg_df = pd.DataFrame(agg_data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        pg_name = pg.name if pg else "Peer Group"
        if not pivot.empty:
            pivot.to_excel(writer, sheet_name="Peer Comparison", index=False)
        if not agg_df.empty:
            agg_df.to_excel(writer, sheet_name="Aggregates", index=False)

    output.seek(0)
    filename = f"peer_group_{peer_group_id}_FY{fiscal_year}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/csv/company/{company_id}")
async def export_company_csv(
    company_id: uuid.UUID,
    years: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Export common-size data as CSV."""
    year_filter = []
    if years:
        year_list = [int(y.strip()) for y in years.split(",")]
        year_filter = [CommonSizeMetric.fiscal_year.in_(year_list)]

    result = await db.execute(
        select(CommonSizeMetric)
        .where(and_(CommonSizeMetric.company_id == company_id, *year_filter))
        .order_by(CommonSizeMetric.fiscal_year, CommonSizeMetric.metric_name)
    )
    rows = result.scalars().all()

    data = [
        {
            "fiscal_year": r.fiscal_year,
            "metric_name": r.metric_name,
            "original_label": r.original_label,
            "raw_value_inr_cr": float(r.raw_value_inr_cr) if r.raw_value_inr_cr else None,
            "common_size_pct": float(r.common_size_pct) if r.common_size_pct else None,
            "is_derived": r.is_derived,
        }
        for r in rows
    ]

    df = pd.DataFrame(data)
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=company_{company_id}_common_size.csv"},
    )
