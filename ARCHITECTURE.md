# Indian Financial Platform — System Architecture

## Overview

A production-grade platform for ingesting, standardizing, and analyzing Indian public company
financial statements (NSE/BSE only). Generates multi-year peer-group common-size benchmarks,
product mix analytics, and export intensity reporting.

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACES                              │
│                                                                     │
│   Streamlit MVP (Port 8501)          Next.js (Future, Port 3000)   │
│   8 pages: Company Selection →       Full SaaS, Auth, Multi-user   │
│   Ingestion → Analysis → Export                                    │
└───────────────────────┬─────────────────────────────────────────────┘
                        │ HTTP / REST
┌───────────────────────▼─────────────────────────────────────────────┐
│                    FastAPI Backend  (Port 8000)                     │
│                                                                     │
│  /api/v1/companies    → search, peer groups                        │
│  /api/v1/jobs         → ingestion job management                   │
│  /api/v1/financials   → raw statements, line items                 │
│  /api/v1/analytics    → common-size, peer benchmarks, time-series  │
│  /api/v1/exports      → Excel, CSV, PDF generation                 │
└──────────┬─────────────────────┬───────────────────────────────────┘
           │                     │
    ┌──────▼──────┐     ┌────────▼────────┐
    │  PostgreSQL │     │  Redis + Celery │
    │  (Port 5432)│     │  Task Queue     │
    │             │     │  (Port 6379)    │
    │  14 tables  │     │  4 workers      │
    │  2 mat views│     │  Beat scheduler │
    └──────┬──────┘     └────────┬────────┘
           │                     │
           └──────────┬──────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────────┐
│                    ETL Orchestrator                                 │
│                                                                     │
│  Provider Priority Chain (pluggable, configurable):                │
│  1. MCA XBRL      → highest reliability (0.99)                    │
│  2. Screener.in   → 10yr P&L, BS, CF (0.88)                       │
│  3. NSE API       → identity + market cap (0.97)                   │
│  4. BSE API       → filing index (0.96)                            │
│  5. FMP API       → fallback financials (0.82)                     │
│  6. Alpha Vantage → last resort (0.78)                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema (PostgreSQL 15)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `companies` | Company master with NSE/BSE identity | nse_ticker, bse_code, isin, cin, market_cap |
| `financial_statements` | One row per company-year-type-source | fiscal_year, statement_type, source_type, quality_score |
| `financial_line_items` | Raw line items at maximum granularity | original_label, standardized_label, hierarchy_path, confidence |
| `label_mappings` | Extensible label → standardized label dictionary | 100+ seed mappings, fuzzy/regex/exact match |
| `common_size_metrics` | Pre-computed % of revenue per line item | metric_name, common_size_pct, revenue_base |
| `peer_groups` | Named groups of companies | name, group_type, sector |
| `peer_group_members` | Company ↔ peer group membership | many-to-many |
| `peer_group_metrics` | Aggregate statistics per group-year-metric | equal_weight_avg, mktcap_weight_avg, median, std_dev, p25, p75 |
| `time_series_metrics` | CAGR, YoY, rolling averages per metric | cagr_pct, yoy_growth_pct, annual_values (JSONB) |
| `product_mix` | Segment revenue (official filings only) | segment_name, revenue_share_pct, source_document, source_page_ref |
| `export_intensity` | Geographic revenue split (disclosed only) | export_revenue, export_pct, geographic_breakdown (JSONB) |
| `data_quality_audit` | Per company-year quality scores | overall_quality_score, mapping_coverage, missing_items |
| `ingestion_jobs` | Background ETL job tracking | status, progress_pct, summary |
| `mv_common_size_pl` | Materialized view for dashboard speed | — |
| `mv_peer_group_summary` | Materialized view for peer dashboards | — |

---

## ETL Pipeline Design

```
User Input (tickers/sector)
         │
         ▼
ETLOrchestrator.ingest_companies()
         │
    ┌────▼────────────────────┐
    │  For each ticker:        │
    │  1. Resolve company ID   │  ← Provider search cascade
    │  2. Upsert companies     │  ← DB write
    │  3. Fetch statements     │  ← Provider cascade (all providers)
    │  4. Normalize labels     │  ← NormalizationEngine
    │  5. Persist line items   │  ← DB write (all items, no discard)
    │  6. Compute common-size  │  ← CommonSizeService
    │  7. Compute time-series  │  ← TimeSeriesService
    │  8. Quality audit        │  ← DataQualityAudit
    └────────────────────────┘
         │
    ┌────▼────────────────────┐
    │  Peer Group Aggregation  │  ← PeerGroupAnalyticsService
    │  (run after all members  │
    │   are ingested)          │
    └─────────────────────────┘
```

### Label Normalization Priority

```
Raw label "Revenue from Operations"
    │
    ├── 1. Exact cache match (DB)      → "revenue" (conf: 0.99) ✓ STOP
    │
    ├── 2. Regex pattern match          → "revenue" (conf: 0.95) ✓ STOP
    │
    ├── 3. Fuzzy match (fuzzywuzzy)     → best match ≥ 80% (conf: scaled)
    │
    └── 4. Unmapped fallback            → standardized_label=NULL, conf=0.0
         (item PRESERVED in DB)
```

---

## Financial Normalization Engine

### Label Mapping Dictionary
- 100+ pre-seeded exact mappings for common Indian labels
- Covers all major P&L, Balance Sheet, Cash Flow labels
- Regex patterns for common variations (50+ patterns)
- Fuzzy matching as last resort (fuzzywuzzy, threshold=80)
- All mappings stored in `label_mappings` table (fully extensible)

### Derived Metrics (auto-computed)
| Output | Formula | Condition |
|--------|---------|-----------|
| EBITDA | EBIT + D&A | If EBITDA missing |
| EBITDA | PBT + Finance Costs + D&A | If EBITDA and EBIT missing |
| EBIT | PBT + Finance Costs | If EBIT missing |
| Gross Profit | Revenue − Raw Materials | If not reported |
| Working Capital | Current Assets − Current Liabilities | If not reported |
| Total Debt | LT Debt + ST Debt | If not reported |
| Net Debt | Total Debt − Cash | If not reported |

---

## Common-Size Methodology

```
For every line item in every company-year:
  Common Size % = (Line Item Value / Revenue) × 100
  Revenue = 100%

Stored: raw_value_inr_cr + revenue_base_inr_cr + common_size_pct
Computed: per company-year after ingestion
```

---

## Peer Group Analytics

Dynamic aggregation — no fixed template:

```sql
For each (fiscal_year, metric_name) across peer group members:
  equal_weight_avg  = mean(common_size_pct)
  mktcap_weight_avg = Σ(pct × mktcap) / Σ(mktcap)
  median            = median(common_size_pct)
  std_dev           = stdev(common_size_pct)
  p25, p75          = percentiles
  count_companies   = n
```

Minimum 2 companies required. All metrics discovered dynamically.

---

## Product Mix & Export Intensity Rules

**STRICT SOURCE POLICY:**

| Allowed | NOT Allowed |
|---------|-------------|
| Annual Reports (official) | News articles |
| NSE/BSE filings | Analyst reports |
| MCA XBRL filings | Financial portals |
| MD&A sections | External databases |
| Investor presentations (company-issued) | Estimates |

Every segment/export data row includes:
- `source_document` (required, NOT NULL)
- `source_page_ref` (page number + section)
- `source_type` (one of 5 allowed values)

Segment definitions are stored exactly as reported — **no cross-company normalization.**

---

## Streamlit Frontend Pages

| Page | Description |
|------|-------------|
| Home | Landing, active session summary |
| 1. Company Selection | Search, CSV upload, sector browse, peer group editing |
| 2. Data Ingestion | Launch ETL job, live progress bar, provider config |
| 3. Financial Statements | Raw line items explorer with mapping confidence indicators |
| 4. Common-Size Analysis | Heatmap, waterfall, trend lines, full P&L/BS table |
| 5. Peer Benchmarks | Bar charts, box plots, side-by-side comparison, aggregate stats |
| 6. Time-Series | CAGR table, multi-company overlays, rolling averages, volatility |
| 7. Product Mix & Exports | Pie charts, area charts, geographic breakdown, source refs |
| 8. Export Center | Excel/CSV/PDF downloads, bulk export, data quality report |

---

## Deployment Plan

### Phase 1 — Local / Single Server
```bash
cp .env.example .env
# Edit .env with your API keys
docker-compose up -d
# App: http://localhost:8501
# API: http://localhost:8000/docs
# Flower: http://localhost:5555
```

### Phase 2 — Production
- PostgreSQL → AWS RDS / GCP Cloud SQL (multi-AZ)
- Redis → ElastiCache / Upstash
- Backend → Docker on ECS/GKE, 2+ replicas
- Celery workers → separate ECS task group
- Streamlit → Streamlit Cloud or Dockerized on ECS
- S3/GCS → document cache (annual report PDFs)
- CloudFront/CDN → static assets

### Phase 3 — Scale
- Replace Streamlit with Next.js (same backend unchanged)
- Add authentication (OAuth2 / Supabase Auth)
- Add multi-tenancy (peer groups scoped to users)
- Add incremental refresh (Celery Beat, daily)
- Add more providers (BSE XBRL parser, MCA filing API)

---

## Data Quality Framework

Each company-year gets an `overall_quality_score` (0–1):

```
quality_score = 
    (mapped_items / total_items) × 0.40 +    # Coverage
    avg_mapping_confidence           × 0.40 +    # Confidence
    (1 - missing_critical / critical) × 0.20     # Completeness
```

Critical metrics tracked: revenue, ebitda, pat, total_assets, total_equity.

Scores below `quality_score_floor` (default 0.50) are excluded from peer aggregates.

---

## Testing Strategy

```
tests/
├── test_normalization.py    # Label mapping, unit conversion, derivation
├── test_etl.py             # Provider parsing, rate limiter, RawStatement
└── conftest.py             # pytest-asyncio fixtures

Run:  cd backend && pytest tests/ -v --cov=app --cov-report=html
```

Target coverage: ≥80% on normalization engine, ≥70% overall.

---

## Scalability

| Concern | Solution |
|---------|---------|
| 1000s of companies | Celery parallelism (configurable concurrency) |
| 10+ years per company | Incremental refresh — only fetch missing years |
| Peer group recomputation | Triggered by Celery task after all members ingested |
| Dashboard speed | Materialized views + pre-computed common-size table |
| Provider rate limits | Per-provider token-bucket rate limiter |
| API key management | `.env` file, Pydantic settings, never hardcoded |
| Schema evolution | Alembic migrations |

---

## File Count & Code Volume

| Component | Files | Lines |
|-----------|-------|-------|
| Database schema | 1 | 555 |
| Backend (FastAPI + ETL + services + models) | 18 | ~3,800 |
| Frontend (Streamlit, 8 pages) | 9 | ~1,500 |
| Tests | 3 | ~350 |
| Config (Docker, env, requirements) | 5 | ~200 |
| **Total** | **36** | **~6,400** |
