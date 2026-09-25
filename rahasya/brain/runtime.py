"""Bounded coordinator / specialist / reviewer loop over existing modules."""

import asyncio
import time

from rahasya.brain.agents import AGENTS
from rahasya.brain.contracts import AgentEvent, AgentReport, BrainState, Delegation, EvidenceBatch, Review, ToolChoice
from rahasya.brain.evidence import fact_view, rank_evidence, shortlist, working_copy
from rahasya.brain.ollama import BrainError, OllamaClient
from rahasya.brain.settings import AgentRole
from rahasya.core.budget import is_tripped
from rahasya.core.models import EntityType
from rahasya.modules.darkweb._gating import scan_has_confirmed_social_profile
from rahasya.storage.evidence_store import EvidenceStore
from rahasya.storage.network_audit import capture_module_outcome, record_audit_event

ROLES = {agent.role: agent for agent in AGENTS}
OWNERS = {name: agent.role for agent in AGENTS for name in agent.modules}


class BrainLimit(RuntimeError):
    pass


class AgentRuntime:
    def __init__(self, config, scan_id, state: BrainState, persist, *, client=None):
        self.config = config
        self.settings = config.brain
        self.scan_id = scan_id
        self.state = state
        self.persist = persist
        self.client = client or OllamaClient(self.settings)
        self.attempted = set()
        self.evidence_store = EvidenceStore(config.storage.scan_dir)

    def event(self, kind, **kwargs):
        event = AgentEvent(kind=kind, **kwargs)
        self.state.events.append(event)
        self.persist()
        record_audit_event(
            "agent_" + kind, outcome=event.outcome or "success", scan_id=self.scan_id,
            source_module="agent:" + event.role, root=self.config.storage.scan_dir,
            **event.model_dump(mode="json", exclude={"timestamp", "kind", "outcome", "role"}),
        )

    def remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BrainLimit("time_limit")
        return remaining

    async def start(self, deadline):
        timeout = self.remaining(deadline)
        try:
            await asyncio.wait_for(self.client.check(), timeout=timeout)
        except asyncio.TimeoutError:
            raise BrainLimit("time_limit") from None
        self.event("ready", reason="Downloaded local models are available")

    async def ask(self, role, instruction, context, schema, deadline):
        if self.state.model_calls >= self.settings.max_model_calls:
            raise BrainLimit("model_call_limit")
        timeout = self.remaining(deadline)
        self.state.model_calls += 1
        self.event("model_started", role=role.value)
        try:
            result = await asyncio.wait_for(self.client.decide(role, instruction, context, schema), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            raise BrainLimit("time_limit") from None
        finally:
            self.state.input_tokens = self.client.input_tokens
            self.state.output_tokens = self.client.output_tokens
            self.persist()

    def eligible(self, entity, modules, excluded):
        result = {}
        for module in modules:
            role = OWNERS.get(module.name)
            key = (entity.entity_type.value, entity.normalized_value, module.name)
            if role is None or role in excluded or key in self.attempted:
                continue
            if entity.entity_type not in module.accepts or not module.is_available():
                continue
            if role in {AgentRole.IDENTITY, AgentRole.SOCIAL, AgentRole.DARKWEB}:
                if entity.entity_type in {EntityType.EMAIL, EntityType.PHONE, EntityType.USERNAME} and not entity.is_ground_truth:
                    continue
            if role == AgentRole.DARKWEB and not scan_has_confirmed_social_profile(self.scan_id):
                continue
            if module.name == "WaybackMachine" and not module._should_fire(entity):
                continue
            result[module.name] = module
        return result

    async def discover(self, entity, modules, known, deadline):
        """Archive observations and yield a bounded working set before review.

        Reviews are advisory. They never change provider attribution or confidence;
        uncertain/contradicted/unreviewed observations are excluded from AI pivots.
        """
        excluded = set()
        for _ in range(self.settings.max_actions_per_entity):
            self.remaining(deadline)
            if self.state.actions >= self.settings.max_actions:
                raise BrainLimit("agent_action_limit")
            if is_tripped(self.scan_id):
                raise BrainLimit("network_budget")
            available = self.eligible(entity, modules, excluded)
            if not available:
                break
            context = {
                "entity": fact_view(entity),
                "related_observations": [fact_view(e) for e in rank_evidence(
                    (e for e in known if e.parent_entity_id == entity.id), 3, entity)],
                "available": [{"module": name, "role": OWNERS[name].value,
                               "description": mod.description[:140]} for name, mod in available.items()],
                "recent_actions": [e.model_dump(mode="json", exclude={"timestamp", "result_ids"})
                                   for e in self.state.events if e.entity_id == entity.id and e.kind == "tool_finished"][-3:],
                "actions_remaining": self.settings.max_actions - self.state.actions,
            }
            delegation = await self.ask(
                AgentRole.COORDINATOR,
                ROLES[AgentRole.COORDINATOR].assignment +
                " Choose one role from available, or null to stop work on this entity. "
                "Prioritize direct identifier evidence and avoid redundant enumerators.",
                context, Delegation, deadline,
            )
            if delegation.role is not None and delegation.role not in {OWNERS[name] for name in available}:
                raise BrainError("Coordinator selected a role with no eligible tools.")
            self.event("delegated", role="coordinator", entity_id=entity.id,
                       outcome=delegation.role.value if delegation.role else "stop", reason=delegation.reason)
            if delegation.role is None:
                break
            role = delegation.role
            context["available"] = [item for item in context["available"] if item["role"] == role.value]
            choice = await self.ask(
                role, ROLES[role].assignment +
                " Choose exactly one listed module for this entity, or null to decline. "
                "The application supplies the entity and validated parameters; you cannot change them.",
                context, ToolChoice, deadline,
            )
            if choice.module is None:
                excluded.add(role)
                self.event("declined", role=role.value, entity_id=entity.id, reason=choice.reason)
                continue
            # Validate again immediately before executing; model output is not authority.
            available = self.eligible(entity, modules, excluded)
            if choice.module not in available or OWNERS[choice.module] != role:
                raise BrainError("Specialist selected an unavailable, repeated, or out-of-role tool.")
            module = available[choice.module]
            timeout = min(self.remaining(deadline), self.config.scan.module_timeout_overrides.get(
                module.name, self.config.scan.module_timeout_seconds))
            self.attempted.add((entity.entity_type.value, entity.normalized_value, module.name))
            self.state.actions += 1
            self.event("tool_started", role=role.value, entity_id=entity.id, module=module.name, reason=choice.reason)
            results = []
            try:
                with capture_module_outcome() as observed:
                    results = await asyncio.wait_for(module.safe_execute(entity, self.scan_id), timeout=timeout)
                outcome = ("partial" if observed["failed"] else "success") if results else (
                    "inconclusive" if observed["failed"] else "skipped" if observed["skipped"] else "no_results")
            except asyncio.TimeoutError:
                outcome = "timeout"
            except Exception:
                outcome = "failed"
            if results:
                selected, counts = shortlist(results, known, self.settings.max_candidates_per_tool, entity)
                # One append per tool output. Bulky metadata and deferred matches
                # never enter the hot scan snapshot or any model prompt.
                refs = self.evidence_store.append_batch(
                    self.scan_id, self.state.actions, module.name, entity.id, results, selected)
                self.state.evidence_batches.append(EvidenceBatch(
                    action=self.state.actions, module=module.name, subject_id=entity.id, **counts))
                results = [working_copy(item, refs[id(item)]) for item in selected]
                self.event("evidence_reduced", role=role.value, entity_id=entity.id, module=module.name,
                           reason=f"Archived {counts['received']} observations; selected {counts['selected']}; "
                                  f"deferred {counts['deferred']}; duplicates {counts['duplicates']}")
            self.event("tool_finished", role=role.value, entity_id=entity.id, module=module.name,
                       outcome=outcome, result_ids=[r.id for r in results])
            # The orchestrator saves selected observations before another model call.
            yield results
            if results:
                persisted_ids = {e.id for e in known}
                selected = rank_evidence((e for e in results if e.id in persisted_ids), 5, entity)
                if not selected:
                    continue
                review = await self.ask(
                    AgentRole.REVIEWER,
                    ROLES[AgentRole.REVIEWER].assignment +
                    " Assess each listed observation's association with the input entity. "
                    "A username match alone is uncertain. Use only listed entity IDs. "
                    "Return one assessment per listed observation. Missing evidence warrants uncertainty.",
                    {"input": fact_view(entity), "observations": [fact_view(e) for e in selected],
                     "tool_outcome": outcome}, Review, deadline,
                )
                expected = {e.id for e in selected}
                received = [a.entity_id for a in review.assessments]
                if len(received) != len(set(received)) or set(received) != expected:
                    raise BrainError("Reviewer returned missing, duplicate, or unknown observation IDs.")
                self.state.assessments.extend(review.assessments)
                by_id = {a.entity_id: a for a in review.assessments}
                for result in selected:
                    result.metadata["agent_review"] = by_id[result.id].disposition
                self.event("reviewed", role="reviewer", entity_id=entity.id,
                           module=module.name, result_ids=received,
                           reason="Assessment is advisory; original source attribution is preserved")
            self.remaining(deadline)

    async def report(self, entities, deadline):
        # A small, explicitly partial report fits the 4K context on the target PC.
        selected = rank_evidence((e for e in entities if e.source_module != "seed" and
                                  e.metadata.get("agent_review") == "supported"), 6)
        if not selected:
            self.state.report = AgentReport(claims=[], limitations=[
                "No observations were marked supported by the model reviewer. This does not establish absence of exposure."])
            self.persist()
            return
        report = await self.ask(
            AgentRole.REPORTER,
            ROLES[AgentRole.REPORTER].assignment +
            " Summarize only the supplied observations. Each claim must cite supplied entity IDs. "
            "Do not infer absence of exposure, calculate risk, or invent relationships. "
            "This is a partial model-generated summary, not independent verification.",
            {"observations": [fact_view(e) for e in selected], "total_entities": len(entities),
             "stop_reason": self.state.stop_reason}, AgentReport, deadline,
        )
        allowed = {e.id for e in selected}
        if any(not set(claim.entity_ids) <= allowed for claim in report.claims):
            raise BrainError("Reporter cited an observation outside its evidence context.")
        self.state.report = report
        self.state.report_entity_ids = [e.id for e in selected]
        self.event("report_completed", role="reporter", reason="Partial model summary; consult source evidence")

    async def close(self):
        await self.client.close()
