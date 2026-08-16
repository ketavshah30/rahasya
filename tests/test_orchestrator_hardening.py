import asyncio

import pytest

from rahasya.config import Settings
from rahasya.core.models import Entity, EntityType, ScanRequest, ScanStatus
from rahasya.core.orchestrator import Orchestrator


class FakeModule:
    name = "Fake"

    def __init__(self, *, delay=0, confidence=0.2):
        self.delay = delay
        self.confidence = confidence
        self.calls = 0

    async def safe_execute(self, entity, scan_id):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return [Entity(
            entity_type=EntityType.USERNAME,
            value=f"pivot-{self.calls}",
            normalized_value=f"pivot-{self.calls}",
            source_module=self.name,
            confidence=self.confidence,
            scan_id=scan_id,
        )]


def configured(tmp_path):
    config = Settings()
    config.storage.scan_dir = tmp_path
    config.scan.max_depth = 2
    config.scan.max_entities = 20
    config.scan.max_time_minutes = 1
    return config


@pytest.mark.asyncio
async def test_low_confidence_entity_is_persisted_but_not_enqueued(tmp_path):
    config = configured(tmp_path)
    config.scan.confidence_threshold = 0.8
    module = FakeModule(confidence=0.2)
    orchestrator = Orchestrator(config)
    orchestrator.module_registry.get_modules_for = lambda entity_type: [module]

    scan_id = await orchestrator.start_scan(ScanRequest(username="seed"))
    await orchestrator._tasks[scan_id]
    result = orchestrator.get_scan_result(scan_id)

    assert result.status == ScanStatus.COMPLETED
    assert module.calls == 1
    assert any(entity.normalized_value == "pivot-1" for entity in result.entities)
    assert orchestrator.scan_store.load(scan_id).stats.total_entities == result.stats.total_entities


@pytest.mark.asyncio
async def test_module_timeout_is_enforced(tmp_path):
    config = configured(tmp_path)
    config.scan.module_timeout_seconds = 0.01
    module = FakeModule(delay=1, confidence=1)
    orchestrator = Orchestrator(config)
    orchestrator.module_registry.get_modules_for = lambda entity_type: [module]

    scan_id = await orchestrator.start_scan(ScanRequest(username="seed"))
    await asyncio.wait_for(orchestrator._tasks[scan_id], timeout=0.5)
    result = orchestrator.get_scan_result(scan_id)

    assert result.status == ScanStatus.COMPLETED
    assert result.stats.total_entities == 1
    assert result.stats.modules_run == 1
