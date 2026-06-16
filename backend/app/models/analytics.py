"""
SQLAlchemy ORM models for analytics outputs, product mix, export intensity,
data quality, and ingestion job tracking.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Numeric, SmallInteger,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CommonSizeMetric(Base):
    __tablename__ = "common_size_metrics"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "metric_name", "consolidation",
            name="uq_cs_company_year_metric",
        ),
    )

    cs_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    consolidation: Mapped[str] = mapped_column(String(20), default="consolidated")
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    original_label: Mapped[Optional[str]] = mapped_column(Text)
    raw_value_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(25, 4))
    revenue_base_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(25, 4))
    common_size_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    is_derived: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PeerGroupMetric(Base):
    __tablename__ = "peer_group_metrics"
    __table_args__ = (
        UniqueConstraint(
            "peer_group_id", "fiscal_year", "metric_name",
            name="uq_pgm_group_year_metric",
        ),
    )

    pgm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    peer_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("peer_groups.peer_group_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    equal_weight_avg: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    mktcap_weight_avg: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    median_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    std_dev: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    min_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    max_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    p25: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    p75: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    count_companies: Mapped[Optional[int]] = mapped_column(SmallInteger)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    peer_group: Mapped["PeerGroup"] = relationship("PeerGroup", back_populates="metrics")


class TimeSeriesMetric(Base):
    __tablename__ = "time_series_metrics"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "metric_name", "base_year", "end_year",
            name="uq_ts_company_metric_period",
        ),
    )

    ts_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    metric_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    base_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    end_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    yoy_growth_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    cagr_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    cagr_years: Mapped[Optional[int]] = mapped_column(SmallInteger)
    rolling_3yr_avg: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    rolling_5yr_avg: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    volatility_std_dev: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6))
    annual_values: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductMix(Base):
    __tablename__ = "product_mix"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "segment_name", "source_type",
            name="uq_pm_company_year_segment",
        ),
    )

    mix_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    segment_name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_segment: Mapped[Optional[str]] = mapped_column(Text)
    hierarchy_level: Mapped[int] = mapped_column(SmallInteger, default=1)
    segment_revenue_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    total_revenue_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    revenue_share_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    segment_ebit_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    ebit_margin_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    source_document: Mapped[str] = mapped_column(Text, nullable=False)
    source_page_ref: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[Optional[str]] = mapped_column(String(30))
    disclosure_type: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExportIntensity(Base):
    __tablename__ = "export_intensity"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "source_type",
            name="uq_ei_company_year_source",
        ),
    )

    export_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    export_revenue_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    domestic_revenue_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    total_revenue_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4))
    geographic_breakdown: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    source_document: Mapped[str] = mapped_column(Text, nullable=False)
    source_page_ref: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[Optional[str]] = mapped_column(String(30))
    disclosure_label: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def export_pct(self) -> Optional[Decimal]:
        if self.total_revenue_inr_cr and self.total_revenue_inr_cr > 0:
            return (self.export_revenue_inr_cr / self.total_revenue_inr_cr) * 100
        return None


class DataQualityAudit(Base):
    __tablename__ = "data_quality_audit"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "statement_type",
            name="uq_dqa_company_year_type",
        ),
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    statement_type: Mapped[Optional[str]] = mapped_column(String(30))
    total_line_items: Mapped[Optional[int]] = mapped_column(SmallInteger)
    mapped_line_items: Mapped[Optional[int]] = mapped_column(SmallInteger)
    derived_items: Mapped[Optional[int]] = mapped_column(SmallInteger)
    missing_items: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    avg_mapping_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    source_reliability: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    overall_quality_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    has_revenue: Mapped[bool] = mapped_column(Boolean, default=False)
    has_ebitda: Mapped[bool] = mapped_column(Boolean, default=False)
    has_pat: Mapped[bool] = mapped_column(Boolean, default=False)
    has_balance_sheet: Mapped[bool] = mapped_column(Boolean, default=False)
    has_cashflow: Mapped[bool] = mapped_column(Boolean, default=False)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    peer_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("peer_groups.peer_group_id"), nullable=True
    )
    company_ids: Mapped[Optional[List[uuid.UUID]]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    requested_years: Mapped[Optional[List[int]]] = mapped_column(ARRAY(SmallInteger))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider_priority: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text))
    progress_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_log: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    summary: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
