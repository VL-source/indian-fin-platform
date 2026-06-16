"""
Page 7: Product Mix & Export Intensity
STRICT RULE: Only data from official filings displayed.
No estimation. Source reference shown for every data point.
"""
import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Product Mix & Exports", page_icon="🌍", layout="wide")

import os
BACKEND = st.session_state.get("backend_url") or os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("🌍 Product Mix & Export Intensity")
st.warning(
    "⚠️ **Data Source Policy:** Only data explicitly disclosed in official company filings "
    "(Annual Reports, NSE/BSE filings, MCA XBRL, Investor Presentations) is shown here. "
    "No estimations, no analyst reports, no external databases.",
    icon="📋",
)

companies = st.session_state.get("selected_companies", [])
if not companies:
    st.warning("No companies selected.")
    st.stop()

tab1, tab2 = st.tabs(["📦 Product Mix / Segments", "🌐 Export Intensity"])

# ── Tab 1: Product Mix ─────────────────────────────────────────────────────
with tab1:
    st.subheader("Segment Revenue Breakdown")
    st.caption(
        "Sourced exclusively from Segment Reporting Notes in Annual Reports. "
        "Segment definitions are preserved exactly as reported — no normalization across companies."
    )

    col1, col2 = st.columns([2, 2])
    with col1:
        company_options = {c["name"]: c for c in companies}
        selected_name = st.selectbox("Company", options=list(company_options.keys()), key="pm_co")
        selected_co = company_options[selected_name]

    years = st.session_state.get("selected_years", list(range(2015, 2025)))
    with col2:
        selected_year = st.selectbox(
            "Fiscal Year", options=sorted(years, reverse=True), key="pm_year"
        )

    @st.cache_data(ttl=120)
    def fetch_product_mix(company_id: str, year: int) -> list:
        try:
            resp = httpx.get(
                f"{BACKEND}/api/v1/analytics/product-mix/{company_id}",
                params={"fiscal_year": year},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return []

    @st.cache_data(ttl=30)
    def get_company_id(ticker: str) -> str:
        try:
            resp = httpx.get(f"{BACKEND}/api/v1/companies/search", params={"q": ticker}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data[0]["company_id"] if data else None
        except Exception:
            return None

    cid = get_company_id(selected_co["ticker"])
    if cid:
        segments = fetch_product_mix(cid, selected_year)
    else:
        segments = []

    if segments:
        seg_df = pd.DataFrame(segments)

        # Pie chart
        if "segment_name" in seg_df.columns and "revenue_share_pct" in seg_df.columns:
            fig_pie = px.pie(
                seg_df,
                names="segment_name",
                values="revenue_share_pct",
                title=f"{selected_name} — Segment Mix FY{selected_year}",
                hole=0.4,
            )
            fig_pie.update_traces(
                textposition="inside",
                textinfo="percent+label",
            )
            fig_pie.update_layout(height=450)
            st.plotly_chart(fig_pie, use_container_width=True)

        # Detail table with source references
        st.subheader("Segment Data with Source References")
        display_cols = [
            c for c in [
                "segment_name", "revenue_share_pct",
                "segment_revenue_inr_cr", "ebit_margin_pct",
                "source_document", "source_page_ref", "source_type"
            ] if c in seg_df.columns
        ]
        st.dataframe(
            seg_df[display_cols].rename(columns={
                "segment_name": "Segment (as reported)",
                "revenue_share_pct": "Revenue Share %",
                "segment_revenue_inr_cr": "Revenue (₹ Cr)",
                "ebit_margin_pct": "EBIT Margin %",
                "source_document": "Source Document",
                "source_page_ref": "Page/Section Reference",
                "source_type": "Source Type",
            }),
            use_container_width=True,
        )
    else:
        st.info(
            f"No segment data found for {selected_name} FY{selected_year}. "
            "This may mean the company does not report segment-level data, "
            "or data has not been extracted yet from the annual report."
        )

    # Multi-year segment trend
    if cid:
        st.subheader("Segment Mix Trend (Multi-Year)")
        all_segments = []
        for yr in sorted(years):
            yr_segs = fetch_product_mix(cid, yr)
            for s in yr_segs:
                s["fiscal_year"] = yr
                all_segments.append(s)

        if all_segments:
            all_df = pd.DataFrame(all_segments)
            if "segment_name" in all_df.columns and "revenue_share_pct" in all_df.columns:
                fig_trend = px.area(
                    all_df,
                    x="fiscal_year",
                    y="revenue_share_pct",
                    color="segment_name",
                    title=f"{selected_name} — Segment Mix Evolution",
                    labels={
                        "revenue_share_pct": "Revenue Share %",
                        "fiscal_year": "Fiscal Year",
                        "segment_name": "Segment",
                    },
                    groupnorm="percent",
                )
                fig_trend.update_layout(height=400)
                st.plotly_chart(fig_trend, use_container_width=True)

# ── Tab 2: Export Intensity ────────────────────────────────────────────────
with tab2:
    st.subheader("Export Revenue Disclosure")
    st.caption(
        "Export % = Export Revenue / Total Revenue. "
        "Only shown if explicitly disclosed in official filings. "
        "No estimation. Geographic breakdown shown if reported."
    )

    @st.cache_data(ttl=120)
    def fetch_export(company_id: str) -> list:
        try:
            resp = httpx.get(
                f"{BACKEND}/api/v1/analytics/export-intensity/{company_id}",
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return []

    col_a, col_b = st.columns([2, 2])
    with col_a:
        export_co_name = st.selectbox(
            "Company", options=list(company_options.keys()), key="ei_co"
        )
        export_co = company_options[export_co_name]

    export_cid = get_company_id(export_co["ticker"])
    if export_cid:
        export_data = fetch_export(export_cid)
    else:
        export_data = []

    if export_data:
        exp_df = pd.DataFrame(export_data)

        # Trend chart
        if "fiscal_year" in exp_df.columns and "export_pct" in exp_df.columns:
            fig_exp = px.bar(
                exp_df.sort_values("fiscal_year"),
                x="fiscal_year",
                y="export_pct",
                title=f"{export_co_name} — Export Intensity (% of Revenue)",
                labels={"export_pct": "Export %", "fiscal_year": "Fiscal Year"},
                color="export_pct",
                color_continuous_scale="Blues",
                text_auto=".1f",
            )
            fig_exp.update_layout(height=380, coloraxis_showscale=False)
            st.plotly_chart(fig_exp, use_container_width=True)

        # Source table
        st.subheader("Export Data with Filing References")
        display_cols = [
            c for c in [
                "fiscal_year", "export_revenue_inr_cr",
                "domestic_revenue_inr_cr", "export_pct",
                "source_document", "source_page_ref", "disclosure_label",
            ] if c in exp_df.columns
        ]
        st.dataframe(
            exp_df[display_cols].rename(columns={
                "fiscal_year": "FY",
                "export_revenue_inr_cr": "Export Revenue (₹ Cr)",
                "domestic_revenue_inr_cr": "Domestic Revenue (₹ Cr)",
                "export_pct": "Export %",
                "source_document": "Source",
                "source_page_ref": "Reference",
                "disclosure_label": "Label in Filing",
            }),
            use_container_width=True,
        )

        # Geographic breakdown if available
        for _, row in exp_df.iterrows():
            geo = row.get("geographic_breakdown")
            if geo and isinstance(geo, dict):
                st.subheader(f"Geographic Breakdown — FY{row['fiscal_year']}")
                geo_df = pd.DataFrame(
                    [{"Region": k, "Revenue Share %": v} for k, v in geo.items()]
                ).sort_values("Revenue Share %", ascending=False)
                fig_geo = px.bar(
                    geo_df,
                    x="Region",
                    y="Revenue Share %",
                    title=f"Geographic Revenue Split — FY{row['fiscal_year']}",
                    text_auto=".1f",
                    color="Revenue Share %",
                    color_continuous_scale="Greens",
                )
                fig_geo.update_layout(height=350, coloraxis_showscale=False)
                st.plotly_chart(fig_geo, use_container_width=True)
                break
    else:
        st.info(
            f"No export data found for {export_co_name}. "
            "The company may not disclose geographic revenue breakdown, "
            "or the data has not been extracted yet."
        )

    # Peer export comparison
    st.subheader("Peer Export Intensity Comparison")
    latest_year = max(years)
    peer_exp = []
    for co in companies:
        co_id = get_company_id(co["ticker"])
        if co_id:
            exp_list = fetch_export(co_id)
            for e in exp_list:
                if e.get("fiscal_year") == latest_year and e.get("export_pct"):
                    peer_exp.append({
                        "Company": co["name"],
                        "Export %": float(e["export_pct"]),
                        "Source": e.get("source_document", "—"),
                    })

    if peer_exp:
        peer_exp_df = pd.DataFrame(peer_exp).sort_values("Export %", ascending=False)
        fig_peer = px.bar(
            peer_exp_df,
            x="Company",
            y="Export %",
            title=f"Export Intensity Comparison — FY{latest_year}",
            text_auto=".1f",
            color="Export %",
            color_continuous_scale="Teal",
        )
        fig_peer.update_layout(height=380, coloraxis_showscale=False, xaxis_tickangle=-30)
        st.plotly_chart(fig_peer, use_container_width=True)
        st.dataframe(peer_exp_df, use_container_width=True)
    else:
        st.info(f"No export data available for peer group in FY{latest_year}.")
