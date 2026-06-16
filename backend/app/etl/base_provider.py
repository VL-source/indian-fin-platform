"""
Abstract base class for all financial data providers.
Every provider must implement this interface to be pluggable.
"""
from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class ProviderReliability(float, Enum):
    """Source reliability scores used in data quality calculations."""
    MCA_XBRL          = 0.99
    NSE_FILING        = 0.97
    BSE_FILING        = 0.96
    ANNUAL_REPORT_PDF = 0.94
    SCREENER          = 0.88
    FMP_API           = 0.82
    ALPHA_VANTAGE     = 0.78
    MANUAL            = 0.70
    UNKNOWN           = 0.50


@dataclass
class RawLineItem:
    """Single financial line item as extracted from a source — no normalization."""
    original_label: str
    reported_value: Optional[float]
    reported_unit: str = "crores"
    reported_currency: str = "INR"
    parent_label: Optional[str] = None
    hierarchy_level: int = 1
    sort_order: Optional[int] = None
    source_note: Optional[str] = None


@dataclass
class RawStatement:
    """One financial statement for one company-year from one source."""
    company_ticker: str              # NSE ticker or BSE code
    fiscal_year: int
    statement_type: str              # 'income_statement' | 'balance_sheet' | 'cash_flow' | 'notes'
    consolidation: str = "consolidated"
    reporting_unit: str = "crores"
    reporting_currency: str = "INR"
    line_items: List[RawLineItem] = field(default_factory=list)
    source_type: str = "unknown"
    source_url: Optional[str] = None
    source_document: Optional[str] = None
    source_page_ref: Optional[str] = None
    source_confidence: float = 0.80
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompanySearchResult:
    """Result from a company lookup / search."""
    ticker: str
    name: str
    exchange: str                    # 'NSE' | 'BSE'
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap_inr_cr: Optional[float] = None
    isin: Optional[str] = None
    bse_code: Optional[str] = None
    nse_ticker: Optional[str] = None
    cin: Optional[str] = None


class RateLimiter:
    """Token-bucket rate limiter for provider HTTP calls."""

    def __init__(self, rps: float):
        self.rps = rps
        self._min_interval = 1.0 / rps
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_time = self._min_interval - (now - self._last_call)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_call = time.monotonic()


class BaseProvider(abc.ABC):
    """
    Abstract base for all financial data providers.

    Subclass this and implement the abstract methods.
    The ETL orchestrator calls them in a provider-agnostic way.
    """

    # Override in subclass
    provider_name: str = "base"
    reliability: float = ProviderReliability.UNKNOWN

    def __init__(self, rate_limit_rps: float = 2.0):
        self._rate_limiter = RateLimiter(rate_limit_rps)
        self._log = structlog.get_logger(self.__class__.__name__)

    # ── Required interface ────────────────────────────────────────────────

    @abc.abstractmethod
    async def search_company(self, query: str) -> List[CompanySearchResult]:
        """Search for a company by name, ticker, or ISIN."""

    @abc.abstractmethod
    async def get_financial_statements(
        self,
        ticker: str,
        years: List[int],
        statement_types: Optional[List[str]] = None,
    ) -> List[RawStatement]:
        """
        Fetch financial statements for a company.

        Args:
            ticker: NSE ticker (preferred) or BSE code.
            years: List of fiscal years to fetch (e.g. [2022, 2023, 2024]).
            statement_types: Subset of ['income_statement', 'balance_sheet',
                             'cash_flow', 'notes']. None = all available.

        Returns:
            List of RawStatement objects, one per (year × statement_type).
        """

    @abc.abstractmethod
    async def get_top_companies_by_sector(
        self,
        sector: str,
        limit: int = 100,
    ) -> List[CompanySearchResult]:
        """Return top N companies in a sector by market cap."""

    # ── Optional / default implementations ───────────────────────────────

    async def validate_ticker(self, ticker: str) -> Optional[CompanySearchResult]:
        """
        Check if a ticker is valid and return company metadata.
        Default: calls search_company; subclasses may implement faster paths.
        """
        results = await self.search_company(ticker)
        for r in results:
            if r.nse_ticker == ticker or r.bse_code == ticker:
                return r
        return None

    async def health_check(self) -> bool:
        """Check if the provider is reachable. Default returns True."""
        return True

    async def _throttle(self) -> None:
        """Apply rate limiting before making an HTTP call."""
        await self._rate_limiter.wait()

    def _normalize_unit(self, value: float, unit: str) -> float:
        """Convert to INR crores for standardization."""
        unit_lower = unit.lower().strip()
        conversion = {
            "crores": 1.0,
            "cr": 1.0,
            "crore": 1.0,
            "lakhs": 0.01,
            "lakh": 0.01,
            "lacs": 0.01,
            "thousands": 0.0001,
            "millions": 0.1,           # 1 million INR = 0.1 crore
            "billions": 100.0,         # 1 billion INR = 100 crore
            "rupees": 0.0000001,       # absolute INR → crore
        }
        factor = conversion.get(unit_lower, 1.0)
        return value * factor

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider={self.provider_name}>"
