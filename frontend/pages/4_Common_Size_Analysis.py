"""
Page 4: Common-Size Analysis
- P&L common-size (Revenue = 100%)
- Balance Sheet common-size
- Waterfall chart
- Heatmap across years
"""
from typing import Dict, List, Optional

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Common-Size Analysis", page_icon="📐", layout="wide")

import os
BACKEND = st.session_state.get("backend_url") or os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("📐 Common-Size Analysis")
st.caption("All line items as % of Revenue (Revenue = 100%)")

companies = st.session_state.get("selected_companies", [])
if not companies:
    st.warning("No companies selected.")
    st.stop()

# ── Company & year selector ────────────────────────────────────────────────
col1, col2 = st.columns([2, 2])
with col1:
    company_options = {c["name"]: c for c in companies}
    selected_name = st.selectbox("Company", options=list(company_options.keys()))
    selected_co = company_options[selected_name]

with col2:
    years = st.session_state.get("selected_years", list(range(2015, 2025)))
    display_years = st.multiselect(
        "Fiscal Years",
        options=years,
        default=years[-5:] if len(years) >= 5 else years,
    )

view_type = st.radio(
    "View",
    options=["P&L Statement", "Balance Sheet", "Full Detail"],
    horizontal=True,
)


@st.cache_data(ttl=60)
def fetch_common_size(ticker: str, years_str: str) -> pd.DataFrame:
    """Fetch common-size data from API."""
    try:
        # First get company ID from API
        resp = httpx.get(
            f"{BACKEND}/api/v1/companies/search",
            params={"q": ticker},
            timeout=15,
        )
        resp.raise_for_status()
        co_list = resp.json()
        if not co_list:
            return pd.DataFrame()
        company_id = co_list[0]["company_id"]

        # Fetch common-size
        cs_resp = httpx.get(
            f"{BACKEND}/api/v1/analytics/common-size/{company_id}",
            params={"years": years_str},
            timeout=15,
        )
        cs_resp.raise_for_status()
        data = cs_resp.json()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"API error: {e}")
        return pd.DataFrame()


# P&L metric groupings
PL_METRICS_ORDER = [
    "revenue", "raw_materials_consumed", "purchase_of_stock_in_trade",
    "employee_benefits_expense", "power_and_fuel", "freight_and_logistics",
    "advertising_and_promotion", "professional_fees", "rent_expense",
    "repairs_and_maintenance", "software_expense", "research_and_development",
    "other_operating_expenses", "ebitda", "depreciation_and_amortization",
    "ebit", "finance_costs", "pbt", "tax_expense", "pat",
]

BS_METRICS_ORDER = [
    "total_assets", "property_plant_equipment", "cwip",
    "current_assets", "inventories", "trade_receivables",
    "cash_and_equivalents", "total_equity", "long_term_debt",
    "short_term_debt", "current_liabilities", "trade_payables",
]

LABEL_MAP = {
    "revenue": "Revenue",
    "raw_materials_consumed": "Raw Materials Consumed",
    "purchase_of_stock_in_trade": "Purchase of Stock-in-Trade",
    "employee_benefits_expense": "Employee Benefits",
    "power_and_fuel": "Power & Fuel",
    "freight_and_logistics": "Freight & Logistics",
    "advertising_and_promotion": "Advertising & Promotion",
    "professional_fees": "Professional Fees",
    "rent_expense": "Rent",
    "repairs_and_maintenance": "Repairs & Maintenance",
    "software_expense": "Software Expenses",
    "research_and_development": "R&D",
    "other_operating_expenses": "Other Operating Expenses",
    "ebitda": "EBITDA",
    "depreciation_and_amortization": "Depreciation & Amortization",
    "ebit": "EBIT",
    "finance_costs": "Finance Costs",
    "pbt": "PBT",
    "tax_expense": "Tax",
    "pat": "PAT",
    "total_assets": "Total Assets",
    "property_plant_equipment": "Property Plant & Equipment",
    "cwip": "Capital WIP",
    "current_assets": "Current Assets",
    "inventories": "Inventories",
    "trade_receivables": "Trade Receivables",
    "cash_and_equivalents": "Cash & Equivalents",
    "total_equity": "Total Equity",
    "long_term_debt": "Long-Term Debt",
    "short_term_debt": "Short-Term Debt",
    "current_liabilities": "Current Liabilities",
    "trade_payables": "Trade Payables",
}

if display_years:
    years_str = ",".join(str(y) for y in display_years)

    with st.spinner("Loading common-size data..."):
        df = fetch_common_size(selected_co["ticker"], years_str)

    if df.empty:
        st.warning("No data available. Please run Data Ingestion first.")
        st.stop()

    # Select metrics based on view
    if view_type == "P&L Statement":
        metrics_order = PL_METRICS_ORDER
    elif view_type == "Balance Sheet":
        metrics_order = BS_METRICS_ORDER
    else:
        metrics_order = df["metric_name"].unique().tolist()

    # Filter and pivot
    df_filtered = df[df["metric_name"].isin(metrics_order)].copy()
    if df_filtered.empty:
        st.warning("No matching metrics found in data.")
        st.stop()

    df_filtered["display_label"] = df_filtered["metric_name"].map(
        lambda x: LABEL_MAP.get(x, x.replace("_", " ").title())
    )

    pivot = df_filtered.pivot_table(
        index="display_label",
        columns="fiscal_year",
        values="common_size_pct",
        aggfunc="first",
    )

    # Reorder rows
    ordered_labels = [
        LABEL_MAP.get(m, m.replace("_", " ").title())
        for m in metrics_order
        if LABEL_MAP.get(m, m.replace("_", " ").title()) in pivot.index
    ]
    pivot = pivot.reindex(ordered_labels)

    # ── Heatmap ───────────────────────────────────────────────────────────
    st.subheader(f"Common-Size {view_type} — {selected_name}")

    fig_heat = px.imshow(
        pivot.astype(float).fillna(0),
        color_continuous_scale=[
            [0.0, "#f7fbff"], [0.3, "#6baed6"], [0.6, "#2171b5"], [1.0, "#08306b"]
        ],
        aspect="auto",
        text_auto=".1f",
        labels={"color": "% of Revenue"},
        title=f"{selected_name} — Common-Size Heatmap",
    )
    fig_heat.update_layout(height=max(400, len(pivot) * 28))
    st.plotly_chart(fig_heat, use_container_width=True)

    # ── Table ─────────────────────────────────────────────────────────────
    st.subheader("Detailed Table (%)")
    formatted = pivot.copy()
    for col in formatted.columns:
        formatted[col] = formatted[col].apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "—"
        )
    st.dataframe(formatted, use_container_width=True, height=min(600, len(pivot) * 36 + 40))

    # ── Waterfall for latest year ─────────────────────────────────────────
    if view_type == "P&L Statement" and len(pivot.columns) > 0:
        st.subheader(f"Cost Waterfall — FY{max(display_years)}")
        latest_col = max(display_years)
        if latest_col in pivot.columns:
            waterfall_data = pivot[latest_col].dropna()
            cost_items = [
                m for m in waterfall_data.index
                if m not in ("Revenue", "EBITDA", "EBIT", "PBT", "PAT")
            ]
            cost_vals = waterfall_data[cost_items]

            fig_wf = go.Figure(go.Waterfall(
                name="Cost Waterfall",
                orientation="v",
                measure=["absolute"] + ["relative"] * (len(cost_vals)) + ["total"],
                x=["Revenue"] + list(cost_vals.index) + ["PAT"],
                y=[100] + [-v for v in cost_vals.values] + [None],
                connector={"line": {"color": "rgb(63, 63, 63)"}},
                decreasing={"marker": {"color": "#d62728"}},
                increasing={"marker": {"color": "#2ca02c"}},
                totals={"marker": {"color": "#1F3864"}},
            ))
            fig_wf.update_layout(
                title=f"Revenue Waterfall FY{latest_col} (% of Revenue)",
                height=500,
                showlegend=False,
            )
            st.plotly_chart(fig_wf, use_container_width=True)

    # ── Trend lines ───────────────────────────────────────────────────────
    st.subheader("Key Metric Trends")
    key_metrics = ["ebitda", "pat", "employee_benefits_expense", "raw_materials_consumed"]
    trend_df = df[df["metric_name"].isin(key_metrics)].copy()
    if not trend_df.empty:
        trend_df["label"] = trend_df["metric_name"].map(LABEL_MAP)
        fig_trend = px.line(
            trend_df,
            x="fiscal_year",
            y="common_size_pct",
            color="label",
            markers=True,
            title="Key Metrics Trend (% of Revenue)",
            labels={"common_size_pct": "% of Revenue", "fiscal_year": "Fiscal Year"},
        )
        fig_trend.update_layout(height=400)
        st.plotly_chart(fig_trend, use_container_width=True)
