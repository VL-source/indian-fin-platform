"""
Page 1: Company Selection
- Search companies by name/ticker
- Upload a CSV list
- Select by sector (top 100 by market cap)
- Edit the peer group before proceeding
"""
import io
import time
from typing import List, Optional

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Company Selection", page_icon="🏢", layout="wide")

BACKEND = st.session_state.get("backend_url", "http://localhost:8000")

SECTORS = [
    "IT Services", "Banking", "NBFC", "Insurance", "FMCG", "Pharmaceuticals",
    "Chemicals", "Auto Components", "Automobile", "Cement", "Steel",
    "Power", "Oil & Gas", "Telecom", "Real Estate", "Retail",
    "Textiles", "Infrastructure", "Consumer Durables", "Agrochemicals",
]

st.title("🏢 Company Selection")
st.caption("Build your peer group. You can search by ticker, upload a list, or select by sector.")


# ── Helper functions ───────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def search_companies_api(query: str) -> List[dict]:
    try:
        resp = httpx.get(
            f"{BACKEND}/api/v1/companies/search/external",
            params={"q": query},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def get_sector_companies(sector: str, limit: int = 100) -> List[dict]:
    try:
        resp = httpx.get(
            f"{BACKEND}/api/v1/companies/sector/{sector}",
            params={"limit": limit},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def add_company(company: dict) -> None:
    """Add a company to the session state selected list."""
    existing_tickers = {c["ticker"] for c in st.session_state.selected_companies}
    if company["ticker"] not in existing_tickers:
        st.session_state.selected_companies.append(company)


def remove_company(ticker: str) -> None:
    st.session_state.selected_companies = [
        c for c in st.session_state.selected_companies if c["ticker"] != ticker
    ]


# ── Input method selection ─────────────────────────────────────────────────

method = st.radio(
    "Choose input method",
    options=["🔍 Search by Name/Ticker", "📋 Upload CSV/Excel", "🏭 Browse by Sector"],
    horizontal=True,
)

st.divider()

# ── Method 1: Search ───────────────────────────────────────────────────────
if method == "🔍 Search by Name/Ticker":
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input(
            "Search company",
            placeholder="e.g. TCS, Infosys, Reliance, HDFC Bank",
        )
    with col2:
        st.write("")
        st.write("")
        search_clicked = st.button("Search", use_container_width=True)

    if query and (search_clicked or len(query) >= 3):
        with st.spinner("Searching..."):
            results = search_companies_api(query)

        if results:
            st.write(f"Found **{len(results)}** companies")
            for i, r in enumerate(results[:10]):
                cols = st.columns([3, 1, 1, 1, 1])
                cols[0].write(f"**{r['name']}**")
                cols[1].write(r["ticker"])
                cols[2].write(r.get("exchange", "NSE"))
                cols[3].write(
                    f"₹{r['market_cap_inr_cr']:,.0f} Cr" if r.get("market_cap_inr_cr") else "—"
                )
                if cols[4].button("Add ➕", key=f"add_{i}_{r['ticker']}_{r.get('exchange', 'NSE')}"):
                    add_company(r)
                    st.rerun()
        else:
            st.warning("No companies found. Try a different name or ticker.")


# ── Method 2: Upload CSV ───────────────────────────────────────────────────
elif method == "📋 Upload CSV/Excel":
    st.info(
        "Upload a file with a column named `ticker` (NSE symbols) and optionally `name`. "
        "Accepted formats: .csv, .xlsx"
    )
    uploaded = st.file_uploader("Choose file", type=["csv", "xlsx"])

    if uploaded:
        try:
            if uploaded.name.endswith(".xlsx"):
                df = pd.read_excel(uploaded)
            else:
                df = pd.read_csv(uploaded)

            if "ticker" not in df.columns:
                st.error("File must contain a `ticker` column.")
            else:
                df = df.dropna(subset=["ticker"])
                st.write(f"Found **{len(df)}** tickers in file")
                st.dataframe(df.head(20), use_container_width=True)

                if st.button("Add All to Peer Group"):
                    for _, row in df.iterrows():
                        add_company({
                            "ticker": str(row["ticker"]).strip().upper(),
                            "name": str(row.get("name", row["ticker"])),
                            "exchange": str(row.get("exchange", "NSE")),
                            "market_cap_inr_cr": None,
                        })
                    st.success(f"Added {len(df)} companies")
                    st.rerun()
        except Exception as e:
            st.error(f"Error reading file: {e}")


# ── Method 3: Sector Browse ────────────────────────────────────────────────
elif method == "🏭 Browse by Sector":
    col1, col2 = st.columns([3, 1])
    with col1:
        sector = st.selectbox("Select Sector", options=SECTORS)
    with col2:
        limit = st.number_input("Max companies", min_value=10, max_value=200, value=100, step=10)

    if st.button(f"Fetch Top {limit} Companies in {sector}", use_container_width=True):
        with st.spinner(f"Fetching top {limit} {sector} companies by market cap..."):
            companies = get_sector_companies(sector, limit)

        if companies:
            st.write(f"Found **{len(companies)}** companies")
            df = pd.DataFrame(companies)
            if "market_cap_inr_cr" in df.columns:
                df = df.sort_values("market_cap_inr_cr", ascending=False)

            st.write("**Review & select companies before proceeding:**")

            # Checkbox selection
            selected_indices = []
            for i, row in df.iterrows():
                col_a, col_b, col_c, col_d = st.columns([0.5, 3, 1.5, 1.5])
                checked = col_a.checkbox("", key=f"chk_{i}", value=True)
                col_b.write(row.get("name", row["ticker"]))
                col_c.write(row["ticker"])
                col_d.write(
                    f"₹{row['market_cap_inr_cr']:,.0f} Cr"
                    if row.get("market_cap_inr_cr")
                    else "—"
                )
                if checked:
                    selected_indices.append(i)

            if st.button("Add Selected to Peer Group"):
                added = 0
                for i in selected_indices:
                    row = df.iloc[i]
                    add_company({
                        "ticker": row["ticker"],
                        "name": row.get("name", row["ticker"]),
                        "exchange": row.get("exchange", "NSE"),
                        "market_cap_inr_cr": row.get("market_cap_inr_cr"),
                    })
                    added += 1
                st.success(f"Added {added} companies to peer group")
                st.rerun()
        else:
            st.warning("No companies found for this sector. The backend may still be loading data.")

# ── Peer Group Panel ───────────────────────────────────────────────────────

st.divider()
st.subheader(f"📋 Current Peer Group — {len(st.session_state.selected_companies)} companies")

if not st.session_state.selected_companies:
    st.info("No companies selected yet. Use the options above to build your peer group.")
else:
    col_yr1, col_yr2 = st.columns(2)
    with col_yr1:
        start_year = st.selectbox(
            "From Fiscal Year",
            options=list(range(2014, 2026)),
            index=1,
            key="start_year",
        )
    with col_yr2:
        end_year = st.selectbox(
            "To Fiscal Year",
            options=list(range(2014, 2026)),
            index=10,
            key="end_year",
        )
    if start_year <= end_year:
        st.session_state.selected_years = list(range(start_year, end_year + 1))

    # Company list table
    rows = []
    for i, c in enumerate(st.session_state.selected_companies):
        rows.append({
            "#": i + 1,
            "Ticker": c["ticker"],
            "Name": c["name"],
            "Exchange": c.get("exchange", "NSE"),
            "Market Cap (₹ Cr)": f"{c['market_cap_inr_cr']:,.0f}" if c.get("market_cap_inr_cr") else "—",
        })

    df_display = pd.DataFrame(rows)
    st.dataframe(df_display, use_container_width=True, height=min(400, len(rows) * 38 + 40))

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        ticker_to_remove = st.selectbox(
            "Remove company",
            options=["—"] + [c["ticker"] for c in st.session_state.selected_companies],
        )
        if ticker_to_remove != "—" and st.button("Remove"):
            remove_company(ticker_to_remove)
            st.rerun()

    with col_b:
        if st.button("Clear All", type="secondary"):
            st.session_state.selected_companies = []
            st.rerun()

    with col_c:
        if st.button("✅ Confirm & Go to Ingestion →", type="primary", use_container_width=True):
            st.switch_page("pages/2_Data_Ingestion.py")
