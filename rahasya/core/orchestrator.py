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
from rahasya.core.related_persons import select_related_persons
from rahasya.core.budget import (
    calls_used as _budget_calls_used,
    clear_scan as _budget_clear_scan,
    is_tripped as _budget_is_tripped,
    register_scan as _budget_register_scan,
)
from rahasya.modules import ModuleRegistry
from rahasya.modules.darkweb._gating import (
    clear_scan as _dw_clear_scan,
    note_confirmed_social_profile as _dw_note_confirmed_social_profile,
)
from rahasya.correlation.graph_manager import GraphManager
from rahasya.correlation.entity_resolver import EntityResolver
from rahasya.utils.logging import get_logger
from rahasya.utils.validators import (
    normalize_email, normalize_phone, normalize_name,
)
from rahasya.storage.scan_store import ScanStore
from rahasya.metrics import ACTIVE_SCANS, SCANS_COMPLETED, SCANS_STARTED
from rahasya.storage.network_audit import record_audit_event


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
        self.event_bus = EventBus(
            self.config.redis.url,
            redis_enabled=self.config.redis.pubsub_enabled,
        )
        self.graph = GraphManager(self.config)
        self.resolver = EntityResolver(self.graph, self.config)
        self.module_registry = ModuleRegistry(self.config)
        self.logger = get_logger("orchestrator")

        # Per-scan tracking
        self._scan_state: Dict[str, dict] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self.scan_store = ScanStore(self.config.storage.scan_dir)
        self.entities: List[Entity] = []
        self._visited_entities: Set[Tuple[str, str]] = set()

    async def start_scan(self, request: ScanRequest, scan_id: Optional[str] = None) -> str:
        """Initialize and run a full OSINT scan.

        Args:
            request: Target information to investigate.

        Returns:
            Unique scan ID string.
        """
        scan_id = scan_id or str(uuid.uuid4())

        # Initialize scan state
        self._scan_state[scan_id] = {
            "request": request,
            "start_time": time.monotonic(),
            "started_at": datetime.now(timezone.utc),
            "completed_at": None,
            "error": None,
            "entity_count": 0,
            "visited": set(),  # (entity_type, normalized_value) tuples
            "entities": [],
            "relationships": [],
            "status": ScanStatus.RUNNING,
            "depth_reached": 0,
            "modules_run": 0,
        }

        # FIXES_NEW.md §J.2 — arm the network-call budget for this scan.
        _budget_register_scan(scan_id, int(self.config.scan.max_network_calls))

        self.logger.info(f"Starting scan {scan_id[:8]}...")
        self._audit(
            scan_id,
            "scan_started",
            outcome="started",
            source_module="orchestrator",
            message="Scan entered the orchestrator",
            request_fields=[key for key, value in request.model_dump().items() if value not in (None, "")],
        )
        if SCANS_STARTED:
            SCANS_STARTED.inc()
        if ACTIVE_SCANS:
            ACTIVE_SCANS.inc()
        await self.event_bus.publish(Event(
            type=EventType.SCAN_STARTED,
            payload={"scan_id": scan_id, "request": request.model_dump()},
        ))

        # Generate seed entities
        seeds = self._generate_seed_entities(request, scan_id)
        self.logger.info(f"Generated {len(seeds)} seed entities from input")

        # Add seeds to graph and state
        registered_seeds: List[Entity] = []
        for seed in seeds:
            if await self._register_entity(scan_id, seed):
                registered_seeds.append(seed)

        for rel in self._build_seed_relationships(registered_seeds):
            self._scan_state[scan_id]["relationships"].append(rel)
            await self.graph.add_edge(rel.source_id, rel.target_id, rel)

        self._persist_state(scan_id, depth=0, module=None)

        # Run the processing loop
        self._tasks[scan_id] = asyncio.create_task(
            self._run_scan_loop(scan_id, registered_seeds),
            name=f"rahasya-scan-{scan_id}",
        )

        return scan_id

    async def _run_scan_loop(self, scan_id: str, initial_entities: List[Entity]) -> None:
        """Main processing loop. Dispatches modules and handles recursion.

        Args:
            scan_id: Active scan identifier.
            initial_entities: Seed entities to start processing.
        """
        state = self._scan_state[scan_id]

        try:
            # Process in BFS order by depth.
            #
            # FIXES_NEW.md §G.3 / §J.1: the outer loop terminates when a
            # full pass promotes ZERO new ground-truth facts, OR any hard
            # budget is hit (time, entity count, network calls, depth cap).
            # The dashboard's `Max Recursion Depth` slider becomes an
            # UPPER BOUND / SAFETY CAP — the convergence check usually
            # terminates before we reach it.
            queue: List[Entity] = list(initial_entities)
            current_depth = 0
            max_depth = self.config.scan.max_depth
            max_entities = self.config.scan.max_entities
            max_time = self.config.scan.max_time_minutes * 60  # seconds

            # Seed ground-truth baseline for §J.1.
            state["ground_truth_facts"] = sum(
                1 for entity in state["entities"]
                if getattr(entity, "is_ground_truth", False)
            )
            state["passes_run"] = 0
            state["converged_by"] = None

            while queue and current_depth <= max_depth:
                # Check time limit
                elapsed = time.monotonic() - state["start_time"]
                if elapsed >= max_time:
                    self.logger.warning(f"Scan {scan_id[:8]} hit time limit ({max_time}s)")
                    self._audit(scan_id, "scan_limit", outcome="stopped", limit="time", limit_value=max_time)
                    state["converged_by"] = "time_limit"
                    break

                # Check entity limit
                if state["entity_count"] >= max_entities:
                    self.logger.warning(f"Scan {scan_id[:8]} hit entity limit ({max_entities})")
                    self._audit(scan_id, "scan_limit", outcome="stopped", limit="entities", limit_value=max_entities)
                    state["converged_by"] = "entity_limit"
                    break

                # FIXES_NEW.md §J.2 — network-call budget check.
                if _budget_is_tripped(scan_id):
                    self.logger.warning(
                        f"Scan {scan_id[:8]} exhausted its network-call budget"
                    )
                    self._audit(
                        scan_id,
                        "scan_limit",
                        outcome="stopped",
                        limit="network_calls",
                        limit_value=self.config.scan.max_network_calls,
                        calls_used=_budget_calls_used(scan_id),
                    )
                    state["converged_by"] = "network_budget"
                    break

                self.logger.info(
                    f"Processing depth {current_depth}: {len(queue)} entities in queue"
                )
                self._audit(
                    scan_id,
                    "depth_started",
                    outcome="started",
                    depth=current_depth,
                    queue_size=len(queue),
                )

                next_queue: List[Entity] = []
                self._persist_state(scan_id, depth=current_depth, module=None)

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

                    module_names = ", ".join(module.name for module in modules)
                    self._persist_state(scan_id, depth=current_depth, module=module_names)
                    module_timeouts = [
                        self.config.scan.module_timeout_overrides.get(
                            module.name,
                            self.config.scan.module_timeout_seconds,
                        )
                        for module in modules
                    ]

                    # Execute all applicable modules in parallel
                    results = await asyncio.gather(
                        *[
                            asyncio.wait_for(
                                mod.safe_execute(entity, scan_id),
                                timeout=module_timeout,
                            )
                            for mod, module_timeout in zip(modules, module_timeouts)
                        ],
                        return_exceptions=True,
                    )

                    # Collect newly discovered entities
                    new_entities: List[Entity] = []
                    for i, result in enumerate(results):
                        state["modules_run"] += 1
                        if isinstance(result, Exception):
                            module_name = modules[i].name if i < len(modules) else "unknown"
                            is_timeout = isinstance(result, (asyncio.TimeoutError, TimeoutError))
                            self._audit(
                                scan_id,
                                "module_timeout" if is_timeout else "module_exception",
                                outcome="timeout" if is_timeout else "failed",
                                source_module=module_name,
                                entity_type=entity.entity_type.value,
                                entity_value=entity.value,
                                timeout_seconds=module_timeouts[i] if is_timeout else None,
                                error_type=type(result).__name__,
                                error=str(result),
                            )
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
                            if new_entity.confidence >= self.config.scan.confidence_threshold:
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

                    self._persist_state(scan_id, depth=current_depth, module=None)

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
                state["passes_run"] = state.get("passes_run", 0) + 1

                # FIXES_NEW.md §J.1: recompute the ground-truth fact count
                # AFTER this pass finished. If a full pass produced zero
                # new ground-truth facts, the scan has converged and we
                # stop even if the depth cap has not been hit.
                new_gt_count = sum(
                    1 for entity in state["entities"]
                    if getattr(entity, "is_ground_truth", False)
                )
                gt_delta = new_gt_count - state.get("ground_truth_facts", 0)
                state["ground_truth_facts"] = new_gt_count

                self._audit(
                    scan_id,
                    "depth_completed",
                    outcome="success",
                    depth=current_depth - 1,
                    entity_count=state["entity_count"],
                    relationship_count=len(state["relationships"]),
                    ground_truth_facts=new_gt_count,
                    ground_truth_delta=gt_delta,
                )
                self._persist_state(scan_id, depth=current_depth, module=None)

                # Convergence check.
                if gt_delta <= 0 and not next_queue:
                    state["converged_by"] = "no_new_info"
                    self._audit(
                        scan_id,
                        "scan_converged",
                        outcome="success",
                        reason="no_new_info",
                        passes_run=state["passes_run"],
                        ground_truth_facts=new_gt_count,
                    )
                    break

            # If the loop exited because we ran out of queue OR hit the
            # depth cap without setting converged_by, tag it appropriately.
            if not state.get("converged_by"):
                if current_depth > max_depth:
                    state["converged_by"] = "depth_cap"
                else:
                    state["converged_by"] = "no_new_info"

            # FIXES_NEW.md §G.2: related-person expansion — after the
            # primary target converges, pick up to N candidate related
            # persons and record who won plus who lost. Actual secondary
            # PersonProfile scans are gated behind related_depth (default 1
            # = no further expansion) and are deferred to a future pass.
            self._select_and_record_related_persons(scan_id)

            state["status"] = ScanStatus.COMPLETED
            state["completed_at"] = datetime.now(timezone.utc)
            self.logger.info(
                f"Scan {scan_id[:8]} completed: "
                f"{state['entity_count']} entities, "
                f"{len(state['relationships'])} relationships, "
                f"depth {state['depth_reached']}"
            )

        except asyncio.CancelledError:
            state["status"] = ScanStatus.CANCELLED
            state["completed_at"] = datetime.now(timezone.utc)
            self._audit(scan_id, "scan_cancelled", outcome="cancelled", source_module="orchestrator")
            self.logger.warning(f"Scan {scan_id[:8]} was cancelled")
        except Exception as e:
            state["status"] = ScanStatus.FAILED
            state["completed_at"] = datetime.now(timezone.utc)
            state["error"] = f"{type(e).__name__}: {e}"
            self._audit(
                scan_id,
                "scan_failed",
                outcome="failed",
                source_module="orchestrator",
                error_type=type(e).__name__,
                error=str(e),
            )
            self.logger.error(f"Scan {scan_id[:8]} failed: {e}", exc_info=True)
        finally:
            self._persist_state(scan_id, depth=state["depth_reached"], module=None)
            await asyncio.gather(
                *(module.teardown() for module in self.module_registry._instances.values()),
                return_exceptions=True,
            )
            await self.event_bus.publish(Event(
                type=EventType.SCAN_COMPLETED,
                payload={"scan_id": scan_id, "status": state["status"].value},
            ))
            self._audit(
                scan_id,
                "scan_completed",
                outcome=state["status"].value.casefold(),
                source_module="orchestrator",
                entity_count=state["entity_count"],
                relationship_count=len(state["relationships"]),
                modules_run=state["modules_run"],
                depth_reached=state["depth_reached"],
                duration_ms=round((time.monotonic() - state["start_time"]) * 1000, 2),
            )
            if SCANS_COMPLETED:
                SCANS_COMPLETED.labels(status=state["status"].value).inc()
            if ACTIVE_SCANS:
                ACTIVE_SCANS.dec()
            # FIXES_NEW.md §H.2 hygiene: drop the dark-web scoreboard entry
            # for this scan so long-lived orchestrator processes don't leak
            # memory between scans.
            _dw_clear_scan(scan_id)
            # FIXES_NEW.md §J.2 hygiene: also clear network-budget state.
            state["network_calls"] = _budget_calls_used(scan_id)
            _budget_clear_scan(scan_id)
            await self.event_bus.close()

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

        # FIXES_NEW.md §H.2: notify the dark-web gating scoreboard whenever
        # a ground-truth SOCIAL_PROFILE lands, so Ahmia/OnionSearch can
        # start firing on subsequent passes.
        if (
            entity.entity_type == EntityType.SOCIAL_PROFILE
            and getattr(entity, "is_ground_truth", False)
        ):
            _dw_note_confirmed_social_profile(
                scan_id,
                platform=str(getattr(entity, "attests_platform", "") or ""),
            )

        # FIXES_NEW.md §J.3: per-fact attribution audit — every promoted
        # ground-truth fact gets one row in the audit log with
        # (fact_type, value, evidence_urls, via_module, parent_entity_id).
        # This is the raw data the eventual CIA report will consume for
        # its "how do we know this?" section.
        if getattr(entity, "is_ground_truth", False):
            self._audit(
                scan_id,
                "fact_promoted",
                outcome="success",
                source_module=entity.source_module,
                fact_type=entity.entity_type.value,
                fact_value=entity.value,
                fact_id=entity.id,
                evidence_urls=list(getattr(entity, "evidence_urls", []) or []),
                attests_platform=getattr(entity, "attests_platform", None),
                parent_entity_id=entity.parent_entity_id,
                confidence=entity.confidence,
            )

        # Add to graph
        await self.graph.add_node(entity)

        self._audit(
            scan_id,
            "entity_registered",
            outcome="success",
            source_module=entity.source_module,
            entity_id=entity.id,
            entity_type=entity.entity_type.value,
            entity_value=entity.value,
            confidence=entity.confidence,
            depth=entity.depth,
            parent_entity_id=entity.parent_entity_id,
        )

        self._persist_state(scan_id, depth=state["depth_reached"], module=None)

        return True

    def _select_and_record_related_persons(self, scan_id: str) -> None:
        """FIXES_NEW.md §G.2: pick up to N related-person candidates.

        Selection rule (Option A): top N by number of distinct providers
        attesting the relationship, then by relationship strength
        (PARENT_OF > SIBLING_OF > SPOUSE_OF > KNOWS), then by first-seen
        timestamp. Losers are logged as runners_up so the operator can
        audit who was rejected.

        Actually launching secondary PersonProfile scans is deferred to a
        later workstream — this pass only *records* the picks and their
        evidence. That is enough to unblock:
          - the audit trail (§G.2 DoD row 1),
          - the ScanStats.related_persons_selected counter (§J.1),
          - the dashboard TODO (§G.2 workstream K) that will surface the
            picks for operator override.
        """
        state = self._scan_state[scan_id]
        cap = int(self.config.scan.max_related_persons)
        if cap <= 0:
            return

        # Seed IDs = every entity that came from the initial ScanRequest.
        seed_ids = {
            entity.id for entity in state["entities"]
            if (entity.metadata or {}).get("is_seed") is True
        }
        candidates = select_related_persons(
            entities=state["entities"],
            relationships=state["relationships"],
            seed_entity_ids=seed_ids,
            cap=cap,
        )
        state["related_persons_selected"] = len(candidates)
        if not candidates:
            return

        for candidate in candidates:
            self._audit(
                scan_id,
                "related_person_selected",
                outcome="success",
                entity_id=candidate.entity_id,
                entity_value=candidate.entity.value,
                evidence_score=candidate.evidence_score,
                top_strength=candidate.top_strength,
                edge_types=sorted({edge.relationship_type.value for edge in candidate.edges}),
                first_seen=candidate.first_seen.isoformat(),
            )

    def _generate_seed_entities(
        self, request: ScanRequest, scan_id: str
    ) -> List[Entity]:
        """Convert raw ScanRequest fields into seed Entity objects.

        Per FIXES_NEW.md Workstream D:
            - D.1: Emails NO LONGER derive a UsernameEntity from the local
              part. `xyz@example.com` does not imply Instagram handle `xyz`.
            - D.2: Names NO LONGER emit UsernameEntity variants as seeds.
              Variants remain available (via generate_username_variants) as
              *candidate* generators inside name-oriented reverse-lookup
              modules, but they never enter the seed set at
              confidence=1.0/HIGH.
            - D.3: Every seed is marked is_ground_truth=True because the
              user supplied it directly. Ground truth is the ONLY class of
              fact allowed to bypass corroboration in downstream modules
              (see Workstream F guards on Sherlock/Maigret/WhatsMyName).

        Args:
            request: User-provided target information.
            scan_id: Scan identifier for metadata.

        Returns:
            List of seed entities. All are ground-truth (user-supplied).
        """
        seeds: List[Entity] = []
        # User-supplied identifiers are ground truth by definition.
        common = {
            "source_module": "seed",
            "scan_id": scan_id,
            "source_reliability": SourceReliability.HIGH,
            "confidence": 1.0,
            "depth": 0,
            "is_ground_truth": True,
            "evidence_urls": [],
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
            # D.2: DO NOT emit UsernameEntity variants from a name as seeds.
            # Variants are candidate hypotheses, not ground truth. They will
            # be produced by name-oriented reverse-lookup modules in
            # Workstream E with source_reliability=LOW, confidence<=0.4, and
            # is_ground_truth=False so that Sherlock/Maigret/WhatsMyName
            # (which now require is_ground_truth=True per F.1-F.3) will not
            # fire on them.

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
            # D.1: DO NOT derive a UsernameEntity from email.split("@")[0].
            # The local part of an email is not the person's handle on any
            # given platform; treating it as ground truth was the root of
            # the "Sherlock/Maigret spraying a bogus handle across 3000
            # sites" behavior. Reverse-lookup modules (holehe, Gravatar,
            # GitHub-email search — Workstream E) are the correct way to
            # discover platform handles from an email.

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
            (EntityType.PERSON, EntityType.LOCATION): RelationshipType.LINKED_TO,
            (EntityType.PERSON, EntityType.PHOTO): RelationshipType.ASSOCIATED_WITH,
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

    @staticmethod
    def _build_seed_relationships(seeds: List[Entity]) -> List[Relationship]:
        """Wire user-supplied identifiers to the PERSON seed.

        Per FIXES_NEW.md Workstream D.4: with the email→username and
        name→username seed-derivations removed, there are no synthetic
        USES_USERNAME edges from the seed set. The seed graph is exactly:
            PersonEntity --{HAS_EMAIL|HAS_PHONE|USES_USERNAME|...}--> id
        for every user-supplied identifier the ScanRequest carried.

        Returns exactly N-1 relationships for a seed list of N entities
        (where 1 is PERSON). Returns [] if no PERSON seed is present
        (e.g. email-only or username-only scans, which are valid).
        """
        people = [entity for entity in seeds if entity.entity_type == EntityType.PERSON]
        if not people:
            return []

        person = people[0]
        relationships: List[Relationship] = []
        for entity in seeds:
            if entity.id == person.id:
                continue
            rel_type = Orchestrator._infer_relationship_type(person, entity)
            if rel_type == RelationshipType.LINKED_TO and entity.entity_type not in {
                EntityType.LOCATION,
                EntityType.PHOTO,
            }:
                # Defensive: unknown pair-types fall through to LINKED_TO,
                # which we do not want on the seed graph unless the child
                # is a semantic "linked" type (photo taken at location, etc.)
                continue
            relationships.append(Relationship(
                source_id=person.id,
                target_id=entity.id,
                relationship_type=rel_type,
                confidence=min(person.confidence, entity.confidence),
                source_module="seed",
                metadata={"reason": "initial target input"},
            ))
        return relationships

    def get_scan_result(self, scan_id: str) -> Optional[ScanResult]:
        """Get the current result of a scan.

        Args:
            scan_id: Scan identifier.

        Returns:
            ScanResult or None if scan not found.
        """
        state = self._scan_state.get(scan_id)
        if not state:
            return self.scan_store.load(scan_id) or ScanResult(scan_id=scan_id, status=ScanStatus.PENDING)

        elapsed = time.monotonic() - state["start_time"]

        # Build type breakdown
        by_type: Dict[str, int] = {}
        for e in state["entities"]:
            type_name = e.entity_type.value
            by_type[type_name] = by_type.get(type_name, 0) + 1

        return ScanResult(
            scan_id=scan_id,
            status=state["status"],
            started_at=state["started_at"],
            completed_at=state["completed_at"],
            entities=state["entities"],
            relationships=state["relationships"],
            stats=ScanStats(
                total_entities=state["entity_count"],
                by_type=by_type,
                total_relationships=len(state["relationships"]),
                modules_run=state["modules_run"],
                depth_reached=state["depth_reached"],
                duration_seconds=elapsed,
                # FIXES_NEW.md §J.1 — surface convergence stats to callers.
                passes_run=state.get("passes_run", 0),
                converged_by=state.get("converged_by"),
                ground_truth_facts=state.get("ground_truth_facts", 0),
                network_calls=state.get("network_calls", 0),
                related_persons_selected=state.get("related_persons_selected", 0),
            ),
            request=state["request"],
            error=state["error"],
        )

    def _persist_state(self, scan_id: str, *, depth: int, module: Optional[str]) -> None:
        """Write an atomic snapshot and lightweight progress event."""
        result = self.get_scan_result(scan_id)
        self.scan_store.save(result)
        self.scan_store.save_status(
            scan_id,
            status=result.status.value,
            depth=depth,
            module=module,
            entity_count=result.stats.total_entities,
            relationship_count=result.stats.total_relationships,
            modules_run=result.stats.modules_run,
            max_depth=self.config.scan.max_depth,
            max_entities=self.config.scan.max_entities,
        )

    def _audit(
        self,
        scan_id: str,
        event_type: str,
        *,
        outcome: str,
        source_module: str = "orchestrator",
        **details,
    ) -> None:
        record_audit_event(
            event_type,
            outcome=outcome,
            scan_id=scan_id,
            source_module=source_module,
            root=self.config.storage.scan_dir,
            **details,
        )

    async def cancel_scan(self, scan_id: str) -> None:
        """Cancel a running scan.

        Args:
            scan_id: Scan to cancel.
        """
        if scan_id in self._scan_state:
            self._scan_state[scan_id]["status"] = ScanStatus.CANCELLED
            task = self._tasks.get(scan_id)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._persist_state(
                scan_id,
                depth=self._scan_state[scan_id]["depth_reached"],
                module=None,
            )
            self.logger.info(f"Scan {scan_id[:8]} cancellation requested")
