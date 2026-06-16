"""
Financial Modeling Prep (FMP) API provider.
FMP covers Indian NSE stocks under the symbol format "TCS.NS".
Used as a fallback when Screener/NSE/BSE are unavailable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.etl.base_provider import (
    BaseProvider, CompanySearchResult, ProviderReliability,
    RawLineItem, RawStatement,
)

FMP_BASE = "https://financialmodelingprep.com/api/v3"

# FMP statement type → our standard type
FMP_STMT_MAP = {
    "income-statement":       "income_statement",
    "balance-sheet-statement": "balance_sheet",
    "cash-flow-statement":    "cash_flow",
}


class FMPProvider(BaseProvider):
    """
    Financial Modeling Prep API provider.
    Requires FMP_API_KEY in settings.
    Indian stocks use the .NS suffix (NSE) or .BO suffix (BSE).
    """

    provider_name = "fmp"
    reliability = ProviderReliability.FMP_API

    def __init__(self):
        super().__init__(rate_limit_rps=settings.fmp_rate_limit_rps)
        if not settings.fmp_api_key:
            self._log.warning("fmp_no_api_key")
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers={"User-Agent": settings.nse_user_agent},
            )
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _api_get(self, path: str, params: Optional[Dict] = None) -> Any:
        await self._throttle()
        client = await self._get_client()
        full_params = {"apikey": settings.fmp_api_key, **(params or {})}
        resp = await client.get(f"{FMP_BASE}/{path}", params=full_params)
        resp.raise_for_status()
        return resp.json()

    async def health_check(self) -> bool:
        if not settings.fmp_api_key:
            return False
        try:
            result = await self._api_get("quote/TCS.NS")
            return bool(result)
        except Exception:
            return False

    async def search_company(self, query: str) -> List[CompanySearchResult]:
        try:
            data = await self._api_get("search", {"query": query, "exchange": "NSE", "limit": 20})
        except Exception as e:
            self._log.warning("fmp_search_error", query=query, error=str(e))
            return []

        results = []
        for item in (data or []):
            ticker_raw = item.get("symbol", "")
            nse_ticker = ticker_raw.replace(".NS", "").replace(".BO", "")
            results.append(
                CompanySearchResult(
                    ticker=nse_ticker,
                    name=item.get("name", ""),
                    exchange="NSE",
                    nse_ticker=nse_ticker,
                    isin=item.get("isin"),
                    sector=item.get("sector"),
                    industry=item.get("industry"),
                )
            )
        return results

    async def get_top_companies_by_sector(
        self, sector: str, limit: int = 100
    ) -> List[CompanySearchResult]:
        """FMP doesn't provide sector-sorted lists for NSE; return empty."""
        return []

    async def get_financial_statements(
        self,
        ticker: str,
        years: List[int],
        statement_types: Optional[List[str]] = None,
    ) -> List[RawStatement]:
        if not settings.fmp_api_key:
            return []

        fmp_symbol = f"{ticker.upper()}.NS"
        types_to_fetch = statement_types or list(FMP_STMT_MAP.values())
        all_statements: List[RawStatement] = []

        for fmp_path, stmt_type in FMP_STMT_MAP.items():
            if stmt_type not in types_to_fetch:
                continue
            try:
                data = await self._api_get(
                    f"{fmp_path}/{fmp_symbol}",
                    params={"period": "annual", "limit": 10},
                )
            except Exception as e:
                self._log.warning("fmp_stmt_error", ticker=ticker, type=fmp_path, error=str(e))
                continue

            if not data:
                continue

            for record in data:
                fy = self._extract_fy(record.get("date", ""))
                if fy not in years:
                    continue

                stmt = RawStatement(
                    company_ticker=ticker,
                    fiscal_year=fy,
                    statement_type=stmt_type,
                    consolidation="consolidated",
                    reporting_unit="millions",   # FMP returns USD millions typically
                    reporting_currency=record.get("reportedCurrency", "INR"),
                    source_type=self.provider_name,
                    source_url=f"{FMP_BASE}/{fmp_path}/{fmp_symbol}",
                    source_document=f"FMP API — {fmp_symbol}",
                    source_confidence=float(self.reliability),
                )

                # Map FMP field names → line items
                for field_name, value in record.items():
                    if field_name in ("date", "symbol", "reportedCurrency",
                                      "cik", "fillingDate", "acceptedDate",
                                      "calendarYear", "period", "link", "finalLink"):
                        continue
                    if not isinstance(value, (int, float)):
                        continue
                    label = self._fmp_field_to_label(field_name)
                    stmt.line_items.append(
                        RawLineItem(
                            original_label=label,
                            reported_value=float(value) if value is not None else None,
                            reported_unit=stmt.reporting_unit,
                            reported_currency=stmt.reporting_currency,
                        )
                    )

                all_statements.append(stmt)

        return all_statements

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_fy(date_str: str) -> int:
        """
        Extract fiscal year from FMP date string "2024-03-31".
        For Indian companies ending March, return the year component.
        """
        try:
            return int(date_str[:4])
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def _fmp_field_to_label(field: str) -> str:
        """Convert camelCase FMP field names to human-readable labels."""
        import re
        # Insert spaces before capitals
        label = re.sub(r"([A-Z])", r" \1", field).strip()
        return label.title()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
