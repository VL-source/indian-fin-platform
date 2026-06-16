"""
SQLAlchemy ORM models for companies and peer groups.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Numeric, SmallInteger,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nse_ticker: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True)
    bse_code: Mapped[Optional[str]] = mapped_column(String(10), unique=True, index=True)
    isin: Mapped[Optional[str]] = mapped_column(String(12), unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    industry: Mapped[Optional[str]] = mapped_column(String(100))
    sub_industry: Mapped[Optional[str]] = mapped_column(String(150))
    market_cap_inr_cr: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    market_cap_date: Mapped[Optional[date]] = mapped_column(Date)
    listing_exchange: Mapped[Optional[str]] = mapped_column(String(10))
    listing_date: Mapped[Optional[date]] = mapped_column(Date)
    face_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    cin: Mapped[Optional[str]] = mapped_column(String(21))
    registered_office: Mapped[Optional[str]] = mapped_column(Text)
    website: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    statements: Mapped[List["FinancialStatement"]] = relationship(
        "FinancialStatement", back_populates="company", cascade="all, delete-orphan"
    )
    peer_memberships: Mapped[List["PeerGroupMember"]] = relationship(
        "PeerGroupMember", back_populates="company"
    )

    def __repr__(self) -> str:
        return f"<Company {self.nse_ticker or self.bse_code}: {self.name}>"

    @property
    def ticker(self) -> str:
        return self.nse_ticker or self.bse_code or self.name


class PeerGroup(Base):
    __tablename__ = "peer_groups"

    peer_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    group_type: Mapped[Optional[str]] = mapped_column(String(30))
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    created_by: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[List["PeerGroupMember"]] = relationship(
        "PeerGroupMember", back_populates="peer_group", cascade="all, delete-orphan"
    )
    metrics: Mapped[List["PeerGroupMetric"]] = relationship(
        "PeerGroupMetric", back_populates="peer_group", cascade="all, delete-orphan"
    )


class PeerGroupMember(Base):
    __tablename__ = "peer_group_members"
    __table_args__ = (UniqueConstraint("peer_group_id", "company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    peer_group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("peer_groups.peer_group_id", ondelete="CASCADE")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.company_id", ondelete="CASCADE")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    peer_group: Mapped["PeerGroup"] = relationship("PeerGroup", back_populates="members")
    company: Mapped["Company"] = relationship("Company", back_populates="peer_memberships")
