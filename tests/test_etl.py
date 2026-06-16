"""
Tests for ETL providers and orchestrator.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.etl.base_provider import RawLineItem, RawStatement, CompanySearchResult
from app.etl.screener_provider import ScreenerProvider


class TestScreenerProvider:

    def test_extract_fiscal_year_march(self):
        assert ScreenerProvider._extract_fiscal_year("Mar 2024") == 2024
        assert ScreenerProvider._extract_fiscal_year("Mar 2019") == 2019
        assert ScreenerProvider._extract_fiscal_year("2024") == 2024

    def test_extract_fiscal_year_invalid(self):
        assert ScreenerProvider._extract_fiscal_year("N/A") is None
        assert ScreenerProvider._extract_fiscal_year("") is None

    def test_parse_number_positive(self):
        assert ScreenerProvider._parse_number("10,234.56") == pytest.approx(10234.56)
        assert ScreenerProvider._parse_number("1000") == pytest.approx(1000.0)

    def test_parse_number_negative_brackets(self):
        """Screener uses (value) for negative numbers."""
        assert ScreenerProvider._parse_number("(500.0)") == pytest.approx(-500.0)
        assert ScreenerProvider._parse_number("(1,234)") == pytest.approx(-1234.0)

    def test_parse_number_null_cases(self):
        assert ScreenerProvider._parse_number("-") is None
        assert ScreenerProvider._parse_number("") is None
        assert ScreenerProvider._parse_number("N/A") is None
        assert ScreenerProvider._parse_number(None) is None


class TestRateLimiter:

    @pytest.mark.asyncio
    async def test_rate_limiter_enforces_minimum_interval(self):
        """Rate limiter must delay calls to enforce RPS limit."""
        import time
        from app.etl.base_provider import RateLimiter

        limiter = RateLimiter(rps=10.0)  # 10 RPS = 100ms min interval
        t0 = time.monotonic()
        await limiter.wait()
        await limiter.wait()
        elapsed = time.monotonic() - t0
        # Should have waited at least one interval
        assert elapsed >= 0.08  # 80ms leeway for timing jitter


class TestRawStatement:

    def test_raw_statement_construction(self):
        stmt = RawStatement(
            company_ticker="TCS",
            fiscal_year=2024,
            statement_type="income_statement",
            line_items=[
                RawLineItem("Revenue from Operations", 236000.0),
                RawLineItem("Employee Benefits Expense", 128000.0),
            ],
        )
        assert stmt.company_ticker == "TCS"
        assert stmt.fiscal_year == 2024
        assert len(stmt.line_items) == 2
        assert stmt.consolidation == "consolidated"

    def test_raw_line_item_defaults(self):
        item = RawLineItem(original_label="Revenue", reported_value=1000.0)
        assert item.reported_unit == "crores"
        assert item.reported_currency == "INR"
        assert item.hierarchy_level == 1
        assert item.parent_label is None


class TestProviderUnitConversion:

    def test_crores_identity(self):
        from app.etl.base_provider import BaseProvider

        class ConcreteProvider(BaseProvider):
            provider_name = "test"
            async def search_company(self, q): return []
            async def get_financial_statements(self, t, y, s=None): return []
            async def get_top_companies_by_sector(self, s, limit=100): return []

        p = ConcreteProvider()
        assert p._normalize_unit(100.0, "crores") == pytest.approx(100.0)
        assert p._normalize_unit(100.0, "lakhs") == pytest.approx(1.0)
        assert p._normalize_unit(10.0, "millions") == pytest.approx(1.0)
        assert p._normalize_unit(1.0, "billions") == pytest.approx(100.0)


class TestCompanySearchResult:

    def test_search_result_construction(self):
        r = CompanySearchResult(
            ticker="TCS",
            name="Tata Consultancy Services",
            exchange="NSE",
            nse_ticker="TCS",
            sector="IT Services",
            market_cap_inr_cr=1_400_000.0,
        )
        assert r.ticker == "TCS"
        assert r.market_cap_inr_cr == pytest.approx(1_400_000.0)
