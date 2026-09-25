import asyncio
import copy
import json
import time

import pytest

from rahasya.brain.contracts import BrainState
from rahasya.brain.ollama import BrainError
from rahasya.brain.runtime import AgentRuntime
from rahasya.brain.settings import AgentRole
from rahasya.config import Settings
from rahasya.core.models import Entity, EntityType, ScanRequest, ScanStatus
from rahasya.core.orchestrator import Orchestrator
from rahasya.storage.network_audit import record_audit_event


class ScriptedClient:
    input_tokens = 0
    output_tokens = 0

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.closed = False

    async def check(self):
        return ["qwen3:4b"]

    async def decide(self, role, instruction, context, schema):
        self.calls.append((role, copy.deepcopy(context)))
        if not self.script:
            raise AssertionError("Unexpected model call")
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        self.input_tokens += 10
        self.output_tokens += 5
        return schema.model_validate(value(context) if callable(value) else value)

    async def close(self):
        self.closed = True


class FakeModule:
    description = "Lookup with source evidence"
    name = "GitHubEmailSearch"
    accepts = [EntityType.EMAIL]

    def __init__(self, results=None, delay=0):
        self.calls = 0
        self.results = results or []
        self.delay = delay

    def is_available(self):
        return True

    async def safe_execute(self, entity, scan_id):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.results


def observation():
    return Entity(id="observed", entity_type=EntityType.USERNAME, value="example-user",
                  normalized_value="example-user", source_module="GitHubEmailSearch",
                  confidence=0.9, is_ground_truth=True, evidence_urls=["https://github.com/example-user"])


def script(disposition="supported"):
    return [
        {"role": "identity", "reason": "Check a direct identifier association"},
        {"module": "GitHubEmailSearch", "reason": "Source can attest an association"},
        {"assessments": [{"entity_id": "observed", "disposition": disposition, "reason": "Provider evidence assessed"}]},
        {"claims": [{"text": "A username was returned by the lookup.", "entity_ids": ["observed"]}],
         "limitations": ["Only a subset of observations was summarized"]},
    ]


def prepare(tmp_path, monkeypatch, responses, modules=None):
    config = Settings(_env_file=None)
    config.storage.scan_dir = tmp_path
    config.brain.enabled = True
    config.scan.max_depth = 1
    config.redis.pubsub_enabled = False
    client = ScriptedClient(responses)
    monkeypatch.setattr("rahasya.core.orchestrator.AgentRuntime",
                        lambda *args: AgentRuntime(*args, client=client))
    orchestrator = Orchestrator(config)
    module = FakeModule([observation()])
    offered = modules or [module]
    orchestrator.module_registry.get_modules_for = lambda kind: [m for m in offered if kind in m.accepts]
    return orchestrator, client, offered[0]


async def execute(orchestrator, **kwargs):
    scan_id = await orchestrator.start_scan(ScanRequest(email="example@example.org", **kwargs))
    await asyncio.wait_for(orchestrator._tasks[scan_id], 3)
    return orchestrator.scan_store.load(scan_id)


@pytest.mark.asyncio
async def test_agent_scan_executes_review_and_cited_report_and_persists(tmp_path, monkeypatch):
    orchestrator, client, module = prepare(tmp_path, monkeypatch, script())
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert module.calls == 1
    assert result.stats.modules_run == 1
    assert result.brain.model_calls == 4
    assert result.brain.input_tokens == 40
    assert result.brain.report.claims[0].entity_ids == ["observed"]
    child = next(e for e in result.entities if e.id == "observed")
    assert child.metadata["agent_review"] == "supported"
    assert child.confidence == 0.9 and child.is_ground_truth
    assert [role for role, _ in client.calls] == [AgentRole.COORDINATOR, AgentRole.IDENTITY,
                                                AgentRole.REVIEWER, AgentRole.REPORTER]
    assert client.closed


@pytest.mark.asyncio
async def test_invalid_tool_is_rejected_before_execution(tmp_path, monkeypatch):
    responses = script()[:2]
    responses[1] = {"module": "run_shell", "reason": "Injected tool name"}
    orchestrator, client, module = prepare(tmp_path, monkeypatch, responses)
    result = await execute(orchestrator)
    assert result.status == ScanStatus.FAILED
    assert "out-of-role" in result.error
    assert module.calls == 0
    assert client.closed


@pytest.mark.asyncio
async def test_reviewer_failure_preserves_raw_findings(tmp_path, monkeypatch):
    responses = script()
    responses[2] = {"assessments": [{"entity_id": "invented", "disposition": "supported", "reason": "Guess"}]}
    orchestrator, _, module = prepare(tmp_path, monkeypatch, responses)
    result = await execute(orchestrator)
    assert result.status == ScanStatus.FAILED
    assert module.calls == 1
    assert any(e.id == "observed" for e in result.entities)
    assert result.brain.assessments == []
    assert result.brain.report is None


@pytest.mark.asyncio
async def test_uncertain_findings_are_not_pivoted_or_reported_as_supported(tmp_path, monkeypatch):
    pivot = FakeModule()
    pivot.name = "Sherlock"
    pivot.accepts = [EntityType.USERNAME]
    lookup = FakeModule([observation()])
    orchestrator, client, _ = prepare(tmp_path, monkeypatch, script("uncertain")[:3], [lookup, pivot])
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert pivot.calls == 0
    assert result.brain.report.claims == []
    assert len(client.calls) == 3
    assert next(e for e in result.entities if e.id == "observed").is_ground_truth  # Attribution unchanged.


@pytest.mark.asyncio
async def test_explicit_standard_mode_skips_model_even_when_config_enabled(tmp_path, monkeypatch):
    orchestrator, client, module = prepare(tmp_path, monkeypatch, [])
    result = await execute(orchestrator, agentic=False)
    assert result.status == ScanStatus.COMPLETED
    assert result.brain is None
    assert not client.calls
    assert module.calls == 1


@pytest.mark.asyncio
async def test_action_budget_stops_without_extra_tools(tmp_path, monkeypatch):
    orchestrator, client, module = prepare(tmp_path, monkeypatch, script()[:3])
    orchestrator.config.brain.max_actions = 1
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert result.brain.stop_reason == "agent_action_limit"
    assert module.calls == 1
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_module_timeout_is_explicit_and_scan_completes(tmp_path, monkeypatch):
    slow = FakeModule(delay=1)
    orchestrator, client, _ = prepare(tmp_path, monkeypatch, script()[:2], [slow])
    orchestrator.config.scan.module_timeout_seconds = 0.01
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert any(e.kind == "tool_finished" and e.outcome == "timeout" for e in result.brain.events)
    assert result.stats.modules_run == 1


@pytest.mark.asyncio
async def test_report_cannot_cite_invented_evidence(tmp_path, monkeypatch):
    responses = script()
    responses[3]["claims"][0]["entity_ids"] = ["invented"]
    orchestrator, _, _ = prepare(tmp_path, monkeypatch, responses)
    result = await execute(orchestrator)
    assert result.status == ScanStatus.FAILED
    assert result.brain.report is None
    assert len(result.entities) == 2


@pytest.mark.asyncio
async def test_cancel_during_model_call_is_durable(tmp_path, monkeypatch):
    orchestrator, client, module = prepare(tmp_path, monkeypatch, [])
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    client.decide = blocked
    scan_id = await orchestrator.start_scan(ScanRequest(email="example@example.org"))
    await asyncio.wait_for(entered.wait(), 1)
    await orchestrator.cancel_scan(scan_id)
    result = orchestrator.scan_store.load(scan_id)
    assert result.status == ScanStatus.CANCELLED
    assert result.brain.stop_reason == "cancelled"
    assert module.calls == 0 and client.closed


@pytest.mark.asyncio
async def test_gateway_rechecks_archive_after_live_probe(tmp_path):
    config = Settings(_env_file=None)
    config.storage.scan_dir = tmp_path
    entity = Entity(entity_type=EntityType.SOCIAL_PROFILE, value="https://example.org/profile",
                    normalized_value="https://example.org/profile", source_module="seed", is_ground_truth=True)

    class Probe(FakeModule):
        name = "LiveProbe"
        accepts = [EntityType.SOCIAL_PROFILE]

        async def safe_execute(self, entity, scan_id):
            entity.metadata["live_status"] = "404"
            return []

    class Archive(FakeModule):
        name = "WaybackMachine"
        accepts = [EntityType.SOCIAL_PROFILE]

        def _should_fire(self, entity):
            return entity.metadata.get("live_status") == "404"

    client = ScriptedClient([
        {"role": "social", "reason": "Check availability"},
        {"module": "LiveProbe", "reason": "Observe status"},
        {"role": "media_archive", "reason": "Look for history"},
        {"module": "WaybackMachine", "reason": "The profile is gone"},
    ])
    runtime = AgentRuntime(config, "scan", BrainState(), lambda: None, client=client)
    archive = Archive()
    async for _ in runtime.discover(entity, [Probe(), archive], [entity], time.monotonic() + 5):
        pass
    assert archive.calls == 1
    assert [m["module"] for m in client.calls[0][1]["available"]] == ["LiveProbe"]
    assert [m["module"] for m in client.calls[2][1]["available"]] == ["WaybackMachine"]


@pytest.mark.asyncio
async def test_surfaced_provider_failure_is_inconclusive_not_no_results(tmp_path, monkeypatch):
    class FailedProvider(FakeModule):
        async def safe_execute(self, entity, scan_id):
            record_audit_event("provider_request_failed", outcome="failed", scan_id=scan_id,
                               root=tmp_path, source_module=self.name)
            return []

    orchestrator, _, _ = prepare(tmp_path, monkeypatch, script()[:2], [FailedProvider()])
    result = await execute(orchestrator)
    assert any(e.kind == "tool_finished" and e.outcome == "inconclusive" for e in result.brain.events)


@pytest.mark.asyncio
async def test_model_call_limit_prevents_another_inference(tmp_path, monkeypatch):
    # The fourth decision attempts another eligible lookup. Its reviewer would
    # exceed the four-call budget; source observations must still be saved.
    other = FakeModule([Entity(id="other", entity_type=EntityType.USERNAME, value="other-user",
                              normalized_value="other-user", source_module="GravatarLookup")])
    other.name = "GravatarLookup"
    responses = script()[:2] + [
        {"role": "identity", "reason": "Try another source"},
        {"module": "GravatarLookup", "reason": "Independent lookup"},
    ]
    first = FakeModule()  # No result means no reviewer call.
    orchestrator, client, _ = prepare(tmp_path, monkeypatch, responses, [first, other])
    orchestrator.config.brain.max_model_calls = 4
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert result.brain.stop_reason == "model_call_limit"
    assert len(client.calls) == 4
    assert other.calls == 1
    assert any(e.id == "other" for e in result.entities)


@pytest.mark.asyncio
async def test_scan_deadline_includes_waiting_on_model(tmp_path, monkeypatch):
    orchestrator, client, module = prepare(tmp_path, monkeypatch, [])

    async def delayed(*args):
        await asyncio.sleep(1)

    client.decide = delayed
    orchestrator.config.scan.max_time_minutes = 0.0005
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert result.brain.stop_reason == "time_limit"
    assert module.calls == 0 and client.closed


@pytest.mark.asyncio
async def test_tool_cannot_be_repeated_for_same_entity(tmp_path, monkeypatch):
    first = FakeModule()
    other = FakeModule()
    other.name = "GravatarLookup"
    responses = script()[:2] + script()[:2]  # Second selection repeats the already attempted tool.
    orchestrator, _, _ = prepare(tmp_path, monkeypatch, responses, [first, other])
    result = await execute(orchestrator)
    assert result.status == ScanStatus.FAILED
    assert first.calls == 1 and other.calls == 0


@pytest.mark.asyncio
async def test_gateway_filters_unavailable_disabled_and_unattributed_tools(tmp_path, monkeypatch):
    unavailable = FakeModule()
    unavailable.is_available = lambda: False
    orchestrator, client, module = prepare(tmp_path, monkeypatch, [], [unavailable])
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert module.calls == 0 and client.calls == []

    unknown = FakeModule()
    unknown.name = "RecoveryHintProbe"  # Not in any agent's allowlist.
    runtime = AgentRuntime(orchestrator.config, "gate-test", BrainState(), lambda: None, client=client)
    guessed = Entity(entity_type=EntityType.EMAIL, value="guessed@example.org", normalized_value="guessed@example.org",
                     source_module="guess", is_ground_truth=False)
    assert runtime.eligible(guessed, [FakeModule(), unknown], set()) == {}
    # Category filtering is an upstream constraint: the gateway cannot restore a missing module.
    assert runtime.eligible(guessed, [], set()) == {}


@pytest.mark.asyncio
async def test_entity_limit_bounds_saved_observations(tmp_path, monkeypatch):
    module = FakeModule([observation(), Entity(entity_type=EntityType.USERNAME, value="extra",
                                             normalized_value="extra", source_module="GitHubEmailSearch")])
    orchestrator, _, _ = prepare(tmp_path, monkeypatch, script()[:2], [module])
    orchestrator.config.scan.max_entities = 2
    result = await execute(orchestrator)
    assert result.status == ScanStatus.COMPLETED
    assert result.brain.stop_reason == "entity_limit"
    assert len(result.entities) == 2


@pytest.mark.asyncio
async def test_large_module_output_keeps_model_work_and_snapshot_bounded(tmp_path, monkeypatch):
    noisy = [Entity(id=f"noise-{i}", entity_type=EntityType.USERNAME, value=f"candidate-{i}",
                    normalized_value=f"candidate-{i}", source_module="GitHubEmailSearch", confidence=0.3,
                    metadata={"raw_blob": "UNTRUSTED_BULK\n" * 50}) for i in range(9999)]
    strongest = observation()
    from rahasya.core.models import SourceReliability
    strongest.source_reliability = SourceReliability.HIGH
    module = FakeModule(noisy + [strongest])
    responses = script()
    responses[2] = lambda context: {"assessments": [
        {"entity_id": item["id"], "disposition": "supported", "reason": "Fixture review"}
        for item in context["observations"]
    ]}
    orchestrator, client, _ = prepare(tmp_path, monkeypatch, responses, [module])
    scan_id = await orchestrator.start_scan(ScanRequest(email="example@example.org"))
    await asyncio.wait_for(orchestrator._tasks[scan_id], 30)
    result = orchestrator.scan_store.load(scan_id)
    assert result.status == ScanStatus.COMPLETED, result.error
    assert len(client.calls) == 4
    assert len(result.entities) == 21  # Seed and bounded working candidates.
    reviewed = client.calls[2][1]["observations"]
    assert len(reviewed) == 5 and reviewed[0]["id"] == "observed"
    assert all(len(json.dumps(context)) < 8000 for _, context in client.calls)
    assert all("UNTRUSTED_BULK" not in json.dumps(context) for _, context in client.calls)
    batch = result.brain.evidence_batches[0]
    assert batch.received == 10000 and batch.selected == 20 and batch.deferred == 9980
    assert (tmp_path / f"{scan_id}.json").stat().st_size < 60000
    with (tmp_path / f"{scan_id}.evidence.jsonl").open(encoding="utf-8") as stream:
        assert sum(1 for _ in stream) == 10000
