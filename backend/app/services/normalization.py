"""
Financial Label Normalization Engine.

Maps raw reported labels to standardized labels using:
1. Exact match (DB lookup)
2. Fuzzy matching (fuzzywuzzy)
3. Regex patterns
4. Fallback: keep original label with low confidence

Rules:
- NEVER discard a line item
- ALWAYS preserve the original label
- Track confidence score per mapping
- Derive missing computed metrics (EBITDA, EBIT, etc.)
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fuzzywuzzy import fuzz, process

from app.etl.base_provider import RawLineItem, RawStatement


# ── Regex patterns for common Indian financial labels ─────────────────────
REGEX_PATTERNS: List[Tuple[re.Pattern, str, str, str, float]] = [
    # (pattern, standardized_label, category, subcategory, confidence)
    (re.compile(r"revenue.*(operation|sales)", re.I), "revenue", "revenue", "operating", 0.95),
    (re.compile(r"(net\s+)?sales", re.I), "revenue", "revenue", "operating", 0.90),
    (re.compile(r"turnover", re.I), "revenue", "revenue", "operating", 0.88),
    (re.compile(r"raw\s+material", re.I), "raw_materials_consumed", "cost", "materials", 0.92),
    (re.compile(r"cost\s+of\s+(material|goods)", re.I), "raw_materials_consumed", "cost", "materials", 0.90),
    (re.compile(r"purchase.*stock.in.trade", re.I), "purchase_of_stock_in_trade", "cost", "materials", 0.93),
    (re.compile(r"employee.*(benefit|cost|expense)", re.I), "employee_benefits_expense", "expense", "employee", 0.93),
    (re.compile(r"staff.*(cost|expense|benefit)", re.I), "employee_benefits_expense", "expense", "employee", 0.90),
    (re.compile(r"(salaries|wages).*(bonus)?", re.I), "salaries_and_wages", "expense", "employee", 0.92),
    (re.compile(r"(power|fuel|electricity)", re.I), "power_and_fuel", "expense", "manufacturing", 0.90),
    (re.compile(r"repair.*maintenance", re.I), "repairs_and_maintenance", "expense", "manufacturing", 0.90),
    (re.compile(r"freight|logistics|shipping", re.I), "freight_and_logistics", "expense", "logistics", 0.88),
    (re.compile(r"adverti(se|sing)|promotion|marketing", re.I), "advertising_and_promotion", "expense", "sga", 0.88),
    (re.compile(r"professional\s+(fee|charge)", re.I), "professional_fees", "expense", "sga", 0.90),
    (re.compile(r"\brent\b", re.I), "rent_expense", "expense", "sga", 0.88),
    (re.compile(r"depreciation|amortis|amortiz", re.I), "depreciation_and_amortization", "expense", "non_cash", 0.93),
    (re.compile(r"finance\s+cost|interest.*expense|borrowing.*cost", re.I), "finance_costs", "expense", "finance", 0.92),
    (re.compile(r"income\s+tax|tax\s+expense", re.I), "tax_expense", "expense", "tax", 0.93),
    (re.compile(r"profit.*(before|loss).*tax", re.I), "pbt", "profit", "pbt", 0.95),
    (re.compile(r"profit.*after\s+tax|profit.*for\s+the\s+year", re.I), "pat", "profit", "pat", 0.95),
    (re.compile(r"ebitda", re.I), "ebitda", "profit", "ebitda", 0.99),
    (re.compile(r"ebit\b", re.I), "ebit", "profit", "ebit", 0.99),
    # Balance sheet
    (re.compile(r"total\s+asset", re.I), "total_assets", "asset", "total", 0.97),
    (re.compile(r"property.*plant.*equipment|fixed\s+asset|ppe\b", re.I), "property_plant_equipment", "asset", "fixed", 0.93),
    (re.compile(r"current\s+asset", re.I), "current_assets", "asset", "current", 0.95),
    (re.compile(r"inventor(y|ies)", re.I), "inventories", "asset", "current", 0.95),
    (re.compile(r"trade\s+receivable|debtors", re.I), "trade_receivables", "asset", "current", 0.93),
    (re.compile(r"cash.*equivalent|bank\s+balance", re.I), "cash_and_equivalents", "asset", "current", 0.93),
    (re.compile(r"total\s+equity|shareholders.*equity|net\s+worth", re.I), "total_equity", "equity", "total", 0.93),
    (re.compile(r"(long.term|non.current).*borrow|long.term\s+debt", re.I), "long_term_debt", "liability", "debt", 0.93),
    (re.compile(r"short.term.*borrow|current.*portion.*debt", re.I), "short_term_debt", "liability", "debt", 0.90),
    (re.compile(r"trade\s+payable|creditors", re.I), "trade_payables", "liability", "current", 0.93),
    (re.compile(r"current\s+liabilit", re.I), "current_liabilities", "liability", "current", 0.93),
    (re.compile(r"total\s+(liabilit|debt.*equity|capital.*employed)", re.I), "total_liabilities", "liability", "total", 0.90),
    # Cash flow
    (re.compile(r"operating\s+activit|cash.*operations", re.I), "cfo", "cashflow", "operating", 0.93),
    (re.compile(r"invest.*activit", re.I), "cfi", "cashflow", "investing", 0.93),
    (re.compile(r"financ.*activit", re.I), "cff", "cashflow", "financing", 0.93),
    (re.compile(r"capital\s+expenditure|capex", re.I), "capex", "cashflow", "investing", 0.93),
    (re.compile(r"(free\s+)?cash\s+flow", re.I), "free_cash_flow", "cashflow", "fcf", 0.90),
]

# Derived metrics computation rules
DERIVATION_RULES: List[Dict[str, Any]] = [
    {
        "output": "ebitda",
        "inputs": ["ebit", "depreciation_and_amortization"],
        "formula": "ebit + depreciation_and_amortization",
        "operator": "add",
    },
    {
        "output": "ebit",
        "inputs": ["pbt", "finance_costs"],
        "formula": "pbt + finance_costs",
        "operator": "add",
    },
    {
        "output": "ebitda",
        "inputs": ["pbt", "finance_costs", "depreciation_and_amortization"],
        "formula": "pbt + finance_costs + depreciation_and_amortization",
        "operator": "add",
    },
    {
        "output": "gross_profit",
        "inputs": ["revenue", "raw_materials_consumed"],
        "formula": "revenue - raw_materials_consumed",
        "operator": "subtract",
    },
    {
        "output": "working_capital",
        "inputs": ["current_assets", "current_liabilities"],
        "formula": "current_assets - current_liabilities",
        "operator": "subtract",
    },
    {
        "output": "total_debt",
        "inputs": ["long_term_debt", "short_term_debt"],
        "formula": "long_term_debt + short_term_debt",
        "operator": "add",
    },
    {
        "output": "net_debt",
        "inputs": ["total_debt", "cash_and_equivalents"],
        "formula": "total_debt - cash_and_equivalents",
        "operator": "subtract",
    },
]


class NormalizationEngine:
    """
    Normalizes raw financial line items to standardized labels.

    Design principles:
    - Never discard a line item, even if unmapped
    - Prefer exact DB match > regex > fuzzy > unmapped
    - Track confidence score for every mapping
    - Compute derived metrics when source data is present
    """

    # In-memory cache of DB mappings (loaded once at startup)
    _mapping_cache: Dict[str, Dict] = {}

    def __init__(self, fuzzy_threshold: int = 80):
        self.fuzzy_threshold = fuzzy_threshold
        self._regex_patterns = REGEX_PATTERNS

    async def load_mappings_from_db(self, db) -> None:
        """Load all label mappings from PostgreSQL into memory cache."""
        from sqlalchemy import select
        from app.models.financial import LabelMapping

        result = await db.execute(select(LabelMapping))
        mappings = result.scalars().all()
        self._mapping_cache = {
            m.original_label_norm: {
                "standardized_label": m.standardized_label,
                "category": m.category,
                "subcategory": m.subcategory,
                "confidence": float(m.confidence_default),
            }
            for m in mappings
        }

    async def normalize(self, raw_stmt: RawStatement) -> List[Dict[str, Any]]:
        """
        Normalize all line items in a raw statement.
        Returns a list of dicts ready for DB insertion.
        """
        results: List[Dict[str, Any]] = []
        normalized_values: Dict[str, float] = {}  # for derivation

        for item in raw_stmt.line_items:
            mapped = self._map_label(item.original_label)

            # Convert to INR crores
            std_value = None
            if item.reported_value is not None:
                std_value = self._to_inr_crores(
                    item.reported_value,
                    item.reported_unit,
                    item.reported_currency,
                )

            if mapped["standardized_label"] and std_value is not None:
                normalized_values[mapped["standardized_label"]] = std_value

            results.append({
                "original_label":     item.original_label,
                "standardized_label": mapped["standardized_label"],
                "category":           mapped["category"],
                "subcategory":        mapped["subcategory"],
                "parent_label":       item.parent_label,
                "hierarchy_level":    item.hierarchy_level,
                "sort_order":         item.sort_order,
                "reported_value":     item.reported_value,
                "std_value_inr_cr":   std_value,
                "mapping_confidence": mapped["confidence"],
                "is_derived":         False,
                "source_note":        item.source_note,
            })

        # Compute derived metrics
        derived = self._compute_derived(normalized_values)
        for d in derived:
            # Only add if not already present from source
            if d["standardized_label"] not in normalized_values:
                results.append(d)
                normalized_values[d["standardized_label"]] = d["std_value_inr_cr"]

        return results

    def _map_label(self, original_label: str) -> Dict[str, Any]:
        """
        Map a raw label to a standardized label.
        Priority: exact DB match → regex → fuzzy → unmapped.
        """
        norm = original_label.lower().strip()
        # Remove common Indian financial noise
        norm = re.sub(r"\(.*?\)", "", norm).strip()
        norm = re.sub(r"\s+", " ", norm)

        # 1. Exact cache hit
        if norm in self._mapping_cache:
            m = self._mapping_cache[norm]
            return {
                "standardized_label": m["standardized_label"],
                "category": m["category"],
                "subcategory": m["subcategory"],
                "confidence": m["confidence"],
            }

        # 2. Regex match
        for pattern, std_label, category, subcategory, conf in self._regex_patterns:
            if pattern.search(norm):
                return {
                    "standardized_label": std_label,
                    "category": category,
                    "subcategory": subcategory,
                    "confidence": conf,
                }

        # 3. Fuzzy match against cache keys
        if self._mapping_cache:
            best_match, score = process.extractOne(
                norm, list(self._mapping_cache.keys()), scorer=fuzz.token_sort_ratio
            ) or (None, 0)
            if score >= self.fuzzy_threshold and best_match:
                m = self._mapping_cache[best_match]
                return {
                    "standardized_label": m["standardized_label"],
                    "category": m["category"],
                    "subcategory": m["subcategory"],
                    "confidence": round(score / 100 * 0.85, 3),  # discount fuzzy
                }

        # 4. No match — keep original label, mark as unmapped
        return {
            "standardized_label": None,
            "category": None,
            "subcategory": None,
            "confidence": 0.0,
        }

    def _compute_derived(self, values: Dict[str, float]) -> List[Dict[str, Any]]:
        """Compute derived financial metrics from available data."""
        derived: List[Dict[str, Any]] = []

        for rule in DERIVATION_RULES:
            if rule["output"] in values:
                continue  # Already present from source
            inputs = {k: values.get(k) for k in rule["inputs"]}
            if any(v is None for v in inputs.values()):
                continue  # Missing inputs

            if rule["operator"] == "add":
                result = sum(inputs.values())
            elif rule["operator"] == "subtract":
                vals = [inputs[k] for k in rule["inputs"]]
                result = vals[0] - sum(vals[1:])
            else:
                continue

            derived.append({
                "original_label":     rule["output"].upper().replace("_", " "),
                "standardized_label": rule["output"],
                "category":           "derived",
                "subcategory":        rule["output"],
                "parent_label":       None,
                "hierarchy_level":    1,
                "sort_order":         999,
                "reported_value":     result,
                "std_value_inr_cr":   result,
                "mapping_confidence": 0.95,
                "is_derived":         True,
                "derivation_formula": rule["formula"],
                "source_note":        f"Derived: {rule['formula']}",
            })

        return derived

    @staticmethod
    def _to_inr_crores(value: float, unit: str, currency: str) -> float:
        """Standardize all monetary values to INR crores."""
        unit_lower = (unit or "crores").lower().strip()
        # Unit conversions to crores
        unit_factor = {
            "crores": 1.0,
            "cr": 1.0,
            "crore": 1.0,
            "lakhs": 0.01,
            "lakh": 0.01,
            "lacs": 0.01,
            "lacs.": 0.01,
            "thousands": 0.0001,
            "millions": 0.1,
            "billion": 100.0,
            "billions": 100.0,
            "rupees": 1e-7,
            "rs.": 1e-7,
        }.get(unit_lower, 1.0)

        # Currency conversions (approximate; production should use live FX)
        if currency and currency.upper() != "INR":
            # FX handling — for non-INR currencies, flag as needing conversion
            # Production: use forex API for historical rates
            currency_factor = 1.0  # placeholder
        else:
            currency_factor = 1.0

        return value * unit_factor * currency_factor
