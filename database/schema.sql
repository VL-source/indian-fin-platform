-- ============================================================
-- Indian Financial Platform — PostgreSQL Schema
-- Production-grade, maximum granularity
-- ============================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- fuzzy text search
CREATE EXTENSION IF NOT EXISTS "btree_gin";  -- composite GIN indexes

-- ============================================================
-- 1. COMPANIES
-- ============================================================
CREATE TABLE IF NOT EXISTS companies (
    company_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nse_ticker          VARCHAR(20)  UNIQUE,
    bse_code            VARCHAR(10)  UNIQUE,
    isin                VARCHAR(12)  UNIQUE,
    name                VARCHAR(255) NOT NULL,
    name_normalized     VARCHAR(255) GENERATED ALWAYS AS (lower(trim(name))) STORED,
    sector              VARCHAR(100),
    industry            VARCHAR(100),
    sub_industry        VARCHAR(150),
    market_cap_inr_cr   NUMERIC(20, 2),        -- INR crores, latest snapshot
    market_cap_date     DATE,
    listing_exchange    VARCHAR(10) CHECK (listing_exchange IN ('NSE', 'BSE', 'BOTH')),
    listing_date        DATE,
    face_value          NUMERIC(10, 2),
    cin                 VARCHAR(21),            -- Corporate Identity Number (MCA)
    registered_office   TEXT,
    website             VARCHAR(500),
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_companies_sector        ON companies(sector);
CREATE INDEX IF NOT EXISTS idx_companies_name_trgm     ON companies USING GIN(name_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_companies_market_cap    ON companies(market_cap_inr_cr DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_companies_nse_ticker    ON companies(nse_ticker);
CREATE INDEX IF NOT EXISTS idx_companies_bse_code      ON companies(bse_code);

-- ============================================================
-- 2. FINANCIAL STATEMENTS (one row per company-year-statement)
-- ============================================================
CREATE TABLE IF NOT EXISTS financial_statements (
    statement_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2040),
    -- e.g. 2024 = FY Apr 2023 – Mar 2024
    statement_type      VARCHAR(30) NOT NULL CHECK (statement_type IN (
                            'income_statement', 'balance_sheet', 'cash_flow',
                            'notes', 'segment', 'other')),
    reporting_currency  VARCHAR(3)  DEFAULT 'INR',
    reporting_unit      VARCHAR(20) DEFAULT 'crores',
    -- Consolidation level
    consolidation       VARCHAR(20) DEFAULT 'consolidated' CHECK (consolidation IN (
                            'consolidated', 'standalone', 'unknown')),
    -- Source metadata
    source_type         VARCHAR(30) CHECK (source_type IN (
                            'mca_xbrl', 'nse_filing', 'bse_filing',
                            'annual_report_pdf', 'screener', 'fmp_api',
                            'alpha_vantage', 'manual', 'other')),
    source_url          TEXT,
    source_document     TEXT,               -- file path / S3 key
    source_page_ref     VARCHAR(100),       -- e.g. "Page 112, Annual Report FY24"
    source_confidence   NUMERIC(4,3) CHECK (source_confidence BETWEEN 0 AND 1),
    -- Audit fields
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    last_refreshed_at   TIMESTAMPTZ DEFAULT NOW(),
    is_restated         BOOLEAN DEFAULT FALSE,
    restatement_note    TEXT,
    data_quality_score  NUMERIC(4,3) CHECK (data_quality_score BETWEEN 0 AND 1),
    UNIQUE(company_id, fiscal_year, statement_type, consolidation, source_type)
);

CREATE INDEX IF NOT EXISTS idx_fs_company_year  ON financial_statements(company_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_fs_type          ON financial_statements(statement_type);
CREATE INDEX IF NOT EXISTS idx_fs_source        ON financial_statements(source_type);

-- ============================================================
-- 3. FINANCIAL LINE ITEMS (maximum granularity)
-- ============================================================
CREATE TABLE IF NOT EXISTS financial_line_items (
    line_item_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    statement_id        UUID NOT NULL REFERENCES financial_statements(statement_id) ON DELETE CASCADE,
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,

    -- Original label exactly as reported in source
    original_label      TEXT NOT NULL,
    original_label_norm TEXT GENERATED ALWAYS AS (lower(trim(original_label))) STORED,

    -- Standardized label (mapped by normalization engine)
    standardized_label  VARCHAR(200),
    label_category      VARCHAR(50),    -- 'revenue', 'cost', 'expense', 'asset', etc.
    label_subcategory   VARCHAR(100),

    -- Hierarchy within the statement (e.g. "Other Expenses > Power & Fuel")
    parent_label        TEXT,
    hierarchy_path      TEXT,           -- slash-delimited, e.g. "expenses/manufacturing/power_fuel"
    hierarchy_level     SMALLINT DEFAULT 1,
    sort_order          SMALLINT,

    -- Values
    reported_value      NUMERIC(25, 4),
    reported_unit       VARCHAR(20) DEFAULT 'crores',
    reported_currency   VARCHAR(3)  DEFAULT 'INR',

    -- Standardized value (always INR crores for comparability)
    std_value_inr_cr    NUMERIC(25, 4),

    -- Derived vs directly reported
    is_derived          BOOLEAN DEFAULT FALSE,
    derivation_formula  TEXT,           -- e.g. "EBIT = PBT + Interest"
    derivation_inputs   TEXT[],         -- array of source line_item_ids

    -- Data quality
    mapping_confidence  NUMERIC(4,3) CHECK (mapping_confidence BETWEEN 0 AND 1),
    is_estimated        BOOLEAN DEFAULT FALSE,
    estimation_method   TEXT,
    source_note         TEXT,

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fli_statement        ON financial_line_items(statement_id);
CREATE INDEX IF NOT EXISTS idx_fli_company_year     ON financial_line_items(company_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_fli_std_label        ON financial_line_items(standardized_label);
CREATE INDEX IF NOT EXISTS idx_fli_orig_label_trgm  ON financial_line_items USING GIN(original_label_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_fli_category         ON financial_line_items(label_category);

-- ============================================================
-- 4. LABEL MAPPING DICTIONARY (extensible)
-- ============================================================
CREATE TABLE IF NOT EXISTS label_mappings (
    mapping_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    original_label_norm TEXT NOT NULL,          -- normalized source label
    standardized_label  VARCHAR(200) NOT NULL,
    category            VARCHAR(50),
    subcategory         VARCHAR(100),
    confidence_default  NUMERIC(4,3) DEFAULT 0.9,
    match_type          VARCHAR(20) CHECK (match_type IN (
                            'exact', 'fuzzy', 'regex', 'manual', 'ml')),
    regex_pattern       TEXT,
    aliases             TEXT[],                 -- alternative forms
    source_count        INTEGER DEFAULT 1,      -- how many times seen in corpus
    last_seen_at        TIMESTAMPTZ DEFAULT NOW(),
    created_by          VARCHAR(100) DEFAULT 'system',
    notes               TEXT,
    UNIQUE(original_label_norm, standardized_label)
);

CREATE INDEX IF NOT EXISTS idx_lm_orig_label    ON label_mappings(original_label_norm);
CREATE INDEX IF NOT EXISTS idx_lm_std_label     ON label_mappings(standardized_label);
CREATE INDEX IF NOT EXISTS idx_lm_aliases       ON label_mappings USING GIN(aliases);

-- ============================================================
-- 5. COMMON-SIZE METRICS (pre-computed, per company-year)
-- ============================================================
CREATE TABLE IF NOT EXISTS common_size_metrics (
    cs_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,
    consolidation       VARCHAR(20) DEFAULT 'consolidated',

    metric_name         VARCHAR(200) NOT NULL,  -- standardized label
    original_label      TEXT,                   -- source label for traceability

    raw_value_inr_cr    NUMERIC(25, 4),
    revenue_base_inr_cr NUMERIC(25, 4),         -- denominator used
    common_size_pct     NUMERIC(10, 6),         -- metric / revenue * 100

    is_derived          BOOLEAN DEFAULT FALSE,
    computed_at         TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(company_id, fiscal_year, metric_name, consolidation)
);

CREATE INDEX IF NOT EXISTS idx_cs_company_year  ON common_size_metrics(company_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_cs_metric        ON common_size_metrics(metric_name);

-- ============================================================
-- 6. PEER GROUP DEFINITIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS peer_groups (
    peer_group_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                VARCHAR(200) NOT NULL,
    description         TEXT,
    group_type          VARCHAR(30) CHECK (group_type IN (
                            'sector', 'custom', 'market_cap_band', 'index')),
    sector              VARCHAR(100),
    created_by          VARCHAR(100),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS peer_group_members (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    peer_group_id       UUID NOT NULL REFERENCES peer_groups(peer_group_id) ON DELETE CASCADE,
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    added_at            TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(peer_group_id, company_id)
);

-- ============================================================
-- 7. PEER GROUP AGGREGATE METRICS
-- ============================================================
CREATE TABLE IF NOT EXISTS peer_group_metrics (
    pgm_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    peer_group_id       UUID NOT NULL REFERENCES peer_groups(peer_group_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,
    metric_name         VARCHAR(200) NOT NULL,

    -- Aggregate statistics
    equal_weight_avg    NUMERIC(15, 6),
    mktcap_weight_avg   NUMERIC(15, 6),
    median_val          NUMERIC(15, 6),
    std_dev             NUMERIC(15, 6),
    min_val             NUMERIC(15, 6),
    max_val             NUMERIC(15, 6),
    p25                 NUMERIC(15, 6),
    p75                 NUMERIC(15, 6),
    count_companies     SMALLINT,

    -- Metadata
    computed_at         TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(peer_group_id, fiscal_year, metric_name)
);

CREATE INDEX IF NOT EXISTS idx_pgm_group_year   ON peer_group_metrics(peer_group_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_pgm_metric       ON peer_group_metrics(metric_name);

-- ============================================================
-- 8. TIME-SERIES ANALYTICS
-- ============================================================
CREATE TABLE IF NOT EXISTS time_series_metrics (
    ts_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    metric_name         VARCHAR(200) NOT NULL,
    base_year           SMALLINT,
    end_year            SMALLINT,

    -- Growth metrics
    yoy_growth_pct      NUMERIC(15, 6),     -- latest year YoY
    cagr_pct            NUMERIC(15, 6),     -- over full period
    cagr_years          SMALLINT,

    -- Trend
    rolling_3yr_avg     NUMERIC(15, 6),
    rolling_5yr_avg     NUMERIC(15, 6),
    volatility_std_dev  NUMERIC(15, 6),     -- std dev of annual values

    -- Raw array (JSONB for flexible length)
    annual_values       JSONB,              -- {"2015": 12.3, "2016": 13.1, ...}

    computed_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, metric_name, base_year, end_year)
);

CREATE INDEX IF NOT EXISTS idx_ts_company   ON time_series_metrics(company_id);
CREATE INDEX IF NOT EXISTS idx_ts_metric    ON time_series_metrics(metric_name);

-- ============================================================
-- 9. PRODUCT MIX / SEGMENT REPORTING
-- ============================================================
CREATE TABLE IF NOT EXISTS product_mix (
    mix_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,

    -- Segment details (exactly as reported)
    segment_name        TEXT NOT NULL,          -- as reported, no normalization
    parent_segment      TEXT,                   -- for hierarchical segments
    hierarchy_level     SMALLINT DEFAULT 1,

    -- Revenue data
    segment_revenue_inr_cr  NUMERIC(20, 4),
    total_revenue_inr_cr    NUMERIC(20, 4),
    revenue_share_pct       NUMERIC(8, 4),      -- % of total
    segment_ebit_inr_cr     NUMERIC(20, 4),
    ebit_margin_pct         NUMERIC(8, 4),

    -- Source (STRICT: only official filings)
    source_document     TEXT NOT NULL,          -- "Annual Report FY2024"
    source_page_ref     TEXT,                   -- "Page 145, Note 32"
    source_type         VARCHAR(30) CHECK (source_type IN (
                            'annual_report', 'mca_xbrl', 'nse_filing',
                            'bse_filing', 'investor_presentation')),
    disclosure_type     VARCHAR(50),            -- 'segment_reporting', 'md&a', 'other'

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, fiscal_year, segment_name, source_type)
);

CREATE INDEX IF NOT EXISTS idx_pm_company_year  ON product_mix(company_id, fiscal_year);

-- ============================================================
-- 10. EXPORT INTENSITY
-- ============================================================
CREATE TABLE IF NOT EXISTS export_intensity (
    export_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,

    -- Revenue split (only directly disclosed values)
    export_revenue_inr_cr   NUMERIC(20, 4),
    domestic_revenue_inr_cr NUMERIC(20, 4),
    total_revenue_inr_cr    NUMERIC(20, 4),
    export_pct              NUMERIC(8, 4) GENERATED ALWAYS AS (
                                CASE WHEN total_revenue_inr_cr > 0
                                THEN export_revenue_inr_cr / total_revenue_inr_cr * 100
                                ELSE NULL END) STORED,

    -- Geographic breakdown (JSONB for variable structure)
    geographic_breakdown    JSONB,  -- {"North America": 45.2, "Europe": 30.1, ...}

    -- Source (STRICT: only official disclosures)
    source_document         TEXT NOT NULL,
    source_page_ref         TEXT,
    source_type             VARCHAR(30) CHECK (source_type IN (
                                'annual_report', 'mca_xbrl', 'nse_filing',
                                'bse_filing', 'investor_presentation')),
    disclosure_label        TEXT,   -- exact label used in filing

    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, fiscal_year, source_type)
);

CREATE INDEX IF NOT EXISTS idx_ei_company_year  ON export_intensity(company_id, fiscal_year);

-- ============================================================
-- 11. DATA INGESTION JOBS
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    peer_group_id       UUID REFERENCES peer_groups(peer_group_id),
    company_ids         UUID[],
    requested_years     SMALLINT[],
    status              VARCHAR(20) DEFAULT 'pending' CHECK (status IN (
                            'pending', 'running', 'partial', 'completed', 'failed')),
    provider_priority   TEXT[],     -- ordered list of providers to try
    progress_pct        NUMERIC(5,2) DEFAULT 0,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    error_log           JSONB,
    summary             JSONB,      -- {"total": 10, "success": 8, "failed": 2, ...}
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 12. DATA QUALITY AUDIT
-- ============================================================
CREATE TABLE IF NOT EXISTS data_quality_audit (
    audit_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id          UUID NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    fiscal_year         SMALLINT NOT NULL,
    statement_type      VARCHAR(30),

    -- Coverage
    total_line_items    SMALLINT,
    mapped_line_items   SMALLINT,
    derived_items       SMALLINT,
    missing_items       TEXT[],         -- standardized labels that are absent

    -- Confidence
    avg_mapping_confidence  NUMERIC(4,3),
    source_reliability      NUMERIC(4,3),   -- provider-level score
    overall_quality_score   NUMERIC(4,3),

    -- Flags
    has_revenue         BOOLEAN DEFAULT FALSE,
    has_ebitda          BOOLEAN DEFAULT FALSE,
    has_pat             BOOLEAN DEFAULT FALSE,
    has_balance_sheet   BOOLEAN DEFAULT FALSE,
    has_cashflow        BOOLEAN DEFAULT FALSE,

    assessed_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(company_id, fiscal_year, statement_type)
);

CREATE INDEX IF NOT EXISTS idx_dqa_company_year ON data_quality_audit(company_id, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_dqa_quality      ON data_quality_audit(overall_quality_score DESC);

-- ============================================================
-- 13. MATERIALIZED VIEWS (for dashboard speed)
-- ============================================================

-- Common-size P&L summary (top 20 metrics per company-year)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_common_size_pl AS
SELECT
    c.company_id,
    c.name AS company_name,
    c.nse_ticker,
    c.sector,
    cs.fiscal_year,
    cs.metric_name,
    cs.raw_value_inr_cr,
    cs.common_size_pct,
    cs.revenue_base_inr_cr
FROM common_size_metrics cs
JOIN companies c ON c.company_id = cs.company_id
WHERE cs.metric_name IN (
    'revenue', 'raw_materials_consumed', 'purchase_of_stock_in_trade',
    'employee_benefits_expense', 'power_and_fuel', 'freight_and_logistics',
    'advertising_and_promotion', 'other_operating_expenses',
    'ebitda', 'depreciation_and_amortization', 'ebit',
    'finance_costs', 'pbt', 'tax_expense', 'pat'
)
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_csp ON mv_common_size_pl(company_id, fiscal_year, metric_name);

-- Peer group dashboard summary
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_peer_group_summary AS
SELECT
    pg.name AS peer_group_name,
    pgm.fiscal_year,
    pgm.metric_name,
    pgm.equal_weight_avg,
    pgm.mktcap_weight_avg,
    pgm.median_val,
    pgm.std_dev,
    pgm.p25,
    pgm.p75,
    pgm.count_companies
FROM peer_group_metrics pgm
JOIN peer_groups pg ON pg.peer_group_id = pgm.peer_group_id
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS uidx_mv_pgs ON mv_peer_group_summary(peer_group_name, fiscal_year, metric_name);

-- ============================================================
-- 14. UTILITY FUNCTIONS
-- ============================================================

-- Auto-update updated_at on companies
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_companies_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_peer_groups_updated_at
    BEFORE UPDATE ON peer_groups
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- Function to refresh all materialized views
CREATE OR REPLACE FUNCTION refresh_materialized_views()
RETURNS void AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY mv_common_size_pl;
  REFRESH MATERIALIZED VIEW CONCURRENTLY mv_peer_group_summary;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- SEED: Core label mappings (extensible)
-- ============================================================
INSERT INTO label_mappings (original_label_norm, standardized_label, category, subcategory, confidence_default, match_type)
VALUES
  -- Revenue
  ('revenue from operations', 'revenue', 'revenue', 'operating', 0.99, 'exact'),
  ('net sales', 'revenue', 'revenue', 'operating', 0.99, 'exact'),
  ('sales', 'revenue', 'revenue', 'operating', 0.95, 'exact'),
  ('turnover', 'revenue', 'revenue', 'operating', 0.95, 'exact'),
  ('total income from operations', 'revenue', 'revenue', 'operating', 0.95, 'exact'),
  ('net revenue from operations', 'revenue', 'revenue', 'operating', 0.99, 'exact'),
  ('gross revenue', 'gross_revenue', 'revenue', 'gross', 0.90, 'exact'),
  -- COGS / Materials
  ('cost of materials consumed', 'raw_materials_consumed', 'cost', 'materials', 0.98, 'exact'),
  ('raw materials consumed', 'raw_materials_consumed', 'cost', 'materials', 0.99, 'exact'),
  ('purchase of stock-in-trade', 'purchase_of_stock_in_trade', 'cost', 'materials', 0.99, 'exact'),
  ('purchases of stock-in-trade', 'purchase_of_stock_in_trade', 'cost', 'materials', 0.99, 'exact'),
  ('changes in inventories of finished goods', 'inventory_changes', 'cost', 'materials', 0.98, 'exact'),
  ('changes in inventories', 'inventory_changes', 'cost', 'materials', 0.95, 'exact'),
  -- Employee costs
  ('employee benefits expense', 'employee_benefits_expense', 'expense', 'employee', 0.99, 'exact'),
  ('staff costs', 'employee_benefits_expense', 'expense', 'employee', 0.95, 'exact'),
  ('salaries and wages', 'salaries_and_wages', 'expense', 'employee', 0.99, 'exact'),
  ('salaries, wages and bonus', 'salaries_and_wages', 'expense', 'employee', 0.99, 'exact'),
  ('provident fund contribution', 'pf_and_gratuity', 'expense', 'employee', 0.95, 'exact'),
  ('gratuity expense', 'pf_and_gratuity', 'expense', 'employee', 0.95, 'exact'),
  ('esop expense', 'esop_expense', 'expense', 'employee', 0.95, 'exact'),
  -- Manufacturing
  ('power and fuel', 'power_and_fuel', 'expense', 'manufacturing', 0.99, 'exact'),
  ('power, fuel and water', 'power_and_fuel', 'expense', 'manufacturing', 0.98, 'exact'),
  ('repairs and maintenance', 'repairs_and_maintenance', 'expense', 'manufacturing', 0.99, 'exact'),
  ('contract labour charges', 'contract_labour', 'expense', 'manufacturing', 0.98, 'exact'),
  ('processing charges', 'processing_charges', 'expense', 'manufacturing', 0.95, 'exact'),
  ('packing and forwarding', 'freight_and_logistics', 'expense', 'logistics', 0.90, 'exact'),
  ('freight and forwarding', 'freight_and_logistics', 'expense', 'logistics', 0.98, 'exact'),
  ('freight outward', 'freight_and_logistics', 'expense', 'logistics', 0.95, 'exact'),
  ('logistics cost', 'freight_and_logistics', 'expense', 'logistics', 0.95, 'exact'),
  -- SGA
  ('advertising and sales promotion', 'advertising_and_promotion', 'expense', 'sga', 0.98, 'exact'),
  ('advertisement and publicity', 'advertising_and_promotion', 'expense', 'sga', 0.98, 'exact'),
  ('professional charges', 'professional_fees', 'expense', 'sga', 0.95, 'exact'),
  ('professional fees', 'professional_fees', 'expense', 'sga', 0.99, 'exact'),
  ('legal and professional fees', 'professional_fees', 'expense', 'sga', 0.98, 'exact'),
  ('rent', 'rent_expense', 'expense', 'sga', 0.99, 'exact'),
  ('rent, rates and taxes', 'rent_expense', 'expense', 'sga', 0.95, 'exact'),
  ('travel and conveyance', 'travel_expense', 'expense', 'sga', 0.98, 'exact'),
  ('communication expenses', 'communication_expense', 'expense', 'sga', 0.95, 'exact'),
  ('insurance', 'insurance_expense', 'expense', 'sga', 0.99, 'exact'),
  ('commission on sales', 'commission_expense', 'expense', 'sga', 0.98, 'exact'),
  ('royalty', 'royalty_expense', 'expense', 'sga', 0.99, 'exact'),
  ('research and development expenses', 'research_and_development', 'expense', 'rd', 0.99, 'exact'),
  ('r&d expenses', 'research_and_development', 'expense', 'rd', 0.95, 'exact'),
  -- IT-specific
  ('software expenses', 'software_expense', 'expense', 'it', 0.98, 'exact'),
  ('subcontracting expenses', 'subcontracting_expense', 'expense', 'it', 0.98, 'exact'),
  ('visa and immigration', 'visa_expense', 'expense', 'it', 0.98, 'exact'),
  -- D&A
  ('depreciation and amortisation expense', 'depreciation_and_amortization', 'expense', 'non_cash', 0.99, 'exact'),
  ('depreciation and amortization', 'depreciation_and_amortization', 'expense', 'non_cash', 0.99, 'exact'),
  ('depreciation', 'depreciation_and_amortization', 'expense', 'non_cash', 0.95, 'exact'),
  -- Finance
  ('finance costs', 'finance_costs', 'expense', 'finance', 0.99, 'exact'),
  ('interest expense', 'finance_costs', 'expense', 'finance', 0.95, 'exact'),
  ('interest and finance charges', 'finance_costs', 'expense', 'finance', 0.98, 'exact'),
  -- Tax
  ('tax expense', 'tax_expense', 'expense', 'tax', 0.99, 'exact'),
  ('income tax expense', 'tax_expense', 'expense', 'tax', 0.99, 'exact'),
  -- Profit lines
  ('profit before tax', 'pbt', 'profit', 'pbt', 0.99, 'exact'),
  ('profit/(loss) before tax', 'pbt', 'profit', 'pbt', 0.99, 'exact'),
  ('profit after tax', 'pat', 'profit', 'pat', 0.99, 'exact'),
  ('profit for the year', 'pat', 'profit', 'pat', 0.99, 'exact'),
  ('profit/(loss) for the year', 'pat', 'profit', 'pat', 0.98, 'exact'),
  -- Balance sheet
  ('total assets', 'total_assets', 'asset', 'total', 0.99, 'exact'),
  ('fixed assets', 'fixed_assets', 'asset', 'fixed', 0.95, 'exact'),
  ('property, plant and equipment', 'property_plant_equipment', 'asset', 'fixed', 0.99, 'exact'),
  ('capital work-in-progress', 'cwip', 'asset', 'fixed', 0.99, 'exact'),
  ('total current assets', 'current_assets', 'asset', 'current', 0.99, 'exact'),
  ('inventories', 'inventories', 'asset', 'current', 0.99, 'exact'),
  ('trade receivables', 'trade_receivables', 'asset', 'current', 0.99, 'exact'),
  ('cash and cash equivalents', 'cash_and_equivalents', 'asset', 'current', 0.99, 'exact'),
  ('short-term investments', 'short_term_investments', 'asset', 'current', 0.98, 'exact'),
  ('total equity', 'total_equity', 'equity', 'total', 0.99, 'exact'),
  ('shareholders equity', 'total_equity', 'equity', 'total', 0.95, 'exact'),
  ('total debt', 'total_debt', 'liability', 'debt', 0.95, 'exact'),
  ('long-term borrowings', 'long_term_debt', 'liability', 'debt', 0.99, 'exact'),
  ('short-term borrowings', 'short_term_debt', 'liability', 'debt', 0.99, 'exact'),
  ('trade payables', 'trade_payables', 'liability', 'current', 0.99, 'exact'),
  ('total current liabilities', 'current_liabilities', 'liability', 'current', 0.99, 'exact')
ON CONFLICT (original_label_norm, standardized_label) DO NOTHING;
