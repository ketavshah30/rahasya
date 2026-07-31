"""Recursive OSINT Orchestration Engine.

The brain of Rahasya. Manages the full scan lifecycle:
1. Accept target input (ScanRequest)
2. Generate seed entities
3. Dispatch to discovery modules in parallel
4. Collect results, resolve entities, build graph
5. Recursively pivot on new discoveries
6. Enforce depth/entity/time limits
"""

import asyncio
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger

from rahasya.config import Settings, settings
from rahasya.core.models import (
    Entity, EntityType, SourceReliability,
    PersonEntity, EmailEntity, PhoneEntity, UsernameEntity,
    PhotoEntity, LocationEntity,
    Relationship, RelationshipType,
    ScanRequest, ScanResult, ScanStatus, ScanStats,
)
from rahasya.core.events import EventBus, EventType, Event
from rahasya.core.entity_queue import EntityQueue
from rahasya.modules import ModuleRegistry
from rahasya.correlation.graph_manager import GraphManager
from rahasya.correlation.entity_resolver import EntityResolver
from rahasya.utils.logging import get_logger
from rahasya.utils.validators import (
    normalize_email, normalize_phone, generate_username_variants, normalize_name,
)


class Orchestrator:
    """Core recursive OSINT orchestration engine.

    Coordinates the entire scanning workflow: entity seeding,
    module dispatch, entity resolution, and graph construction.
    """

    def __init__(self, config: Optional[Settings] = None):
        """Initialize orchestrator with configuration.

        Args:
            config: Application settings. Defaults to global settings.
        """
        self.config = config or settings
        self.event_bus = EventBus()
        self.graph = GraphManager(self.config)
        self.resolver = EntityResolver(self.graph, self.config)
        self.module_registry = ModuleRegistry(self.config)
        self.logger = get_logger("orchestrator")

        # Per-scan tracking
        self._scan_state: Dict[str, dict] = {}
        self.entities: List[Entity] = []
        self._visited_entities: Set[Tuple[str, str]] = set()

    async def start_scan(self, request: ScanRequest) -> str:
        """Initialize and run a full OSINT scan.

        Args:
            request: Target information to investigate.

        Returns:
            Unique scan ID string.
        """
        scan_id = str(uuid.uuid4())

        # Initialize scan state
        self._scan_state[scan_id] = {
            "request": request,
            "start_time": time.monotonic(),
            "entity_count": 0,
            "visited": set(),  # (entity_type, normalized_value) tuples
            "entities": [],
            "relationships": [],
            "status": ScanStatus.RUNNING,
            "depth_reached": 0,
            "modules_run": 0,
        }

        self.logger.info(f"Starting scan {scan_id[:8]}...")
        await self.event_bus.publish(Event(
            type=EventType.SCAN_STARTED,
            payload={"scan_id": scan_id, "request": request.model_dump()},
        ))

        # Generate seed entities
        seeds = self._generate_seed_entities(request, scan_id)
        self.logger.info(f"Generated {len(seeds)} seed entities from input")

        # Add seeds to graph and state
        for seed in seeds:
            await self._register_entity(scan_id, seed)

        # Run the processing loop
        asyncio.create_task(self._run_scan_loop(scan_id, seeds))

        return scan_id

    async def _run_scan_loop(self, scan_id: str, initial_entities: List[Entity]) -> None:
        """Main processing loop. Dispatches modules and handles recursion.

        Args:
            scan_id: Active scan identifier.
            initial_entities: Seed entities to start processing.
        """
        state = self._scan_state[scan_id]

        try:
            # Process in BFS order by depth
            queue: List[Entity] = list(initial_entities)
            current_depth = 0
            max_depth = self.config.scan.max_depth
            max_entities = self.config.scan.max_entities
            max_time = self.config.scan.max_time_minutes * 60  # seconds

            while queue and current_depth <= max_depth:
                # Check time limit
                elapsed = time.monotonic() - state["start_time"]
                if elapsed >= max_time:
                    self.logger.warning(f"Scan {scan_id[:8]} hit time limit ({max_time}s)")
                    break

                # Check entity limit
                if state["entity_count"] >= max_entities:
                    self.logger.warning(f"Scan {scan_id[:8]} hit entity limit ({max_entities})")
                    break

                self.logger.info(
                    f"Processing depth {current_depth}: {len(queue)} entities in queue"
                )

                next_queue: List[Entity] = []

                for entity in queue:
                    # Recheck limits
                    if state["entity_count"] >= max_entities:
                        break
                    elapsed = time.monotonic() - state["start_time"]
                    if elapsed >= max_time:
                        break

                    # Get applicable modules for this entity type
                    modules = self.module_registry.get_modules_for(entity.entity_type)
                    if not modules:
                        continue

                    self.logger.debug(
                        f"Dispatching {len(modules)} modules for "
                        f"[{entity.entity_type.value}] '{entity.value}'"
                    )

                    # Execute all applicable modules in parallel
                    results = await asyncio.gather(
                        *[mod.safe_execute(entity, scan_id) for mod in modules],
                        return_exceptions=True,
                    )

                    # Collect newly discovered entities
                    new_entities: List[Entity] = []
                    for i, result in enumerate(results):
                        state["modules_run"] += 1
                        if isinstance(result, Exception):
                            self.logger.error(
                                f"Module failed: {type(result).__name__}: {result}"
                            )
                            continue
                        if result:
                            new_entities.extend(result)

                    # Register new entities and queue for next depth
                    for new_entity in new_entities:
                        new_entity.depth = current_depth + 1
                        new_entity.parent_entity_id = entity.id

                        if await self._register_entity(scan_id, new_entity):
                            next_queue.append(new_entity)

                            # Create parent-child relationship
                            rel = Relationship(
                                source_id=entity.id,
                                target_id=new_entity.id,
                                relationship_type=self._infer_relationship_type(
                                    entity, new_entity
                                ),
                                confidence=new_entity.confidence,
                                source_module=new_entity.source_module,
                            )
                            state["relationships"].append(rel)
                            await self.graph.add_edge(entity.id, new_entity.id, rel)

                    # Run entity resolution on new batch
                    if new_entities:
                        resolved_rels = await self.resolver.resolve(
                            new_entities + [entity]
                        )
                        for rel in resolved_rels:
                            state["relationships"].append(rel)
                            await self.graph.add_edge(
                                rel.source_id, rel.target_id, rel
                            )

                    # Publish progress
                    await self.event_bus.publish(Event(
                        type=EventType.SCAN_PROGRESS,
                        payload={
                            "scan_id": scan_id,
                            "entity_count": state["entity_count"],
                            "depth": current_depth,
                        },
                    ))

                queue = next_queue
                current_depth += 1
                state["depth_reached"] = current_depth

            state["status"] = ScanStatus.COMPLETED
            self.logger.info(
                f"Scan {scan_id[:8]} completed: "
                f"{state['entity_count']} entities, "
                f"{len(state['relationships'])} relationships, "
                f"depth {state['depth_reached']}"
            )

        except asyncio.CancelledError:
            state["status"] = ScanStatus.CANCELLED
            self.logger.warning(f"Scan {scan_id[:8]} was cancelled")
        except Exception as e:
            state["status"] = ScanStatus.FAILED
            self.logger.error(f"Scan {scan_id[:8]} failed: {e}", exc_info=True)
        finally:
            elapsed = time.monotonic() - state["start_time"]
            await self.event_bus.publish(Event(
                type=EventType.SCAN_COMPLETED,
                payload={"scan_id": scan_id, "status": state["status"].value},
            ))

    async def _register_entity(self, scan_id: str, entity: Entity) -> bool:
        """Register a new entity if not already visited.

        Args:
            scan_id: Active scan ID.
            entity: Entity to register.

        Returns:
            True if entity is new and was registered, False if duplicate.
        """
        state = self._scan_state[scan_id]
        key = (entity.entity_type.value, entity.normalized_value)

        if key in state["visited"]:
            return False

        state["visited"].add(key)
        state["entities"].append(entity)
        state["entity_count"] += 1

        # Add to graph
        await self.graph.add_node(entity)

        return True

    def _generate_seed_entities(
        self, request: ScanRequest, scan_id: str
    ) -> List[Entity]:
        """Convert raw ScanRequest fields into seed Entity objects.

        Args:
            request: User-provided target information.
            scan_id: Scan identifier for metadata.

        Returns:
            List of seed entities.
        """
        seeds: List[Entity] = []
        common = {
            "source_module": "seed",
            "scan_id": scan_id,
            "source_reliability": SourceReliability.HIGH,
            "confidence": 1.0,
            "depth": 0,
            "metadata": {"scan_id": scan_id, "is_seed": True},
        }

        if request.name:
            name_clean = normalize_name(request.name)
            seeds.append(PersonEntity(
                entity_type=EntityType.PERSON,
                value=request.name,
                normalized_value=name_clean,
                name=name_clean,
                **common,
            ))
            # Generate username variants from name
            variants = generate_username_variants(request.name)
            for variant in variants[:5]:  # Limit to top 5 variants
                seeds.append(UsernameEntity(
                    entity_type=EntityType.USERNAME,
                    value=variant,
                    normalized_value=variant.lower(),
                    handle=variant,
                    **common,
                ))

        if request.email:
            email_clean = request.email.strip().lower()
            domain = email_clean.split("@")[-1] if "@" in email_clean else ""
            seeds.append(EmailEntity(
                entity_type=EntityType.EMAIL,
                value=request.email,
                normalized_value=email_clean,
                address=email_clean,
                domain=domain,
                **common,
            ))
            # Extract username from email as potential social handle
            local_part = email_clean.split("@")[0] if "@" in email_clean else ""
            if local_part and len(local_part) >= 3:
                seeds.append(UsernameEntity(
                    entity_type=EntityType.USERNAME,
                    value=local_part,
                    normalized_value=local_part,
                    handle=local_part,
                    **common,
                ))

        if request.phone:
            phone_norm = normalize_phone(request.phone)
            seeds.append(PhoneEntity(
                entity_type=EntityType.PHONE,
                value=request.phone,
                normalized_value=phone_norm or request.phone.strip(),
                number=phone_norm or request.phone.strip(),
                **common,
            ))

        if request.username:
            handle = request.username.lstrip("@").strip()
            seeds.append(UsernameEntity(
                entity_type=EntityType.USERNAME,
                value=handle,
                normalized_value=handle.lower(),
                handle=handle,
                **common,
            ))

        if request.photo_path:
            seeds.append(PhotoEntity(
                entity_type=EntityType.PHOTO,
                value=request.photo_path,
                normalized_value=request.photo_path,
                file_path=request.photo_path,
                **common,
            ))

        if request.location:
            seeds.append(LocationEntity(
                entity_type=EntityType.LOCATION,
                value=request.location,
                normalized_value=request.location.lower().strip(),
                **common,
            ))

        return seeds

    def generate_seed_entities(self, request: ScanRequest) -> List[Entity]:
        """Synchronous helper for callers that only need seed expansion."""
        return self._generate_seed_entities(request, "preview")

    def seed_from_email(self, email: str) -> List[Entity]:
        return self.generate_seed_entities(ScanRequest(email=email))

    def seed_from_name(self, name: str) -> List[Entity]:
        return self.generate_seed_entities(ScanRequest(name=name))

    def seed_from_phone(self, phone: str) -> List[Entity]:
        return self.generate_seed_entities(ScanRequest(phone=phone))

    def seed_from_username(self, username: str) -> List[Entity]:
        return self.generate_seed_entities(ScanRequest(username=username))

    def infer_relationship_type(self, parent: Entity, child: Entity) -> RelationshipType:
        return self._infer_relationship_type(parent, child)

    def register_entity(self, entity: Entity) -> bool:
        """Synchronous in-memory registration helper for lightweight callers."""
        key = (entity.entity_type.value, entity.normalized_value)
        if key in self._visited_entities:
            return False
        self._visited_entities.add(key)
        self.entities.append(entity)
        return True

    @staticmethod
    def _infer_relationship_type(
        parent: Entity, child: Entity
    ) -> RelationshipType:
        """Infer the relationship type between parent and child entities.

        Args:
            parent: The entity that triggered discovery.
            child: The newly discovered entity.

        Returns:
            Appropriate RelationshipType.
        """
        type_map = {
            (EntityType.PERSON, EntityType.EMAIL): RelationshipType.HAS_EMAIL,
            (EntityType.PERSON, EntityType.PHONE): RelationshipType.HAS_PHONE,
            (EntityType.PERSON, EntityType.USERNAME): RelationshipType.USES_USERNAME,
            (EntityType.USERNAME, EntityType.SOCIAL_PROFILE): RelationshipType.HAS_PROFILE,
            (EntityType.EMAIL, EntityType.BREACH_RECORD): RelationshipType.APPEARED_IN_BREACH,
            (EntityType.USERNAME, EntityType.SOCIAL_PROFILE): RelationshipType.HAS_PROFILE,
            (EntityType.EMAIL, EntityType.DARK_WEB_MENTION): RelationshipType.MENTIONED_ON,
            (EntityType.USERNAME, EntityType.DARK_WEB_MENTION): RelationshipType.MENTIONED_ON,
            (EntityType.PHONE, EntityType.DARK_WEB_MENTION): RelationshipType.MENTIONED_ON,
            (EntityType.PHOTO, EntityType.LOCATION): RelationshipType.TAKEN_AT,
        }
        key = (parent.entity_type, child.entity_type)
        return type_map.get(key, RelationshipType.LINKED_TO)

    def get_scan_result(self, scan_id: str) -> Optional[ScanResult]:
        """Get the current result of a scan.

        Args:
            scan_id: Scan identifier.

        Returns:
            ScanResult or None if scan not found.
        """
        state = self._scan_state.get(scan_id)
        if not state:
            return ScanResult(scan_id=scan_id, status=ScanStatus.PENDING)

        elapsed = time.monotonic() - state["start_time"]

        # Build type breakdown
        by_type: Dict[str, int] = {}
        for e in state["entities"]:
            type_name = e.entity_type.value
            by_type[type_name] = by_type.get(type_name, 0) + 1

        return ScanResult(
            scan_id=scan_id,
            status=state["status"],
            started_at=datetime.now(timezone.utc),
            completed_at=(
                datetime.now(timezone.utc)
                if state["status"] in (ScanStatus.COMPLETED, ScanStatus.FAILED)
                else None
            ),
            entities=state["entities"],
            relationships=state["relationships"],
            stats=ScanStats(
                total_entities=state["entity_count"],
                by_type=by_type,
                total_relationships=len(state["relationships"]),
                modules_run=state["modules_run"],
                depth_reached=state["depth_reached"],
                duration_seconds=elapsed,
            ),
        )

    async def cancel_scan(self, scan_id: str) -> None:
        """Cancel a running scan.

        Args:
            scan_id: Scan to cancel.
        """
        if scan_id in self._scan_state:
            self._scan_state[scan_id]["status"] = ScanStatus.CANCELLED
            # Force limits to stop the loop
            self._scan_state[scan_id]["entity_count"] = (
                self.config.scan.max_entities + 1
            )
            self.logger.info(f"Scan {scan_id[:8]} cancellation requested")
