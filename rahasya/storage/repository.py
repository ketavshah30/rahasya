"""Data access layer repositories.

Implements the repository pattern for abstracting database operations
related to scans, entities, relationships, and module executions.
"""
import uuid
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime, timezone
from loguru import logger
from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from rahasya.core.models import ScanStatus
from rahasya.storage.sql_models import (
    ScanRecord, EntityRecord, RelationshipRecord, 
    ModuleExecutionRecord, ModuleExecutionStatus
)
from rahasya.storage.database import db_manager


class BaseRepository:
    """Base repository class providing common DB session utility."""
    
    @property
    def db(self):
        """Get the DB manager context."""
        return db_manager


class ScanRepository(BaseRepository):
    """Repository for managing scan records."""

    async def create_scan(self, request_data: Dict[str, Any], config: Dict[str, Any]) -> ScanRecord:
        """Create a new scan record."""
        async with self.db.get_session() as session:
            scan = ScanRecord(
                request_data=request_data,
                config=config,
                status=ScanStatus.PENDING,
            )
            session.add(scan)
            # flush to get ID before commit if needed
            await session.flush()
            logger.info(f"Created scan record: {scan.id}")
            return scan

    async def get_scan(self, scan_id: uuid.UUID) -> Optional[ScanRecord]:
        """Retrieve a scan by ID."""
        async with self.db.get_session() as session:
            stmt = select(ScanRecord).where(ScanRecord.id == scan_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_scan_status(self, scan_id: uuid.UUID, status: ScanStatus, error_message: Optional[str] = None) -> Optional[ScanRecord]:
        """Update the status of a scan."""
        async with self.db.get_session() as session:
            scan = await self.get_scan(scan_id)
            if not scan:
                return None
            
            # Using update statement for efficiency or just updating the object
            stmt = update(ScanRecord).where(ScanRecord.id == scan_id).values(status=status)
            if status == ScanStatus.RUNNING and scan.status != ScanStatus.RUNNING:
                stmt = stmt.values(started_at=datetime.now(timezone.utc))
            elif status in (ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED):
                stmt = stmt.values(completed_at=datetime.now(timezone.utc))
            
            if error_message:
                stmt = stmt.values(error_message=error_message)

            await session.execute(stmt)
            logger.info(f"Updated scan {scan_id} status to {status.name}")
            return await self.get_scan(scan_id)

    async def list_scans(self, limit: int = 100, offset: int = 0) -> Sequence[ScanRecord]:
        """List scans with pagination."""
        async with self.db.get_session() as session:
            stmt = select(ScanRecord).order_by(ScanRecord.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return result.scalars().all()


class EntityRepository(BaseRepository):
    """Repository for managing entity records."""

    async def create_entity(self, scan_id: uuid.UUID, entity_data: Dict[str, Any]) -> EntityRecord:
        """Create a new entity record."""
        async with self.db.get_session() as session:
            entity = EntityRecord(
                scan_id=scan_id,
                entity_type=entity_data['type'],
                value=entity_data['value'],
                normalized_value=entity_data.get('normalized_value', entity_data['value'].lower()),
                source_module=entity_data['source_module'],
                confidence=entity_data.get('confidence', 1.0),
                metadata_json=entity_data.get('metadata', {}),
                parent_entity_id=entity_data.get('parent_entity_id'),
                depth=entity_data.get('depth', 0)
            )
            session.add(entity)
            await session.flush()
            
            # Update scan stats
            await session.execute(
                update(ScanRecord)
                .where(ScanRecord.id == scan_id)
                .values(total_entities=ScanRecord.total_entities + 1)
            )
            
            return entity

    async def get_entity(self, entity_id: uuid.UUID) -> Optional[EntityRecord]:
        """Retrieve an entity by ID."""
        async with self.db.get_session() as session:
            stmt = select(EntityRecord).where(EntityRecord.id == entity_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def find_by_value(self, scan_id: uuid.UUID, entity_type: str, normalized_value: str) -> Optional[EntityRecord]:
        """Find an entity by its normalized value within a scan."""
        async with self.db.get_session() as session:
            stmt = select(EntityRecord).where(
                EntityRecord.scan_id == scan_id,
                EntityRecord.entity_type == entity_type,
                EntityRecord.normalized_value == normalized_value
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_entities_for_scan(self, scan_id: uuid.UUID, limit: int = 1000) -> Sequence[EntityRecord]:
        """Retrieve all entities for a specific scan."""
        async with self.db.get_session() as session:
            stmt = select(EntityRecord).where(EntityRecord.scan_id == scan_id).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()

    async def bulk_create(self, scan_id: uuid.UUID, entities_data: List[Dict[str, Any]]) -> List[uuid.UUID]:
        """Bulk create entities efficiently."""
        if not entities_data:
            return []
            
        async with self.db.get_session() as session:
            # We use insert().returning() to get generated IDs
            records = []
            for d in entities_data:
                records.append({
                    'scan_id': scan_id,
                    'entity_type': d['type'],
                    'value': d['value'],
                    'normalized_value': d.get('normalized_value', d['value'].lower()),
                    'source_module': d['source_module'],
                    'confidence': d.get('confidence', 1.0),
                    'metadata_json': d.get('metadata', {}),
                    'parent_entity_id': d.get('parent_entity_id'),
                    'depth': d.get('depth', 0)
                })
            
            stmt = insert(EntityRecord).values(records).returning(EntityRecord.id)
            result = await session.execute(stmt)
            ids = [row[0] for row in result]
            
            # Update scan stats
            await session.execute(
                update(ScanRecord)
                .where(ScanRecord.id == scan_id)
                .values(total_entities=ScanRecord.total_entities + len(ids))
            )
            
            return ids


class RelationshipRepository(BaseRepository):
    """Repository for managing relationship records."""

    async def create_relationship(self, scan_id: uuid.UUID, rel_data: Dict[str, Any]) -> RelationshipRecord:
        """Create a new relationship record."""
        async with self.db.get_session() as session:
            rel = RelationshipRecord(
                scan_id=scan_id,
                source_entity_id=rel_data['source_id'],
                target_entity_id=rel_data['target_id'],
                relationship_type=rel_data['type'],
                confidence=rel_data.get('confidence', 1.0),
                source_module=rel_data['source_module'],
                metadata_json=rel_data.get('metadata', {})
            )
            session.add(rel)
            
            # Update scan stats
            await session.execute(
                update(ScanRecord)
                .where(ScanRecord.id == scan_id)
                .values(total_relationships=ScanRecord.total_relationships + 1)
            )
            
            return rel

    async def get_relationships_for_scan(self, scan_id: uuid.UUID) -> Sequence[RelationshipRecord]:
        """Retrieve all relationships for a specific scan."""
        async with self.db.get_session() as session:
            stmt = select(RelationshipRecord).where(RelationshipRecord.scan_id == scan_id)
            result = await session.execute(stmt)
            return result.scalars().all()

    async def find_related_entities(self, entity_id: uuid.UUID) -> Sequence[RelationshipRecord]:
        """Find all relationships originating from or targeting an entity."""
        async with self.db.get_session() as session:
            stmt = select(RelationshipRecord).where(
                (RelationshipRecord.source_entity_id == entity_id) | 
                (RelationshipRecord.target_entity_id == entity_id)
            )
            result = await session.execute(stmt)
            return result.scalars().all()
