"""
Page 5: Peer Group Benchmarks
- Compare all companies in the group for a given year
- Show peer avg, median, std dev, percentiles
- Box plots, bar charts, scatter plots
"""
from typing import List, Optional

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Peer Benchmarks", page_icon="🔬", layout="wide")

import os
BACKEND = st.session_state.get("backend_url") or os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("🔬 Peer Group Benchmarks")
st.caption("Side-by-side comparison of all companies in the peer group")

companies = st.session_state.get("selected_companies", [])
peer_group_id = st.session_state.get("peer_group_id")

if not companies:
    st.warning("No companies selected.")
    st.stop()


@st.cache_data(ttl=120)
def fetch_peer_compare(company_ids_str: str, fiscal_year: int, metrics_str: str) -> pd.DataFrame:
    """Fetch common-size data for all companies in peer group for one year."""
    try:
        # If peer_group_id exists, use the optimized endpoint
        resp = httpx.get(
            f"{BACKEND}/api/v1/analytics/common-size/compare/peer-group/{peer_group_id}",
            params={"fiscal_year": fiscal_year, "metrics": metrics_str},
            timeout=20,
        )
        resp.raise_for_status()
        return pd.DataFrame(resp.json())
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=120)
def fetch_peer_agg(peer_group_id: str, fiscal_year: int) -> pd.DataFrame:
    """Fetch peer-group aggregate statistics."""
    try:
        resp = httpx.get(
            f"{BACKEND}/api/v1/analytics/peer-group/{peer_group_id}",
            params={"years": str(fiscal_year)},
            timeout=20,
        )
        resp.raise_for_status()
        return pd.DataFrame(resp.json())
    except Exception:
        return pd.DataFrame()


# ── Controls ───────────────────────────────────────────────────────────────
years = st.session_state.get("selected_years", list(range(2015, 2025)))
col1, col2 = st.columns([2, 2])
with col1:
    fiscal_year = st.selectbox("Fiscal Year", options=sorted(years, reverse=True))
with col2:
    KEY_METRICS = [
        "revenue", "ebitda", "pat", "employee_benefits_expense",
        "raw_materials_consumed", "power_and_fuel", "freight_and_logistics",
        "advertising_and_promotion", "finance_costs", "total_assets",
        "total_equity", "total_debt",
    ]
    selected_metrics = st.multiselect(
        "Metrics to Compare",
        options=KEY_METRICS,
        default=["ebitda", "pat", "employee_benefits_expense", "raw_materials_consumed"],
    )

LABEL_MAP = {
    "ebitda": "EBITDA %",
    "pat": "PAT %",
    "employee_benefits_expense": "Employee Cost %",
    "raw_materials_consumed": "Raw Materials %",
    "power_and_fuel": "Power & Fuel %",
    "freight_and_logistics": "Freight %",
    "advertising_and_promotion": "Advertising %",
    "finance_costs": "Finance Costs %",
    "revenue": "Revenue (₹ Cr)",
    "total_assets": "Total Assets (₹ Cr)",
    "total_equity": "Equity (₹ Cr)",
    "total_debt": "Debt (₹ Cr)",
}

if not peer_group_id:
    st.info(
        "💡 Create a peer group first via the API or run ingestion to enable aggregate statistics. "
        "Showing individual company comparisons."
    )

if selected_metrics and fiscal_year:
    metrics_str = ",".join(selected_metrics)
    company_ids_str = ",".join(c.get("company_id", c["ticker"]) for c in companies)

    with st.spinner("Loading benchmark data..."):
        df_compare = fetch_peer_compare(company_ids_str, fiscal_year, metrics_str)

    if df_compare.empty:
        st.warning("No data available for this selection. Ensure ingestion has completed.")
        st.stop()

    # ── Bar Chart Comparison ───────────────────────────────────────────────
    for metric in selected_metrics:
        metric_df = df_compare[df_compare["metric_name"] == metric].copy()
        if metric_df.empty:
            continue

        metric_df = metric_df.sort_values("common_size_pct", ascending=False)
        metric_label = LABEL_MAP.get(metric, metric.replace("_", " ").title())

        fig = px.bar(
            metric_df,
            x="company_name",
            y="common_size_pct",
            color="common_size_pct",
            color_continuous_scale="Blues",
            title=f"{metric_label} — FY{fiscal_year}",
            labels={
                "common_size_pct": "% of Revenue",
                "company_name": "Company",
            },
            text_auto=".1f",
        )
        fig.update_layout(
            showlegend=False,
            height=350,
            xaxis_tickangle=-30,
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Summary Table ──────────────────────────────────────────────────────
    st.subheader("Summary Table")
    pivot = df_compare.pivot_table(
        index="company_name",
        columns="metric_name",
        values="common_size_pct",
        aggfunc="first",
    )
    pivot.columns = [LABEL_MAP.get(c, c) for c in pivot.columns]
    st.dataframe(
        pivot.style.format("{:.1f}%", na_rep="—").background_gradient(
            cmap="Blues", axis=0
        ),
        use_container_width=True,
    )

    # ── Box plots ──────────────────────────────────────────────────────────
    st.subheader("Distribution (Box Plot)")
    fig_box = go.Figure()
    for metric in selected_metrics:
        metric_df = df_compare[df_compare["metric_name"] == metric]
        if metric_df.empty:
            continue
        label = LABEL_MAP.get(metric, metric.replace("_", " ").title())
        fig_box.add_trace(go.Box(
            y=metric_df["common_size_pct"],
            name=label,
            boxmean="sd",
            marker_color="#1F3864",
        ))
    fig_box.update_layout(
        title=f"Distribution of Key Metrics — FY{fiscal_year}",
        height=450,
        yaxis_title="% of Revenue",
    )
    st.plotly_chart(fig_box, use_container_width=True)

    # ── Peer Aggregate Statistics ──────────────────────────────────────────
    if peer_group_id:
        st.subheader("Peer Group Aggregate Statistics")
        df_agg = fetch_peer_agg(peer_group_id, fiscal_year)
        if not df_agg.empty:
            df_agg_filtered = df_agg[df_agg["metric_name"].isin(selected_metrics)].copy()
            df_agg_filtered["metric_name"] = df_agg_filtered["metric_name"].map(
                lambda x: LABEL_MAP.get(x, x)
            )
            display_cols = [
                "metric_name", "equal_weight_avg", "mktcap_weight_avg",
                "median_val", "std_dev", "p25", "p75", "count_companies",
            ]
            display_cols = [c for c in display_cols if c in df_agg_filtered.columns]
            st.dataframe(
                df_agg_filtered[display_cols].rename(columns={
                    "metric_name": "Metric",
                    "equal_weight_avg": "Equal-Wt Avg",
                    "mktcap_weight_avg": "MktCap-Wt Avg",
                    "median_val": "Median",
                    "std_dev": "Std Dev",
                    "p25": "P25",
                    "p75": "P75",
                    "count_companies": "# Companies",
                }).style.format("{:.1f}", subset=pd.IndexSlice[:, ["Equal-Wt Avg", "MktCap-Wt Avg", "Median", "Std Dev", "P25", "P75"]], na_rep="—"),
                use_container_width=True,
            )
