"""
Indian Financial Platform — Streamlit App Entry Point.
Multi-page app with sidebar navigation.
"""
import streamlit as st

st.set_page_config(
    page_title="Indian Financial Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "# Indian Financial Platform\nMulti-year peer-group financial benchmarking for NSE/BSE listed companies."
    },
)

# ── Global styles ──────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-title { font-size: 2.2rem; font-weight: 700; color: #1F3864; }
    .subtitle   { font-size: 1.1rem; color: #555; margin-bottom: 1.5rem; }
    .metric-card {
        background: #f0f4ff; border-radius: 10px; padding: 1rem;
        border-left: 4px solid #1F3864; margin: 0.5rem 0;
    }
    .stSelectbox label { font-weight: 600; }
    .stButton > button { background-color: #1F3864; color: white; border-radius: 6px; }
    .stButton > button:hover { background-color: #2E5497; }
    div[data-testid="stSidebar"] { background: #0A1628; }
    div[data-testid="stSidebar"] * { color: #E8EDF5 !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state defaults ─────────────────────────────────────────────────
if "selected_companies" not in st.session_state:
    st.session_state.selected_companies = []
if "peer_group_id" not in st.session_state:
    st.session_state.peer_group_id = None
if "ingestion_job_id" not in st.session_state:
    st.session_state.ingestion_job_id = None
if "selected_years" not in st.session_state:
    st.session_state.selected_years = list(range(2015, 2025))
if "backend_url" not in st.session_state:
    import os
    st.session_state.backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")

# ── Landing page ───────────────────────────────────────────────────────────
st.markdown('<div class="main-title">📊 Indian Financial Platform</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Multi-year, peer-group common-size financial benchmarking '
    'for NSE/BSE listed companies</div>',
    unsafe_allow_html=True,
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Data Sources", "6 Providers", "MCA XBRL → FMP")
col2.metric("Coverage", "NSE + BSE", "Indian markets only")
col3.metric("History", "Up to 10 Years", "Annual statements")
col4.metric("Granularity", "Line-item level", "Original labels preserved")

st.divider()
st.markdown("""
### How to use
1. **Company Selection** → Add companies by ticker/name or pick a sector
2. **Data Ingestion** → Fetch & normalize financial statements
3. **Financial Statements** → Explore raw line items
4. **Common-Size Analysis** → P&L and Balance Sheet as % of revenue
5. **Peer Benchmarks** → Compare across the group
6. **Time-Series** → 10-year trends, CAGR, volatility
7. **Product Mix & Exports** → Segment and geographic breakdown
8. **Export** → Download Excel / CSV / PDF

Navigate using the **pages** in the sidebar →
""")

# Show active session summary if companies are loaded
if st.session_state.selected_companies:
    st.success(
        f"✅ Active session: **{len(st.session_state.selected_companies)} companies** selected "
        f"| Years: {min(st.session_state.selected_years)}–{max(st.session_state.selected_years)}"
    )
