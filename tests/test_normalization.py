"""
Tests for the financial label normalization engine.
"""
import pytest
from app.services.normalization import NormalizationEngine
from app.etl.base_provider import RawLineItem, RawStatement


@pytest.fixture
def engine():
    return NormalizationEngine(fuzzy_threshold=80)


class TestLabelMapping:

    def test_exact_revenue_labels(self, engine):
        """Common Indian revenue labels must map to 'revenue'."""
        cases = [
            "Revenue from Operations",
            "Net Sales",
            "Sales",
            "Turnover",
            "Revenue From Operations",
        ]
        for label in cases:
            result = engine._map_label(label)
            assert result["standardized_label"] == "revenue", (
                f"Expected 'revenue' for label '{label}', got '{result['standardized_label']}'"
            )
            assert result["confidence"] >= 0.85

    def test_employee_cost_variants(self, engine):
        cases = [
            "Employee Benefits Expense",
            "Employee Benefits",
            "Staff Costs",
        ]
        for label in cases:
            result = engine._map_label(label)
            assert result["standardized_label"] in (
                "employee_benefits_expense", "salaries_and_wages"
            ), f"Failed for: {label}"

    def test_depreciation_variants(self, engine):
        cases = [
            "Depreciation and Amortisation Expense",
            "Depreciation and Amortization",
            "Depreciation",
        ]
        for label in cases:
            result = engine._map_label(label)
            assert result["standardized_label"] == "depreciation_and_amortization", (
                f"Failed for: {label}"
            )

    def test_unmapped_labels_preserved(self, engine):
        """Labels that don't match anything must return None standardized but not be discarded."""
        result = engine._map_label("Some Very Unusual Custom Line Item XYZ123")
        assert result["standardized_label"] is None
        assert result["confidence"] == 0.0

    def test_confidence_range(self, engine):
        """All confidence scores must be between 0 and 1."""
        labels = [
            "Revenue from Operations",
            "Profit Before Tax",
            "Unknown Label ABC",
            "Salaries & Wages",
            "Power and Fuel",
        ]
        for label in labels:
            result = engine._map_label(label)
            assert 0.0 <= result["confidence"] <= 1.0, (
                f"Confidence out of range for: {label}"
            )


class TestUnitConversion:

    def test_crores_passthrough(self):
        engine = NormalizationEngine()
        assert engine._to_inr_crores(100.0, "crores", "INR") == pytest.approx(100.0)
        assert engine._to_inr_crores(100.0, "cr", "INR") == pytest.approx(100.0)

    def test_lakhs_to_crores(self):
        engine = NormalizationEngine()
        # 100 lakhs = 1 crore
        assert engine._to_inr_crores(100.0, "lakhs", "INR") == pytest.approx(1.0)

    def test_millions_to_crores(self):
        engine = NormalizationEngine()
        # 10 million INR = 1 crore
        assert engine._to_inr_crores(10.0, "millions", "INR") == pytest.approx(1.0)

    def test_case_insensitive_unit(self):
        engine = NormalizationEngine()
        assert engine._to_inr_crores(50.0, "CRORES", "INR") == pytest.approx(50.0)
        assert engine._to_inr_crores(50.0, "Crore", "INR") == pytest.approx(50.0)


class TestDerivedMetrics:

    def test_ebitda_derivation_from_ebit_da(self):
        """EBITDA = EBIT + D&A when EBITDA not present."""
        engine = NormalizationEngine()
        values = {"ebit": 1000.0, "depreciation_and_amortization": 200.0}
        derived = engine._compute_derived(values)
        ebitda = next((d for d in derived if d["standardized_label"] == "ebitda"), None)
        assert ebitda is not None
        assert ebitda["std_value_inr_cr"] == pytest.approx(1200.0)
        assert ebitda["is_derived"] is True

    def test_ebitda_not_derived_when_present(self):
        """If EBITDA is already present in source, don't re-derive."""
        engine = NormalizationEngine()
        values = {
            "ebitda": 1500.0,
            "ebit": 1000.0,
            "depreciation_and_amortization": 200.0,
        }
        derived = engine._compute_derived(values)
        ebitda_derived = [d for d in derived if d["standardized_label"] == "ebitda"]
        assert len(ebitda_derived) == 0

    def test_working_capital_derivation(self):
        engine = NormalizationEngine()
        values = {"current_assets": 5000.0, "current_liabilities": 3000.0}
        derived = engine._compute_derived(values)
        wc = next((d for d in derived if d["standardized_label"] == "working_capital"), None)
        assert wc is not None
        assert wc["std_value_inr_cr"] == pytest.approx(2000.0)

    def test_derivation_skipped_when_inputs_missing(self):
        engine = NormalizationEngine()
        # Only EBIT present, no D&A → can't derive EBITDA via this route
        values = {"ebit": 1000.0}
        derived = engine._compute_derived(values)
        # Should NOT derive EBITDA from EBIT alone (needs D&A)
        ebitda = [d for d in derived if d["standardized_label"] == "ebitda"]
        assert len(ebitda) == 0


class TestNormalizeStatement:

    @pytest.mark.asyncio
    async def test_normalize_preserves_all_items(self):
        """Every line item must be preserved — even unmapped ones."""
        engine = NormalizationEngine()
        raw = RawStatement(
            company_ticker="TEST",
            fiscal_year=2024,
            statement_type="income_statement",
            line_items=[
                RawLineItem("Revenue from Operations", 10000.0),
                RawLineItem("Employee Benefits Expense", 3000.0),
                RawLineItem("Totally Unknown Custom Item XYZ", 500.0),
                RawLineItem("Profit After Tax", 1200.0),
            ],
        )
        normalized = await engine.normalize(raw)
        assert len([n for n in normalized if not n["is_derived"]]) == 4

    @pytest.mark.asyncio
    async def test_normalize_adds_derived_metrics(self):
        engine = NormalizationEngine()
        raw = RawStatement(
            company_ticker="TEST",
            fiscal_year=2024,
            statement_type="income_statement",
            line_items=[
                RawLineItem("Revenue from Operations", 10000.0),
                RawLineItem("Profit Before Tax", 2000.0),
                RawLineItem("Finance Costs", 300.0),
                RawLineItem("Depreciation and Amortisation", 500.0),
            ],
        )
        normalized = await engine.normalize(raw)
        standardized_labels = [n["standardized_label"] for n in normalized]
        # EBIT = PBT + Finance Costs should be derived
        assert "ebit" in standardized_labels or "ebitda" in standardized_labels


class TestCommonSizeComputation:

    def test_revenue_base_found(self):
        """CommonSizeService must correctly identify the revenue denominator."""
        from app.services.analytics import CommonSizeService
        from unittest.mock import MagicMock

        class MockItem:
            def __init__(self, label, value):
                self.standardized_label = label
                self.std_value_inr_cr = value

        svc = CommonSizeService()
        items = [
            MockItem("revenue", 10000.0),
            MockItem("employee_benefits_expense", 3000.0),
            MockItem("ebitda", 2000.0),
        ]
        revenue = svc._find_revenue(items)
        assert revenue == pytest.approx(10000.0)

    def test_revenue_base_none_when_missing(self):
        from app.services.analytics import CommonSizeService

        class MockItem:
            def __init__(self, label, value):
                self.standardized_label = label
                self.std_value_inr_cr = value

        svc = CommonSizeService()
        items = [MockItem("employee_benefits_expense", 3000.0)]
        assert svc._find_revenue(items) is None


class TestPeerGroupAggregates:

    def test_statistics_computation(self):
        """Test that peer group stats match expected values."""
        import statistics
        values = [10.0, 12.0, 11.0, 13.0, 9.0]
        assert statistics.mean(values) == pytest.approx(11.0)
        assert statistics.median(values) == pytest.approx(11.0)
        assert statistics.stdev(values) == pytest.approx(1.581, rel=0.01)

    def test_market_cap_weighted_average(self):
        """Mktcap-weighted avg should weight larger companies more."""
        values = [10.0, 20.0]   # company A, B
        weights = [1000.0, 100.0]  # A has 10x the market cap
        weighted_avg = sum(v * w for v, w in zip(values, weights)) / sum(weights)
        # Should be closer to 10 (company A's value)
        assert weighted_avg < 15.0
        assert weighted_avg > 10.0
