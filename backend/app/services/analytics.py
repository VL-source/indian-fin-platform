"""
Analytics services: common-size computation, peer-group aggregation,
time-series analysis.
"""
from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    CommonSizeMetric, PeerGroupMetric, TimeSeriesMetric,
    FinancialLineItem, Company, PeerGroup, PeerGroupMember,
)


class CommonSizeService:
    """
    Computes common-size financial statements.
    Revenue = 100%; every other line item = (value / revenue) * 100
    """

    async def compute_for_company(
        self,
        db: AsyncSession,
        company_id: str,
        years: Optional[List[int]] = None,
        consolidation: str = "consolidated",
    ) -> None:
        """
        Compute and persist common-size metrics for a company.
        Call after ingestion or data update.
        """
        # Fetch all line items for the company
        filters = [FinancialLineItem.company_id == company_id]
        if years:
            filters.append(FinancialLineItem.fiscal_year.in_(years))

        result = await db.execute(
            select(FinancialLineItem)
            .where(and_(*filters))
            .order_by(FinancialLineItem.fiscal_year, FinancialLineItem.sort_order)
        )
        items = result.scalars().all()

        # Group by year
        by_year: Dict[int, List[FinancialLineItem]] = {}
        for item in items:
            by_year.setdefault(item.fiscal_year, []).append(item)

        for year, year_items in by_year.items():
            await self._compute_year(db, company_id, year, year_items, consolidation)

    async def _compute_year(
        self,
        db: AsyncSession,
        company_id: str,
        year: int,
        items: List[FinancialLineItem],
        consolidation: str,
    ) -> None:
        """Compute common-size for a single company-year."""
        # Find revenue base
        revenue_base = self._find_revenue(items)
        if revenue_base is None or revenue_base < settings.min_revenue_for_common_size:
            return  # Cannot compute without a valid revenue denominator

        # Delete old records for this company-year
        from sqlalchemy import delete
        await db.execute(
            delete(CommonSizeMetric).where(
                CommonSizeMetric.company_id == company_id,
                CommonSizeMetric.fiscal_year == year,
                CommonSizeMetric.consolidation == consolidation,
            )
        )

        # Compute and insert
        for item in items:
            if item.std_value_inr_cr is None:
                continue
            raw_val = float(item.std_value_inr_cr)
            cs_pct = (raw_val / revenue_base) * 100.0

            # Revenue line itself = 100%
            if item.standardized_label == "revenue":
                cs_pct = 100.0

            cs = CommonSizeMetric(
                company_id=company_id,
                fiscal_year=year,
                consolidation=consolidation,
                metric_name=item.standardized_label or item.original_label,
                original_label=item.original_label,
                raw_value_inr_cr=Decimal(str(raw_val)),
                revenue_base_inr_cr=Decimal(str(revenue_base)),
                common_size_pct=Decimal(str(round(cs_pct, 6))),
                is_derived=item.is_derived,
            )
            db.add(cs)

        await db.flush()

    @staticmethod
    def _find_revenue(items: List[FinancialLineItem]) -> Optional[float]:
        """Find revenue from line items, preferring 'revenue' standardized label."""
        # First priority: exact 'revenue' match
        for item in items:
            if (
                item.standardized_label == "revenue"
                and item.std_value_inr_cr is not None
                and item.std_value_inr_cr > 0
            ):
                return float(item.std_value_inr_cr)

        # Second: gross_revenue
        for item in items:
            if (
                item.standardized_label == "gross_revenue"
                and item.std_value_inr_cr is not None
                and item.std_value_inr_cr > 0
            ):
                return float(item.std_value_inr_cr)

        return None


class PeerGroupAnalyticsService:
    """
    Computes dynamic peer-group aggregates across companies.
    Handles all line items that appear frequently enough.
    No fixed template — dynamically discovers metrics.
    """

    MIN_COMPANIES_FOR_AGGREGATE = 2

    async def compute_peer_group_metrics(
        self,
        db: AsyncSession,
        peer_group_id: str,
        years: Optional[List[int]] = None,
        metric_names: Optional[List[str]] = None,
    ) -> None:
        """
        Compute and persist peer-group aggregate statistics.
        Automatically discovers all metrics present in the group.
        """
        # Get member companies
        members_result = await db.execute(
            select(PeerGroupMember.company_id)
            .where(PeerGroupMember.peer_group_id == peer_group_id)
        )
        company_ids = [row[0] for row in members_result]

        if len(company_ids) < self.MIN_COMPANIES_FOR_AGGREGATE:
            return

        # Get market caps for weighted averages
        mktcap_result = await db.execute(
            select(Company.company_id, Company.market_cap_inr_cr)
            .where(Company.company_id.in_(company_ids))
        )
        mktcap_map: Dict[str, float] = {
            str(row[0]): float(row[1] or 0) for row in mktcap_result
        }
        total_mktcap = sum(mktcap_map.values())

        # Fetch common-size metrics for all members
        cs_filter = [CommonSizeMetric.company_id.in_(company_ids)]
        if years:
            cs_filter.append(CommonSizeMetric.fiscal_year.in_(years))
        if metric_names:
            cs_filter.append(CommonSizeMetric.metric_name.in_(metric_names))

        cs_result = await db.execute(
            select(
                CommonSizeMetric.fiscal_year,
                CommonSizeMetric.metric_name,
                CommonSizeMetric.company_id,
                CommonSizeMetric.common_size_pct,
                CommonSizeMetric.raw_value_inr_cr,
            ).where(and_(*cs_filter))
        )
        rows = cs_result.all()

        # Group by (year, metric)
        grouped: Dict[Tuple[int, str], List[Tuple[str, float, float]]] = {}
        for fy, metric, cid, cs_pct, raw_val in rows:
            key = (fy, metric)
            if cs_pct is not None:
                cid_str = str(cid)
                mktcap = mktcap_map.get(cid_str, 0)
                grouped.setdefault(key, []).append(
                    (cid_str, float(cs_pct), mktcap)
                )

        # Delete old peer group metrics
        from sqlalchemy import delete
        await db.execute(
            delete(PeerGroupMetric).where(
                PeerGroupMetric.peer_group_id == peer_group_id,
                *([PeerGroupMetric.fiscal_year.in_(years)] if years else []),
            )
        )

        # Compute and insert aggregates
        for (fy, metric), data_points in grouped.items():
            if len(data_points) < self.MIN_COMPANIES_FOR_AGGREGATE:
                continue

            values = [d[1] for d in data_points]
            weights = [d[2] for d in data_points]

            eq_avg = statistics.mean(values)
            median = statistics.median(values)
            std_dev = statistics.stdev(values) if len(values) > 1 else 0.0
            min_val = min(values)
            max_val = max(values)

            # Percentiles
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            p25 = sorted_vals[max(0, int(n * 0.25) - 1)]
            p75 = sorted_vals[min(n - 1, int(n * 0.75))]

            # Market-cap weighted average
            if total_mktcap > 0 and sum(weights) > 0:
                mktcap_avg = sum(v * w for v, w in zip(values, weights)) / sum(weights)
            else:
                mktcap_avg = eq_avg

            pgm = PeerGroupMetric(
                peer_group_id=peer_group_id,
                fiscal_year=fy,
                metric_name=metric,
                equal_weight_avg=Decimal(str(round(eq_avg, 6))),
                mktcap_weight_avg=Decimal(str(round(mktcap_avg, 6))),
                median_val=Decimal(str(round(median, 6))),
                std_dev=Decimal(str(round(std_dev, 6))),
                min_val=Decimal(str(round(min_val, 6))),
                max_val=Decimal(str(round(max_val, 6))),
                p25=Decimal(str(round(p25, 6))),
                p75=Decimal(str(round(p75, 6))),
                count_companies=len(data_points),
            )
            db.add(pgm)

        await db.flush()


class TimeSeriesService:
    """
    Computes multi-year time-series analytics.
    Generates growth rates, CAGR, rolling averages, and volatility.
    """

    async def compute_for_company(
        self,
        db: AsyncSession,
        company_id: str,
        metric_names: Optional[List[str]] = None,
    ) -> None:
        """Compute time-series metrics for all available years."""
        filters = [CommonSizeMetric.company_id == company_id]
        if metric_names:
            filters.append(CommonSizeMetric.metric_name.in_(metric_names))

        result = await db.execute(
            select(
                CommonSizeMetric.metric_name,
                CommonSizeMetric.fiscal_year,
                CommonSizeMetric.common_size_pct,
                CommonSizeMetric.raw_value_inr_cr,
            )
            .where(and_(*filters))
            .order_by(CommonSizeMetric.metric_name, CommonSizeMetric.fiscal_year)
        )
        rows = result.all()

        # Group by metric
        by_metric: Dict[str, Dict[int, float]] = {}
        for metric, fy, cs_pct, _ in rows:
            if cs_pct is not None:
                by_metric.setdefault(metric, {})[fy] = float(cs_pct)

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        for metric, year_values in by_metric.items():
            if len(year_values) < 2:
                continue

            years = sorted(year_values.keys())
            values = [year_values[y] for y in years]

            base_year = years[0]
            end_year = years[-1]
            n_years = end_year - base_year

            # YoY growth (latest year)
            yoy = None
            if len(values) >= 2 and values[-2] and values[-2] != 0:
                yoy = (values[-1] / values[-2] - 1) * 100

            # CAGR
            cagr = None
            if n_years > 0 and values[0] and values[0] != 0:
                cagr = ((abs(values[-1]) / abs(values[0])) ** (1 / n_years) - 1) * 100
                if values[-1] < 0 or values[0] < 0:
                    cagr = None  # CAGR undefined for sign changes

            # Rolling averages
            rolling_3 = statistics.mean(values[-3:]) if len(values) >= 3 else None
            rolling_5 = statistics.mean(values[-5:]) if len(values) >= 5 else None

            # Volatility
            volatility = statistics.stdev(values) if len(values) >= 2 else None

            annual_json = {str(y): round(v, 4) for y, v in zip(years, values)}

            stmt = pg_insert(TimeSeriesMetric.__table__).values(
                company_id=company_id,
                metric_name=metric,
                base_year=base_year,
                end_year=end_year,
                yoy_growth_pct=round(yoy, 4) if yoy is not None else None,
                cagr_pct=round(cagr, 4) if cagr is not None else None,
                cagr_years=n_years,
                rolling_3yr_avg=round(rolling_3, 4) if rolling_3 is not None else None,
                rolling_5yr_avg=round(rolling_5, 4) if rolling_5 is not None else None,
                volatility_std_dev=round(volatility, 4) if volatility is not None else None,
                annual_values=annual_json,
            ).on_conflict_do_update(
                index_elements=["company_id", "metric_name", "base_year", "end_year"],
                set_=dict(
                    yoy_growth_pct=round(yoy, 4) if yoy is not None else None,
                    cagr_pct=round(cagr, 4) if cagr is not None else None,
                    rolling_3yr_avg=round(rolling_3, 4) if rolling_3 is not None else None,
                    rolling_5yr_avg=round(rolling_5, 4) if rolling_5 is not None else None,
                    volatility_std_dev=round(volatility, 4) if volatility is not None else None,
                    annual_values=annual_json,
                ),
            )
            await db.execute(stmt)

        await db.flush()
