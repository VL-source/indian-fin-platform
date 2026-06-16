"""
ETL Orchestrator — coordinates multi-provider ingestion.

Strategy:
1. For each company-year, attempt providers in priority order.
2. Stop at first successful fetch.
3. Merge data across statement types.
4. Persist to database.
5. Track job progress in IngestionJob table.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Type

import structlog

from app.config import settings
from app.database import get_db_context
from app.etl.base_provider import BaseProvider, CompanySearchResult, RawStatement
from app.etl.screener_provider import ScreenerProvider
from app.etl.nse_provider import NSEProvider
from app.etl.fmp_provider import FMPProvider
from app.models import (
    Company, FinancialStatement, FinancialLineItem,
    IngestionJob, DataQualityAudit,
)
from app.services.normalization import NormalizationEngine

logger = structlog.get_logger(__name__)

# Provider registry — add new providers here
PROVIDER_REGISTRY: Dict[str, Type[BaseProvider]] = {
    "screener":     ScreenerProvider,
    "nse":          NSEProvider,
    "fmp":          FMPProvider,
}

# Source reliability scores for quality calculation
SOURCE_RELIABILITY: Dict[str, float] = {
    "screener":      0.88,
    "nse":           0.97,
    "fmp":           0.82,
    "mca_xbrl":      0.99,
    "alpha_vantage": 0.78,
    "manual":        0.70,
}


class ETLOrchestrator:
    """
    Coordinates data ingestion across multiple providers.
    Designed to run as a Celery background task.
    """

    def __init__(
        self,
        provider_priority: Optional[List[str]] = None,
        years: Optional[List[int]] = None,
    ):
        self.provider_priority = provider_priority or settings.provider_priority
        self.years = years or self._default_years()
        self._normalization_engine = NormalizationEngine()
        self._log = logger.bind(orchestrator="ETLOrchestrator")

        # Instantiate providers
        self._providers: Dict[str, BaseProvider] = {}
        for name in self.provider_priority:
            cls = PROVIDER_REGISTRY.get(name)
            if cls:
                self._providers[name] = cls()

    @staticmethod
    def _default_years() -> List[int]:
        current = datetime.now().year
        return list(range(current - settings.max_years, current + 1))

    # ── Main entry points ─────────────────────────────────────────────────

    async def ingest_companies(
        self,
        tickers: List[str],
        job_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, bool]:
        """
        Ingest financials for a list of tickers.
        Returns {ticker: success} map.
        """
        results: Dict[str, bool] = {}
        total = len(tickers)

        async with get_db_context() as db:
            if job_id:
                await self._update_job(db, job_id, status="running", started_at=datetime.now(timezone.utc))

        sem = asyncio.Semaphore(settings.etl_concurrency)

        async def ingest_one(ticker: str, idx: int) -> Tuple[str, bool]:
            async with sem:
                try:
                    ok = await self._ingest_ticker(ticker)
                    self._log.info("ingest_complete", ticker=ticker, success=ok)
                    if job_id:
                        pct = (idx + 1) / total * 100
                        async with get_db_context() as db:
                            await self._update_job(db, job_id, progress_pct=pct)
                    return ticker, ok
                except Exception as e:
                    self._log.error("ingest_error", ticker=ticker, error=str(e))
                    return ticker, False

        tasks = [ingest_one(t, i) for i, t in enumerate(tickers)]
        outcomes = await asyncio.gather(*tasks)
        results = dict(outcomes)

        # Finalize job
        if job_id:
            success_count = sum(v for v in results.values())
            async with get_db_context() as db:
                await self._update_job(
                    db, job_id,
                    status="completed",
                    completed_at=datetime.now(timezone.utc),
                    progress_pct=100.0,
                    summary={
                        "total": total,
                        "success": success_count,
                        "failed": total - success_count,
                    },
                )
        return results

    async def resolve_and_ingest_sector(
        self,
        sector: str,
        limit: int = 100,
        job_id: Optional[uuid.UUID] = None,
    ) -> List[CompanySearchResult]:
        """
        Step 1: Resolve top N companies in a sector.
        Returns list for user review BEFORE ingestion.
        """
        companies: List[CompanySearchResult] = []

        for provider_name in self.provider_priority:
            provider = self._providers.get(provider_name)
            if not provider:
                continue
            try:
                companies = await provider.get_top_companies_by_sector(sector, limit)
                if companies:
                    self._log.info(
                        "sector_resolved",
                        sector=sector,
                        provider=provider_name,
                        count=len(companies),
                    )
                    break
            except Exception as e:
                self._log.warning(
                    "sector_resolve_error",
                    sector=sector,
                    provider=provider_name,
                    error=str(e),
                )

        return companies

    # ── Single-ticker ingestion ───────────────────────────────────────────

    async def _ingest_ticker(self, ticker: str) -> bool:
        """
        Full ingestion pipeline for one ticker:
        1. Resolve company identity
        2. Fetch statements from providers (priority order)
        3. Normalize line items
        4. Persist to database
        5. Compute data quality audit
        """
        ticker = ticker.strip().upper()

        # Step 1: Resolve company
        company_info = await self._resolve_company(ticker)
        if not company_info:
            self._log.warning("company_not_found", ticker=ticker)
            return False

        async with get_db_context() as db:
            company = await self._upsert_company(db, company_info)

        # Step 2: Fetch statements
        raw_statements: List[RawStatement] = []
        for provider_name in self.provider_priority:
            provider = self._providers.get(provider_name)
            if not provider:
                continue
            try:
                stmts = await provider.get_financial_statements(ticker, self.years)
                if stmts:
                    raw_statements.extend(stmts)
                    self._log.info(
                        "statements_fetched",
                        ticker=ticker,
                        provider=provider_name,
                        count=len(stmts),
                    )
                    # Continue to merge data from other providers for different years/types
            except Exception as e:
                self._log.warning(
                    "provider_fetch_error",
                    ticker=ticker,
                    provider=provider_name,
                    error=str(e),
                )

        if not raw_statements:
            self._log.warning("no_statements_found", ticker=ticker)
            return False

        # Step 3: Normalize and persist
        async with get_db_context() as db:
            for raw_stmt in raw_statements:
                normalized = await self._normalization_engine.normalize(raw_stmt)
                await self._persist_statement(db, company, raw_stmt, normalized)

            # Step 4: Data quality audit
            await self._compute_quality_audit(db, company)

        return True

    async def _resolve_company(self, ticker: str) -> Optional[CompanySearchResult]:
        """Try each provider to get company metadata."""
        for provider_name in self.provider_priority:
            provider = self._providers.get(provider_name)
            if not provider:
                continue
            try:
                results = await provider.search_company(ticker)
                for r in results:
                    if (r.nse_ticker and r.nse_ticker.upper() == ticker) or \
                       (r.bse_code and r.bse_code == ticker):
                        return r
                if results:
                    return results[0]  # best match
            except Exception:
                pass
        return None

    async def _upsert_company(self, db, info: CompanySearchResult) -> Company:
        """Insert or update company record."""
        from sqlalchemy import select
        result = await db.execute(
            select(Company).where(
                (Company.nse_ticker == info.nse_ticker) |
                (Company.bse_code == info.bse_code) |
                (Company.isin == info.isin)
            ).limit(1)
        )
        company = result.scalar_one_or_none()
        if company is None:
            company = Company(
                nse_ticker=info.nse_ticker,
                bse_code=info.bse_code,
                isin=info.isin,
                name=info.name,
                sector=info.sector,
                industry=info.industry,
                market_cap_inr_cr=info.market_cap_inr_cr,
                market_cap_date=datetime.now().date() if info.market_cap_inr_cr else None,
                listing_exchange=info.exchange,
            )
            db.add(company)
            await db.flush()
        else:
            # Update mutable fields
            if info.market_cap_inr_cr:
                company.market_cap_inr_cr = info.market_cap_inr_cr
                company.market_cap_date = datetime.now().date()
            if info.sector:
                company.sector = info.sector

        return company

    async def _persist_statement(
        self, db, company: Company, raw: RawStatement, normalized: List[Dict]
    ) -> None:
        """Upsert financial statement and all line items."""
        from sqlalchemy import select

        # Upsert statement record
        stmt_result = await db.execute(
            select(FinancialStatement).where(
                FinancialStatement.company_id == company.company_id,
                FinancialStatement.fiscal_year == raw.fiscal_year,
                FinancialStatement.statement_type == raw.statement_type,
                FinancialStatement.consolidation == raw.consolidation,
                FinancialStatement.source_type == raw.source_type,
            ).limit(1)
        )
        fin_stmt = stmt_result.scalar_one_or_none()
        if fin_stmt is None:
            fin_stmt = FinancialStatement(
                company_id=company.company_id,
                fiscal_year=raw.fiscal_year,
                statement_type=raw.statement_type,
                consolidation=raw.consolidation,
                reporting_currency=raw.reporting_currency,
                reporting_unit=raw.reporting_unit,
                source_type=raw.source_type,
                source_url=raw.source_url,
                source_document=raw.source_document,
                source_page_ref=raw.source_page_ref,
                source_confidence=raw.source_confidence,
                data_quality_score=SOURCE_RELIABILITY.get(raw.source_type, 0.70),
            )
            db.add(fin_stmt)
            await db.flush()

        # Delete old line items for this statement before re-inserting
        from sqlalchemy import delete
        await db.execute(
            delete(FinancialLineItem).where(
                FinancialLineItem.statement_id == fin_stmt.statement_id
            )
        )

        # Insert normalized line items
        for item in normalized:
            li = FinancialLineItem(
                statement_id=fin_stmt.statement_id,
                company_id=company.company_id,
                fiscal_year=raw.fiscal_year,
                original_label=item["original_label"],
                standardized_label=item.get("standardized_label"),
                label_category=item.get("category"),
                label_subcategory=item.get("subcategory"),
                parent_label=item.get("parent_label"),
                hierarchy_level=item.get("hierarchy_level", 1),
                sort_order=item.get("sort_order"),
                reported_value=item.get("reported_value"),
                reported_unit=raw.reporting_unit,
                reported_currency=raw.reporting_currency,
                std_value_inr_cr=item.get("std_value_inr_cr"),
                is_derived=item.get("is_derived", False),
                derivation_formula=item.get("derivation_formula"),
                mapping_confidence=item.get("mapping_confidence"),
                is_estimated=item.get("is_estimated", False),
            )
            db.add(li)

    async def _compute_quality_audit(self, db, company: Company) -> None:
        """Compute and persist data quality audit for all years of a company."""
        from sqlalchemy import select, func as sqlfunc

        years_result = await db.execute(
            select(FinancialStatement.fiscal_year)
            .where(FinancialStatement.company_id == company.company_id)
            .distinct()
        )
        years = [row[0] for row in years_result]

        REQUIRED_LABELS = {
            "revenue", "ebitda", "pat",
            "total_assets", "total_equity",
        }

        for year in years:
            items_result = await db.execute(
                select(FinancialLineItem)
                .where(
                    FinancialLineItem.company_id == company.company_id,
                    FinancialLineItem.fiscal_year == year,
                )
            )
            items = items_result.scalars().all()

            if not items:
                continue

            mapped = [i for i in items if i.standardized_label]
            derived = [i for i in items if i.is_derived]
            present_labels = {i.standardized_label for i in mapped if i.standardized_label}
            missing = list(REQUIRED_LABELS - present_labels)

            avg_conf = sum(
                float(i.mapping_confidence) for i in mapped if i.mapping_confidence
            ) / max(len(mapped), 1)

            quality_score = min(
                1.0,
                (len(mapped) / max(len(items), 1)) * 0.4 +
                avg_conf * 0.4 +
                (1.0 - len(missing) / max(len(REQUIRED_LABELS), 1)) * 0.2,
            )

            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(DataQualityAudit.__table__).values(
                company_id=company.company_id,
                fiscal_year=year,
                statement_type="income_statement",
                total_line_items=len(items),
                mapped_line_items=len(mapped),
                derived_items=len(derived),
                missing_items=missing,
                avg_mapping_confidence=round(avg_conf, 3),
                overall_quality_score=round(quality_score, 3),
                has_revenue="revenue" in present_labels,
                has_ebitda="ebitda" in present_labels,
                has_pat="pat" in present_labels,
                has_balance_sheet="total_assets" in present_labels,
            ).on_conflict_do_update(
                index_elements=["company_id", "fiscal_year", "statement_type"],
                set_=dict(
                    total_line_items=len(items),
                    mapped_line_items=len(mapped),
                    derived_items=len(derived),
                    missing_items=missing,
                    avg_mapping_confidence=round(avg_conf, 3),
                    overall_quality_score=round(quality_score, 3),
                    has_revenue="revenue" in present_labels,
                    has_ebitda="ebitda" in present_labels,
                    has_pat="pat" in present_labels,
                ),
            )
            await db.execute(stmt)

    @staticmethod
    async def _update_job(db, job_id: uuid.UUID, **kwargs) -> None:
        from sqlalchemy import update
        await db.execute(
            update(IngestionJob)
            .where(IngestionJob.job_id == job_id)
            .values(**kwargs)
        )

    async def close(self) -> None:
        for p in self._providers.values():
            if hasattr(p, "close"):
                await p.close()
