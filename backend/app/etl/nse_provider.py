"""
NSE India data provider.
Uses NSE's public APIs for company search, market cap data,
and filing metadata. NSE doesn't provide parsed financials directly,
but is the authoritative source for company identity and filing index.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.etl.base_provider import (
    BaseProvider, CompanySearchResult, ProviderReliability,
    RawLineItem, RawStatement,
)


NSE_BASE = "https://www.nseindia.com"
NSE_API  = "https://www.nseindia.com/api"


class NSEProvider(BaseProvider):
    """
    NSE India provider.

    Responsibilities:
    - Company search and identity resolution
    - Market capitalization data (authoritative for NSE-listed companies)
    - Filing index (XBRL/PDF links for annual reports)
    - Sector / industry classification

    Note: NSE blocks direct API calls without a session cookie.
    This provider handles session initialization via a browser-like request
    to the main site before hitting the API endpoints.
    """

    provider_name = "nse"
    reliability = ProviderReliability.NSE_FILING

    _HEADERS = {
        "User-Agent": settings.nse_user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    }

    def __init__(self):
        super().__init__(rate_limit_rps=settings.nse_rate_limit_rps)
        self._client: Optional[httpx.AsyncClient] = None
        self._session_initialized = False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers=self._HEADERS,
                follow_redirects=True,
                cookies=httpx.Cookies(),
            )
            await self._init_session()
        return self._client

    async def _init_session(self) -> None:
        """NSE requires a valid cookie — visit homepage first."""
        if self._session_initialized:
            return
        try:
            await self._throttle()
            await self._client.get(NSE_BASE)
            self._session_initialized = True
            self._log.debug("nse_session_initialized")
        except Exception as e:
            self._log.warning("nse_session_init_failed", error=str(e))

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def _api_get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        await self._throttle()
        client = await self._get_client()
        url = f"{NSE_API}/{endpoint}"
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ── Public interface ──────────────────────────────────────────────────

    async def search_company(self, query: str) -> List[CompanySearchResult]:
        """Search NSE for a company by name or symbol."""
        try:
            data = await self._api_get("search/autocomplete", {"q": query, "type": "equity"})
        except Exception as e:
            self._log.warning("nse_search_error", query=query, error=str(e))
            return []

        results = []
        symbols = data.get("symbols", []) if isinstance(data, dict) else data
        for item in symbols:
            results.append(
                CompanySearchResult(
                    ticker=item.get("symbol", ""),
                    name=item.get("symbol_info", item.get("name", "")),
                    exchange="NSE",
                    nse_ticker=item.get("symbol"),
                    isin=item.get("isin"),
                    sector=item.get("industry"),
                )
            )
        return results

    async def get_company_details(self, ticker: str) -> Optional[CompanySearchResult]:
        """Fetch full company profile from NSE."""
        try:
            data = await self._api_get(f"quote-equity?symbol={quote(ticker.upper())}")
        except Exception as e:
            self._log.warning("nse_company_detail_error", ticker=ticker, error=str(e))
            return None

        info = data.get("info", {})
        mkt = data.get("priceInfo", {})

        # Market cap = last price × issued shares (approximate)
        return CompanySearchResult(
            ticker=ticker.upper(),
            name=info.get("companyName", ticker),
            exchange="NSE",
            nse_ticker=ticker.upper(),
            isin=info.get("isin"),
            sector=info.get("industry"),
            industry=info.get("industry"),
        )

    async def get_market_cap(self, ticker: str) -> Optional[float]:
        """Return market cap in INR crores for a ticker."""
        try:
            data = await self._api_get(
                f"quote-equity?symbol={quote(ticker.upper())}&section=trade_info"
            )
            mktcap = (
                data.get("tradeInfo", {})
                    .get("totalMarketCap", None)
            )
            if mktcap:
                # NSE returns in INR (absolute); convert to crores
                return float(mktcap) / 1e7
        except Exception as e:
            self._log.warning("nse_mktcap_error", ticker=ticker, error=str(e))
        return None

    async def get_top_companies_by_sector(
        self, sector: str, limit: int = 100
    ) -> List[CompanySearchResult]:
        """
        Fetch top companies in a sector from NSE index constituents.
        Uses NSE's equity search with industry filter.
        """
        try:
            # NSE provides industry-filtered equity list
            data = await self._api_get(
                "equity-stockIndices",
                params={"index": sector.upper().replace(" ", "%20")},
            )
            companies_raw = data.get("data", [])
        except Exception:
            # Fallback: use screener for sector lookup
            self._log.warning("nse_sector_fetch_failed", sector=sector)
            return []

        results = []
        for item in companies_raw[:limit]:
            results.append(
                CompanySearchResult(
                    ticker=item.get("symbol", ""),
                    name=item.get("meta", {}).get("companyName", item.get("symbol", "")),
                    exchange="NSE",
                    nse_ticker=item.get("symbol"),
                    sector=sector,
                    market_cap_inr_cr=self._safe_float(
                        item.get("meta", {}).get("totalMarketCap"), divisor=1e7
                    ),
                )
            )

        # Sort by market cap descending
        results.sort(
            key=lambda x: x.market_cap_inr_cr or 0,
            reverse=True,
        )
        return results[:limit]

    async def get_financial_statements(
        self,
        ticker: str,
        years: List[int],
        statement_types: Optional[List[str]] = None,
    ) -> List[RawStatement]:
        """
        NSE doesn't serve parsed financials directly.
        This method returns empty — the orchestrator will try the next provider.
        NSE is used for company identity, market cap, and filing index only.
        """
        return []

    async def get_filing_index(
        self, ticker: str, year: int
    ) -> List[Dict[str, str]]:
        """
        Return list of filing URLs (XBRL, PDF) for a company-year.
        These are used by XBRL and PDF providers downstream.
        """
        try:
            data = await self._api_get(
                "annual-reports",
                params={"symbol": ticker.upper(), "year": year},
            )
            return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            self._log.warning("nse_filing_index_error", ticker=ticker, year=year, error=str(e))
            return []

    @staticmethod
    def _safe_float(value: Any, divisor: float = 1.0) -> Optional[float]:
        try:
            return float(value) / divisor if value is not None else None
        except (TypeError, ValueError):
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
