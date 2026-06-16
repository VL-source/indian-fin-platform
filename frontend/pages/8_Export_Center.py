"""
Page 8: Export Center
- Download Excel (company / peer group)
- Download CSV
- Generate PDF summary report
"""
import httpx
import streamlit as st

st.set_page_config(page_title="Export Center", page_icon="📥", layout="wide")

BACKEND = st.session_state.get("backend_url", "http://localhost:8000")

st.title("📥 Export Center")
st.caption("Download financial data in Excel, CSV, or PDF format.")

companies = st.session_state.get("selected_companies", [])
years = st.session_state.get("selected_years", list(range(2015, 2025)))
peer_group_id = st.session_state.get("peer_group_id")

if not companies:
    st.warning("No companies selected.")
    st.stop()


@st.cache_data(ttl=30)
def get_company_id(ticker: str) -> str:
    try:
        resp = httpx.get(f"{BACKEND}/api/v1/companies/search", params={"q": ticker}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data[0]["company_id"] if data else None
    except Exception:
        return None


def download_excel(url: str, filename: str, params: dict = None) -> None:
    try:
        resp = httpx.get(url, params=params or {}, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        st.download_button(
            label=f"⬇ Download {filename}",
            data=resp.content,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        st.error(f"Failed to generate export: {e}")


def download_csv(url: str, filename: str, params: dict = None) -> None:
    try:
        resp = httpx.get(url, params=params or {}, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        st.download_button(
            label=f"⬇ Download {filename}",
            data=resp.content,
            file_name=filename,
            mime="text/csv",
        )
    except Exception as e:
        st.error(f"Failed to generate CSV: {e}")


years_str = ",".join(str(y) for y in years)

tab1, tab2, tab3 = st.tabs(["🏢 Company Reports", "👥 Peer Group Report", "📋 Data Quality"])

# ── Tab 1: Company Reports ─────────────────────────────────────────────────
with tab1:
    st.subheader("Company-Level Export")

    col1, col2 = st.columns([2, 2])
    with col1:
        company_options = {c["name"]: c for c in companies}
        selected_name = st.selectbox("Company", options=list(company_options.keys()))
        selected_co = company_options[selected_name]

    with col2:
        export_format = st.radio("Format", options=["Excel (.xlsx)", "CSV (.csv)"], horizontal=True)

    cid = get_company_id(selected_co["ticker"])
    ticker_clean = selected_co["ticker"].replace("/", "_")

    st.markdown(f"**{selected_name}** — FY{min(years)} to FY{max(years)}")
    st.markdown("Includes: Raw Financials, Common-Size Metrics, Time-Series Analytics")

    if cid:
        if export_format == "Excel (.xlsx)":
            if st.button("Generate Excel Report", key="gen_excel_co"):
                with st.spinner("Generating..."):
                    download_excel(
                        f"{BACKEND}/api/v1/exports/excel/company/{cid}",
                        f"{ticker_clean}_financial_analysis.xlsx",
                        params={"years": years_str},
                    )
        else:
            if st.button("Generate CSV", key="gen_csv_co"):
                with st.spinner("Generating..."):
                    download_csv(
                        f"{BACKEND}/api/v1/exports/csv/company/{cid}",
                        f"{ticker_clean}_common_size.csv",
                        params={"years": years_str},
                    )
    else:
        st.error("Company not found in database. Please run ingestion first.")

    st.divider()

    # Bulk download for all companies
    st.subheader("Bulk Download — All Companies")
    if st.button("Generate All Companies (ZIP)", key="bulk_dl"):
        st.info("Bulk ZIP export: Generates one Excel per company and packages into a ZIP archive.")
        # Production: would trigger a background task
        st.warning("Bulk export requires background processing. Check the Jobs page.")


# ── Tab 2: Peer Group Report ───────────────────────────────────────────────
with tab2:
    st.subheader("Peer Group Comparison Export")

    if not peer_group_id:
        st.info(
            "No peer group ID found in session. "
            "Create a peer group via the API or use Company Selection → Confirm step."
        )
    else:
        col_a, col_b = st.columns([2, 2])
        with col_a:
            fy_for_peer = st.selectbox(
                "Fiscal Year", options=sorted(years, reverse=True), key="peer_fy"
            )
        with col_b:
            peer_format = st.radio("Format", ["Excel (.xlsx)", "CSV"], horizontal=True, key="peer_fmt")

        st.markdown(
            f"Includes: Side-by-side % comparison + Peer averages, medians, percentiles — "
            f"FY{fy_for_peer}"
        )

        if peer_format == "Excel (.xlsx)":
            if st.button("Generate Peer Group Excel", key="gen_peer_excel"):
                with st.spinner("Generating..."):
                    download_excel(
                        f"{BACKEND}/api/v1/exports/excel/peer-group/{peer_group_id}",
                        f"peer_group_FY{fy_for_peer}.xlsx",
                        params={"fiscal_year": fy_for_peer},
                    )

    st.divider()

    # Manual peer export — build from selected companies
    st.subheader("Ad-hoc Multi-Company CSV")
    st.caption("Download common-size data for all selected companies in one file.")

    if st.button("Generate Multi-Company CSV", key="gen_multi_csv"):
        import io
        import pandas as pd

        all_data = []
        with st.spinner("Fetching data for all companies..."):
            for co in companies:
                co_id = get_company_id(co["ticker"])
                if not co_id:
                    continue
                try:
                    resp = httpx.get(
                        f"{BACKEND}/api/v1/analytics/common-size/{co_id}",
                        params={"years": years_str},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    rows = resp.json()
                    for r in rows:
                        r["company"] = co["name"]
                        r["ticker"] = co["ticker"]
                        all_data.append(r)
                except Exception:
                    pass

        if all_data:
            df = pd.DataFrame(all_data)
            csv_bytes = df.to_csv(index=False).encode()
            st.download_button(
                label="⬇ Download Multi-Company CSV",
                data=csv_bytes,
                file_name="multi_company_common_size.csv",
                mime="text/csv",
            )
        else:
            st.error("No data available.")


# ── Tab 3: Data Quality ────────────────────────────────────────────────────
with tab3:
    st.subheader("Data Quality Report")
    st.caption("Coverage, confidence scores, and missing fields for each company-year.")

    if st.button("Generate Quality Report", key="gen_dq"):
        import pandas as pd

        dq_rows = []
        with st.spinner("Fetching quality metrics..."):
            for co in companies:
                co_id = get_company_id(co["ticker"])
                if not co_id:
                    continue
                try:
                    resp = httpx.get(
                        f"{BACKEND}/api/v1/financials/{co_id}/available-years",
                        timeout=10,
                    )
                    available_years = resp.json() if resp.status_code == 200 else []
                    for yr in available_years:
                        dq_rows.append({
                            "Company": co["name"],
                            "Ticker": co["ticker"],
                            "Fiscal Year": yr,
                            "Status": "Data Available",
                        })
                except Exception:
                    dq_rows.append({
                        "Company": co["name"],
                        "Ticker": co["ticker"],
                        "Fiscal Year": "—",
                        "Status": "Not Ingested / Error",
                    })

        if dq_rows:
            dq_df = pd.DataFrame(dq_rows)
            st.dataframe(dq_df, use_container_width=True)

            # Coverage summary
            ingested = dq_df[dq_df["Status"] == "Data Available"]["Company"].nunique()
            total = len(companies)
            st.metric("Coverage", f"{ingested}/{total} companies", f"{ingested/total*100:.0f}%")

            # Download
            csv_dq = dq_df.to_csv(index=False).encode()
            st.download_button(
                label="⬇ Download Quality Report CSV",
                data=csv_dq,
                file_name="data_quality_report.csv",
                mime="text/csv",
            )
        else:
            st.warning("No data available to generate report.")
