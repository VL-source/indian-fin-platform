"""
Page 3: Financial Statements Explorer
- View raw reported line items at maximum granularity
- See original labels + standardized labels + mapping confidence
- Filter by statement type, year, category
"""
import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Financial Statements", page_icon="📄", layout="wide")

import os
BACKEND = st.session_state.get("backend_url") or os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("📄 Financial Statements Explorer")
st.caption(
    "Raw reported line items at maximum granularity — original labels preserved. "
    "View standardized mappings, confidence scores, and source traceability."
)

companies = st.session_state.get("selected_companies", [])
if not companies:
    st.warning("No companies selected.")
    st.stop()


@st.cache_data(ttl=120)
def get_company_id(ticker: str) -> str:
    try:
        resp = httpx.get(f"{BACKEND}/api/v1/companies/search", params={"q": ticker}, timeout=10)
        data = resp.json()
        return data[0]["company_id"] if data else None
    except Exception:
        return None


@st.cache_data(ttl=120)
def fetch_statements(company_id: str, fiscal_year: int, stmt_type: str) -> list:
    try:
        resp = httpx.get(
            f"{BACKEND}/api/v1/financials/{company_id}/statements",
            params={"fiscal_year": fiscal_year, "statement_type": stmt_type},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


# ── Controls ───────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)

with col1:
    company_map = {c["name"]: c for c in companies}
    sel_name = st.selectbox("Company", list(company_map.keys()))
    sel_co = company_map[sel_name]

years = st.session_state.get("selected_years", list(range(2015, 2025)))
with col2:
    sel_year = st.selectbox("Fiscal Year", sorted(years, reverse=True))

with col3:
    stmt_type = st.selectbox(
        "Statement Type",
        options=["income_statement", "balance_sheet", "cash_flow", "notes"],
        format_func=lambda x: {
            "income_statement": "P&L Statement",
            "balance_sheet": "Balance Sheet",
            "cash_flow": "Cash Flow",
            "notes": "Notes / Disclosures",
        }.get(x, x),
    )

# Show/hide options
show_derived = st.checkbox("Show derived items", value=True)
show_unmapped = st.checkbox("Show unmapped items", value=True)
min_confidence = st.slider("Min mapping confidence", 0.0, 1.0, 0.0, 0.05)

st.divider()

# ── Fetch data ─────────────────────────────────────────────────────────────
cid = get_company_id(sel_co["ticker"])
if not cid:
    st.error("Company not found in database. Please run ingestion first.")
    st.stop()

with st.spinner("Loading financial data..."):
    statements = fetch_statements(cid, sel_year, stmt_type)

if not statements:
    st.warning(
        f"No {stmt_type.replace('_', ' ')} data found for {sel_name} FY{sel_year}. "
        "Please run Data Ingestion first."
    )
    st.stop()

# ── Display statements ─────────────────────────────────────────────────────
for stmt in statements:
    source_info = f"Source: **{stmt.get('source_type', 'unknown')}**"
    if stmt.get("source_document"):
        source_info += f" | {stmt['source_document']}"
    quality = stmt.get("data_quality_score")
    if quality:
        quality_color = "🟢" if quality >= 0.8 else "🟡" if quality >= 0.6 else "🔴"
        source_info += f" | Quality: {quality_color} {quality:.2f}"

    with st.expander(
        f"📋 {stmt_type.replace('_', ' ').title()} — {stmt.get('consolidation', '').title()} — {source_info}",
        expanded=True,
    ):
        items = stmt.get("line_items", [])

        # Filter
        if not show_derived:
            items = [i for i in items if not i.get("is_derived")]
        if not show_unmapped:
            items = [i for i in items if i.get("standardized_label")]
        if min_confidence > 0:
            items = [
                i for i in items
                if (i.get("mapping_confidence") or 0) >= min_confidence
            ]

        if not items:
            st.info("No items match the current filters.")
            continue

        # Build display dataframe
        rows = []
        for item in items:
            conf = item.get("mapping_confidence")
            conf_display = f"{conf:.2f}" if conf is not None else "—"
            conf_icon = ""
            if conf is not None:
                conf_icon = "🟢" if conf >= 0.9 else "🟡" if conf >= 0.7 else "🔴"

            rows.append({
                "Lvl": item.get("hierarchy_level", 1),
                "Original Label (as reported)": ("  " * (item.get("hierarchy_level", 1) - 1)) + item.get("original_label", ""),
                "Standardized Label": item.get("standardized_label") or "⚠ Unmapped",
                "Category": item.get("label_category") or "—",
                "Value (₹ Cr)": f"{float(item['std_value_inr_cr']):,.2f}" if item.get("std_value_inr_cr") is not None else "—",
                "Confidence": f"{conf_icon} {conf_display}",
                "Derived": "✓" if item.get("is_derived") else "",
            })

        df = pd.DataFrame(rows)

        # Color coding
        def style_row(row):
            styles = [""] * len(row)
            if "⚠ Unmapped" in str(row.get("Standardized Label", "")):
                styles = ["background-color: #fff3cd"] * len(row)
            elif row.get("Derived") == "✓":
                styles = ["background-color: #e8f4f8"] * len(row)
            return styles

        styled = df.style.apply(style_row, axis=1)
        st.dataframe(styled, use_container_width=True, height=min(600, len(rows) * 36 + 40))

        # Mapping summary
        total = len(items)
        mapped = sum(1 for i in items if i.get("standardized_label"))
        derived_count = sum(1 for i in items if i.get("is_derived"))
        avg_conf = sum(float(i.get("mapping_confidence") or 0) for i in items) / max(total, 1)

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("Total Line Items", total)
        col_b.metric("Mapped", mapped, f"{mapped/total*100:.0f}%")
        col_c.metric("Derived", derived_count)
        col_d.metric("Avg Confidence", f"{avg_conf:.2f}")

# ── Unmapped items summary ─────────────────────────────────────────────────
all_items = [i for stmt in statements for i in stmt.get("line_items", [])]
unmapped = [i for i in all_items if not i.get("standardized_label")]

if unmapped:
    with st.expander(f"⚠ {len(unmapped)} Unmapped Items (need mapping)"):
        st.caption(
            "These labels were not matched to any standardized label. "
            "Add them to the label mapping dictionary for better coverage."
        )
        for item in unmapped[:50]:
            st.code(f'"{item.get("original_label")}"  →  [UNMAPPED]')
