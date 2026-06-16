"""
Page 2: Data Ingestion
- Launch ETL job for selected companies
- Live progress tracking
- Data quality summary on completion
"""
import time
import httpx
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Data Ingestion", page_icon="⚙️", layout="wide")

BACKEND = st.session_state.get("backend_url", "http://localhost:8000")

st.title("⚙️ Data Ingestion")
st.caption("Fetch and normalize financial statements for your peer group.")

companies = st.session_state.get("selected_companies", [])
years = st.session_state.get("selected_years", list(range(2015, 2025)))

if not companies:
    st.warning("No companies selected. Go back to Company Selection first.")
    if st.button("← Go to Company Selection"):
        st.switch_page("pages/1_Company_Selection.py")
    st.stop()

# ── Configuration panel ────────────────────────────────────────────────────
st.subheader("Configuration")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Companies", len(companies))
with col2:
    st.metric("Year Range", f"FY{min(years)} – FY{max(years)}")
with col3:
    st.metric("Max Years", max(years) - min(years) + 1)

# Provider priority
with st.expander("⚙️ Advanced: Provider Priority"):
    st.info(
        "Providers are tried in order. Data from earlier providers is preferred. "
        "All providers are tried to maximize coverage."
    )
    provider_order = st.multiselect(
        "Provider Priority (drag to reorder)",
        options=["screener", "nse", "bse", "mca_xbrl", "fmp", "alpha_vantage"],
        default=["screener", "nse", "fmp"],
    )

# Show companies to be ingested
with st.expander(f"Companies ({len(companies)})", expanded=True):
    df = pd.DataFrame([
        {"Ticker": c["ticker"], "Name": c["name"], "Exchange": c.get("exchange", "NSE")}
        for c in companies
    ])
    st.dataframe(df, use_container_width=True, height=200)

st.divider()

# ── Launch ingestion ───────────────────────────────────────────────────────
job_id = st.session_state.get("ingestion_job_id")

if not job_id:
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.write("Click **Start Ingestion** to begin fetching data from providers.")
    with col_b:
        if st.button("🚀 Start Ingestion", type="primary", use_container_width=True):
            tickers = [c["ticker"] for c in companies]
            try:
                resp = httpx.post(
                    f"{BACKEND}/api/v1/jobs/ingest/tickers",
                    json={
                        "tickers": tickers,
                        "years": years,
                        "provider_priority": provider_order,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                st.session_state.ingestion_job_id = data["job_id"]
                st.rerun()
            except Exception as e:
                st.error(f"Failed to start ingestion: {e}")

# ── Progress tracking ──────────────────────────────────────────────────────
else:
    st.subheader("Ingestion Progress")

    status_placeholder = st.empty()
    progress_bar = st.progress(0)
    log_placeholder = st.empty()

    def poll_job(job_id: str) -> dict:
        resp = httpx.get(f"{BACKEND}/api/v1/jobs/{job_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    # Polling loop
    max_polls = 180   # 15 minutes max (5 second intervals)
    for i in range(max_polls):
        try:
            job = poll_job(job_id)
            pct = float(job.get("progress_pct", 0))
            status = job.get("status", "unknown")
            summary = job.get("summary") or {}

            progress_bar.progress(int(pct))
            status_placeholder.markdown(
                f"**Status:** `{status.upper()}` | **Progress:** {pct:.1f}%"
            )

            if summary:
                log_placeholder.markdown(
                    f"✅ Success: {summary.get('success', 0)} | "
                    f"❌ Failed: {summary.get('failed', 0)} | "
                    f"Total: {summary.get('total', 0)}"
                )

            if status in ("completed", "failed", "partial"):
                if status == "completed":
                    st.success(f"✅ Ingestion complete! {summary.get('success', '?')} companies ingested successfully.")
                elif status == "failed":
                    st.error("❌ Ingestion failed. Check logs.")
                else:
                    st.warning(f"⚠️ Partial success: {summary.get('success', 0)}/{summary.get('total', 0)} companies ingested.")
                break

            time.sleep(5)
            if i < max_polls - 1:
                st.rerun()

        except Exception as e:
            st.warning(f"Status check error: {e}")
            time.sleep(5)

    # Reset button
    col_reset, col_next = st.columns(2)
    with col_reset:
        if st.button("🔄 Run Again"):
            st.session_state.ingestion_job_id = None
            st.rerun()
    with col_next:
        if st.button("▶ View Financial Statements →", type="primary"):
            st.switch_page("pages/3_Financial_Statements.py")
