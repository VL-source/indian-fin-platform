"""
Page 6: Time-Series Analysis
- 10-year trends for any metric
- CAGR, YoY growth, rolling averages
- Volatility comparison
- Multi-company overlays
"""
from typing import List

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Time-Series Analysis", page_icon="📈", layout="wide")

BACKEND = st.session_state.get("backend_url", "http://localhost:8000")

st.title("📈 Time-Series Analysis")
st.caption("10-year trends, CAGR, YoY growth, rolling averages, and volatility analysis")

companies = st.session_state.get("selected_companies", [])
if not companies:
    st.warning("No companies selected.")
    st.stop()


@st.cache_data(ttl=120)
def fetch_ts(company_id: str, ticker: str, metrics_str: str) -> pd.DataFrame:
    try:
        resp = httpx.get(
            f"{BACKEND}/api/v1/analytics/time-series/{company_id}",
            params={"metrics": metrics_str},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data)
        if not df.empty:
            df["ticker"] = ticker
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=120)
def fetch_company_id(ticker: str) -> str:
    try:
        resp = httpx.get(
            f"{BACKEND}/api/v1/companies/search",
            params={"q": ticker},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data[0]["company_id"] if data else None
    except Exception:
        return None


TS_METRICS = [
    "revenue", "ebitda", "pat", "ebit",
    "employee_benefits_expense", "raw_materials_consumed",
    "power_and_fuel", "finance_costs",
    "total_assets", "total_equity", "total_debt",
    "capex", "free_cash_flow",
]

LABEL_MAP = {
    "revenue": "Revenue",
    "ebitda": "EBITDA Margin %",
    "pat": "PAT Margin %",
    "ebit": "EBIT Margin %",
    "employee_benefits_expense": "Employee Cost %",
    "raw_materials_consumed": "Raw Materials %",
    "power_and_fuel": "Power & Fuel %",
    "finance_costs": "Finance Costs %",
    "total_assets": "Total Assets",
    "total_equity": "Equity",
    "total_debt": "Debt",
    "capex": "Capex",
    "free_cash_flow": "Free Cash Flow",
}

# ── Controls ───────────────────────────────────────────────────────────────
col1, col2 = st.columns([2, 2])
with col1:
    selected_metrics = st.multiselect(
        "Metrics",
        options=TS_METRICS,
        default=["ebitda", "pat", "employee_benefits_expense"],
        format_func=lambda x: LABEL_MAP.get(x, x),
    )
with col2:
    selected_companies_ts = st.multiselect(
        "Companies",
        options=[c["ticker"] for c in companies],
        default=[c["ticker"] for c in companies[:3]],
    )

if not selected_metrics or not selected_companies_ts:
    st.info("Select at least one metric and one company.")
    st.stop()

metrics_str = ",".join(selected_metrics)

# Fetch data
all_ts: List[pd.DataFrame] = []
with st.spinner("Loading time-series data..."):
    for ticker in selected_companies_ts:
        cid = fetch_company_id(ticker)
        if cid:
            ts_df = fetch_ts(cid, ticker, metrics_str)
            if not ts_df.empty:
                all_ts.append(ts_df)

if not all_ts:
    st.warning("No time-series data available. Run ingestion first.")
    st.stop()

combined_ts = pd.concat(all_ts, ignore_index=True)

# ── Section: CAGR Summary ────────────────────────────────────────────────
st.subheader("CAGR Summary")

cagr_data = []
for _, row in combined_ts.iterrows():
    if row.get("cagr_pct") is not None:
        cagr_data.append({
            "Ticker": row["ticker"],
            "Metric": LABEL_MAP.get(row["metric_name"], row["metric_name"]),
            "CAGR %": round(float(row["cagr_pct"]), 2),
            "Period": f"FY{row.get('base_year', '?')}–FY{row.get('end_year', '?')}",
            "YoY Growth %": round(float(row["yoy_growth_pct"]), 2) if row.get("yoy_growth_pct") else None,
        })

if cagr_data:
    cagr_df = pd.DataFrame(cagr_data)
    cagr_pivot = cagr_df.pivot_table(
        index="Metric", columns="Ticker", values="CAGR %", aggfunc="first"
    )
    st.dataframe(
        cagr_pivot.style.format("{:.1f}%", na_rep="—").background_gradient(
            cmap="RdYlGn", axis=None
        ),
        use_container_width=True,
    )

# ── Section: Trend Charts ────────────────────────────────────────────────
st.subheader("Trend Charts")

for metric in selected_metrics:
    metric_df = combined_ts[combined_ts["metric_name"] == metric]
    if metric_df.empty:
        continue

    metric_label = LABEL_MAP.get(metric, metric.replace("_", " ").title())

    # Expand annual_values JSON
    rows = []
    for _, row in metric_df.iterrows():
        av = row.get("annual_values")
        if isinstance(av, dict):
            for yr, val in av.items():
                rows.append({"Ticker": row["ticker"], "Year": int(yr), "Value": val})
        elif isinstance(av, str):
            import json
            try:
                av_dict = json.loads(av)
                for yr, val in av_dict.items():
                    rows.append({"Ticker": row["ticker"], "Year": int(yr), "Value": val})
            except Exception:
                pass

    if not rows:
        continue

    trend_df = pd.DataFrame(rows).sort_values("Year")

    fig = px.line(
        trend_df,
        x="Year",
        y="Value",
        color="Ticker",
        markers=True,
        title=f"{metric_label} — Multi-Year Trend",
        labels={"Value": metric_label, "Year": "Fiscal Year"},
    )
    fig.update_layout(height=380, hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

# ── Section: Volatility ──────────────────────────────────────────────────
st.subheader("Margin Volatility (Std Dev)")

vol_data = []
for _, row in combined_ts.iterrows():
    if row.get("volatility_std_dev") is not None:
        vol_data.append({
            "Ticker": row["ticker"],
            "Metric": LABEL_MAP.get(row["metric_name"], row["metric_name"]),
            "Std Dev": round(float(row["volatility_std_dev"]), 2),
        })

if vol_data:
    vol_df = pd.DataFrame(vol_data)
    fig_vol = px.bar(
        vol_df,
        x="Ticker",
        y="Std Dev",
        color="Metric",
        barmode="group",
        title="Margin Volatility — Standard Deviation of Annual Values",
        labels={"Std Dev": "Std Dev (pp)", "Ticker": "Company"},
    )
    fig_vol.update_layout(height=380)
    st.plotly_chart(fig_vol, use_container_width=True)

# ── Section: Rolling Averages ─────────────────────────────────────────────
st.subheader("Rolling Averages")
rolling_data = []
for _, row in combined_ts.iterrows():
    if row.get("rolling_3yr_avg") or row.get("rolling_5yr_avg"):
        rolling_data.append({
            "Ticker": row["ticker"],
            "Metric": LABEL_MAP.get(row["metric_name"], row["metric_name"]),
            "3-Year Avg": round(float(row["rolling_3yr_avg"]), 2) if row.get("rolling_3yr_avg") else None,
            "5-Year Avg": round(float(row["rolling_5yr_avg"]), 2) if row.get("rolling_5yr_avg") else None,
        })

if rolling_data:
    ra_df = pd.DataFrame(rolling_data)
    st.dataframe(
        ra_df.style.format("{:.1f}", na_rep="—"),
        use_container_width=True,
    )
