"""SQLAlchemy 2.0 ORM models for Rahasya storage layer.

Defines the database schema using modern SQLAlchemy Mapped classes.
"""
import uuid
import enum
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    String, Integer, Float, DateTime, ForeignKey, 
    UniqueConstraint, Enum as SQLEnum, JSON, Text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from rahasya.core.models import ScanStatus


def utcnow() -> datetime:
    """Helper to get current UTC datetime."""
    return datetime.now(timezone.utc)


class ModuleExecutionStatus(enum.Enum):
    """Status of a module execution."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class BaseSQLModel(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


class ScanRecord(BaseSQLModel):
    """Record of a scan operation."""
    __tablename__ = 'scans'

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[ScanStatus] = mapped_column(SQLEnum(ScanStatus), nullable=False, default=ScanStatus.PENDING)
    request_data: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    total_entities: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_relationships: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depth_reached: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    entities: Mapped[list["EntityRecord"]] = relationship(
        "EntityRecord", back_populates="scan", lazy="selectin", cascade="all, delete-orphan"
    )
    relationships: Mapped[list["RelationshipRecord"]] = relationship(
        "RelationshipRecord", back_populates="scan", lazy="selectin", cascade="all, delete-orphan"
    )
    module_executions: Mapped[list["ModuleExecutionRecord"]] = relationship(
        "ModuleExecutionRecord", back_populates="scan", lazy="selectin", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ScanRecord(id={self.id}, status={self.status.name})>"


class EntityRecord(BaseSQLModel):
    """Record of an entity discovered during a scan."""
    __tablename__ = 'entities'

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scans.id', ondelete="CASCADE"), nullable=False)
    
    entity_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    normalized_value: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_module: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    
    parent_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('entities.id', ondelete="SET NULL"), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    scan: Mapped["ScanRecord"] = relationship("ScanRecord", back_populates="entities")

    __table_args__ = (
        UniqueConstraint('scan_id', 'entity_type', 'normalized_value', name='uix_scan_entity'),
    )

    def __repr__(self) -> str:
        return f"<EntityRecord(id={self.id}, type={self.entity_type}, value='{self.value}')>"


class RelationshipRecord(BaseSQLModel):
    """Record of a relationship between two entities."""
    __tablename__ = 'relationships'

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scans.id', ondelete="CASCADE"), nullable=False, index=True)
    
    source_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('entities.id', ondelete="CASCADE"), nullable=False, index=True)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('entities.id', ondelete="CASCADE"), nullable=False, index=True)
    
    relationship_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_module: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    scan: Mapped["ScanRecord"] = relationship("ScanRecord", back_populates="relationships")

    def __repr__(self) -> str:
        return f"<RelationshipRecord(id={self.id}, type={self.relationship_type})>"


class ModuleExecutionRecord(BaseSQLModel):
    """Record of a specific module execution for a scan."""
    __tablename__ = 'module_executions'

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('scans.id', ondelete="CASCADE"), nullable=False, index=True)
    
    module_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('entities.id', ondelete="CASCADE"), nullable=True, index=True)
    
    status: Mapped[ModuleExecutionStatus] = mapped_column(SQLEnum(ModuleExecutionStatus), nullable=False, default=ModuleExecutionStatus.PENDING)
    
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    entities_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    scan: Mapped["ScanRecord"] = relationship("ScanRecord", back_populates="module_executions")

    def __repr__(self) -> str:
        return f"<ModuleExecutionRecord(id={self.id}, module={self.module_name}, status={self.status.name})>"
