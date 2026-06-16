"""
SQLAlchemy ORM models for financial statements, line items, and label mappings.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Numeric, SmallInteger,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FinancialStatement(Base):
    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fiscal_year", "statement_type",
            "consolidation", "source_type",
            name="uq_fs_company_year_type_source",
        ),
    )

    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    statement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reporting_currency: Mapped[str] = mapped_column(String(3), default="INR")
    reporting_unit: Mapped[str] = mapped_column(String(20), default="crores")
    consolidation: Mapped[str] = mapped_column(String(20), default="consolidated")
    source_type: Mapped[Optional[str]] = mapped_column(String(30))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    source_document: Mapped[Optional[str]] = mapped_column(Text)
    source_page_ref: Mapped[Optional[str]] = mapped_column(String(100))
    source_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_restated: Mapped[bool] = mapped_column(Boolean, default=False)
    restatement_note: Mapped[Optional[str]] = mapped_column(Text)
    data_quality_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="statements")
    line_items: Mapped[List["FinancialLineItem"]] = relationship(
        "FinancialLineItem",
        back_populates="statement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<FinancialStatement company={self.company_id} "
            f"year={self.fiscal_year} type={self.statement_type}>"
        )


class FinancialLineItem(Base):
    __tablename__ = "financial_line_items"

    line_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financial_statements.statement_id", ondelete="CASCADE"),
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.company_id", ondelete="CASCADE"),
        index=True,
    )
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Labels
    original_label: Mapped[str] = mapped_column(Text, nullable=False)
    standardized_label: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    label_category: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    label_subcategory: Mapped[Optional[str]] = mapped_column(String(100))

    # Hierarchy
    parent_label: Mapped[Optional[str]] = mapped_column(Text)
    hierarchy_path: Mapped[Optional[str]] = mapped_column(Text)
    hierarchy_level: Mapped[int] = mapped_column(SmallInteger, default=1)
    sort_order: Mapped[Optional[int]] = mapped_column(SmallInteger)

    # Values
    reported_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(25, 4))
    reported_unit: Mapped[str] = mapped_column(String(20), default="crores")
    reported_currency: Mapped[str] = mapped_column(String(3), default="INR")
    std_value_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(25, 4))

    # Derivation
    is_derived: Mapped[bool] = mapped_column(Boolean, default=False)
    derivation_formula: Mapped[Optional[str]] = mapped_column(Text)

    # Quality
    mapping_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 3))
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    estimation_method: Mapped[Optional[str]] = mapped_column(Text)
    source_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    statement: Mapped["FinancialStatement"] = relationship(
        "FinancialStatement", back_populates="line_items"
    )

    def __repr__(self) -> str:
        return (
            f"<LineItem {self.original_label!r} → "
            f"{self.standardized_label!r}: {self.std_value_inr_cr}>"
        )


class LabelMapping(Base):
    __tablename__ = "label_mappings"
    __table_args__ = (
        UniqueConstraint(
            "original_label_norm", "standardized_label",
            name="uq_lm_orig_std",
        ),
    )

    mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    original_label_norm: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    standardized_label: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(50))
    subcategory: Mapped[Optional[str]] = mapped_column(String(100))
    confidence_default: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.9"))
    match_type: Mapped[Optional[str]] = mapped_column(String(20))
    regex_pattern: Mapped[Optional[str]] = mapped_column(Text)
    aliases: Mapped[Optional[list]] = mapped_column(ARRAY(Text))
    source_count: Mapped[int] = mapped_column(default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    notes: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<LabelMapping {self.original_label_norm!r} → {self.standardized_label!r}>"
