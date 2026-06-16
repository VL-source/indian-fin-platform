"""
Screener.in data provider.
Screener is one of the most reliable free sources for Indian company financials.
It provides 10+ years of P&L, Balance Sheet, and Cash Flow data.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type
)

from app.config import settings
from app.etl.base_provider import (
    BaseProvider, CompanySearchResult, ProviderReliability,
    RawLineItem, RawStatement,
)


SCREENER_BASE = "https://www.screener.in"

STATEMENT_MAP = {
    "profit-loss":    "income_statement",
    "balance-sheet":  "balance_sheet",
    "cash-flow":      "cash_flow",
}


class ScreenerProvider(BaseProvider):
    """
    Fetches financial data from screener.in via HTML scraping.

    Screener publishes structured HTML tables for P&L, Balance Sheet,
    and Cash Flow going back 10+ years. This provider:
    1. Searches company by name/ticker
    2. Navigates to company page
    3. Scrapes all financial tables with full label preservation
    4. Returns raw, un-normalized data
    """

    provider_name = "screener"
    reliability = ProviderReliability.SCREENER

    def __init__(self):
        super().__init__(rate_limit_rps=settings.screener_rate_limit_rps)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={
                    "User-Agent": settings.nse_user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    async def _get(self, url: str) -> str:
        await self._throttle()
        client = await self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    # ── Public interface ──────────────────────────────────────────────────

    async def search_company(self, query: str) -> List[CompanySearchResult]:
        """Search Screener for a company by name or ticker."""
        url = f"{SCREENER_BASE}/api/company/search/?q={quote(query)}&v=3"
        await self._throttle()
        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data: List[Dict] = resp.json()
        except Exception as e:
            self._log.warning("screener_search_error", query=query, error=str(e))
            return []

        results = []
        for item in data:
            results.append(
                CompanySearchResult(
                    ticker=item.get("symbol", ""),
                    name=item.get("name", ""),
                    exchange="NSE" if item.get("symbol") else "BSE",
                    nse_ticker=item.get("symbol"),
                    isin=item.get("isin"),
                )
            )
        return results

    async def get_top_companies_by_sector(
        self, sector: str, limit: int = 100
    ) -> List[CompanySearchResult]:
        """
        Fetch top companies in a sector by market cap from Screener screeners.
        URL pattern: /screen/raw/?query=Sector+is+{sector}&sort=Market+Cap&order=desc
        """
        query = f"Sector is {sector}"
        url = (
            f"{SCREENER_BASE}/screen/raw/"
            f"?query={quote(query)}&sort=Market+Cap&order=desc&limit={limit}"
        )
        try:
            html = await self._get(url)
        except Exception as e:
            self._log.error("screener_sector_fetch_error", sector=sector, error=str(e))
            return []

        return self._parse_screener_list(html)

    async def get_financial_statements(
        self,
        ticker: str,
        years: List[int],
        statement_types: Optional[List[str]] = None,
    ) -> List[RawStatement]:
        """
        Fetch all available financial statements for a company from Screener.
        """
        # First resolve the Screener company slug
        slug = await self._resolve_slug(ticker)
        if not slug:
            self._log.warning("screener_slug_not_found", ticker=ticker)
            return []

        types_to_fetch = statement_types or list(STATEMENT_MAP.values())
        statements: List[RawStatement] = []

        for screener_tab, stmt_type in STATEMENT_MAP.items():
            if stmt_type not in types_to_fetch:
                continue
            url = f"{SCREENER_BASE}/company/{slug}/consolidated/#{screener_tab}"
            try:
                html = await self._get(url)
                raw_stmts = self._parse_financial_table(
                    html, ticker, stmt_type, screener_tab, url
                )
                # Filter to requested years
                filtered = [s for s in raw_stmts if s.fiscal_year in years]
                statements.extend(filtered)
            except Exception as e:
                self._log.error(
                    "screener_statement_fetch_error",
                    ticker=ticker, tab=screener_tab, error=str(e)
                )

        return statements

    # ── Parsing helpers ───────────────────────────────────────────────────

    async def _resolve_slug(self, ticker: str) -> Optional[str]:
        """Get the Screener URL slug for a ticker."""
        results = await self.search_company(ticker)
        for r in results:
            if r.nse_ticker and r.nse_ticker.upper() == ticker.upper():
                # Screener slug = ticker in many cases
                return ticker.upper()
        # Fallback: try direct URL
        test_url = f"{SCREENER_BASE}/company/{ticker.upper()}/consolidated/"
        try:
            client = await self._get_client()
            resp = await client.head(test_url)
            if resp.status_code == 200:
                return ticker.upper()
        except Exception:
            pass
        return None

    def _parse_financial_table(
        self,
        html: str,
        ticker: str,
        stmt_type: str,
        tab_id: str,
        source_url: str,
    ) -> List[RawStatement]:
        """
        Parse Screener's financial HTML tables.
        Returns one RawStatement per fiscal year found.
        """
        soup = BeautifulSoup(html, "lxml")
        section = soup.find("section", {"id": tab_id})
        if not section:
            return []

        table = section.find("table")
        if not table:
            return []

        rows = table.find_all("tr")
        if not rows:
            return []

        # Header row → fiscal years
        header_row = rows[0]
        headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]

        # Parse fiscal years from headers (e.g. "Mar 2024", "Mar 2023")
        year_map: Dict[int, int] = {}  # col_index → fiscal_year
        for col_idx, h in enumerate(headers[1:], start=1):
            fy = self._extract_fiscal_year(h)
            if fy:
                year_map[col_idx] = fy

        if not year_map:
            return []

        # Build per-year statement shells
        year_to_stmt: Dict[int, RawStatement] = {
            fy: RawStatement(
                company_ticker=ticker,
                fiscal_year=fy,
                statement_type=stmt_type,
                consolidation="consolidated",
                source_type=self.provider_name,
                source_url=source_url,
                source_document=f"Screener.in — {ticker} — {stmt_type}",
                source_confidence=float(self.reliability),
            )
            for fy in year_map.values()
        }

        # Data rows
        sort_order = 0
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            label = cells[0].get_text(strip=True)
            if not label or label in ("", "+"):
                continue

            # Detect hierarchy from row class
            row_class = row.get("class", [])
            is_sub = "sub" in " ".join(row_class).lower()
            hierarchy_level = 2 if is_sub else 1

            for col_idx, fy in year_map.items():
                if col_idx >= len(cells):
                    continue
                raw_text = cells[col_idx].get_text(strip=True).replace(",", "")
                value = self._parse_number(raw_text)

                year_to_stmt[fy].line_items.append(
                    RawLineItem(
                        original_label=label,
                        reported_value=value,
                        hierarchy_level=hierarchy_level,
                        sort_order=sort_order,
                    )
                )
            sort_order += 1

        return list(year_to_stmt.values())

    def _parse_screener_list(self, html: str) -> List[CompanySearchResult]:
        """Parse Screener screen results page."""
        soup = BeautifulSoup(html, "lxml")
        results = []
        table = soup.find("table", class_="data-table")
        if not table:
            return results

        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link = cells[0].find("a")
            name = link.get_text(strip=True) if link else cells[0].get_text(strip=True)
            href = link["href"] if link else ""
            ticker = href.strip("/").split("/")[-1] if href else ""
            mktcap_text = cells[-1].get_text(strip=True).replace(",", "") if cells else ""
            mktcap = self._parse_number(mktcap_text)
            results.append(
                CompanySearchResult(
                    ticker=ticker,
                    name=name,
                    exchange="NSE",
                    nse_ticker=ticker,
                    market_cap_inr_cr=mktcap,
                )
            )
        return results

    @staticmethod
    def _extract_fiscal_year(header: str) -> Optional[int]:
        """
        Extract fiscal year integer from header like "Mar 2024" → 2024.
        Screener uses March year-end (Indian FY).
        """
        match = re.search(r"(\d{4})", header)
        return int(match.group(1)) if match else None

    @staticmethod
    def _parse_number(text: str) -> Optional[float]:
        """Parse numeric string, handling negatives in parentheses."""
        if not text or text in ("-", "—", "NA", "N/A", ""):
            return None
        text = text.strip()
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            val = float(text.replace(",", ""))
            return -val if negative else val
        except ValueError:
            return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
