from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import errno
import threading

import pytest

from rahasya.storage import scan_store

from rahasya.core.models import (
    Entity,
    EntityType,
    Relationship,
    RelationshipType,
    ScanRequest,
    ScanResult,
    ScanStats,
    ScanStatus,
)
from rahasya.storage.scan_store import ScanStore


def test_scan_store_roundtrip_and_delete(tmp_path):
    store = ScanStore(tmp_path)
    entity = Entity(
        entity_type=EntityType.EMAIL,
        value="Person@Example.com",
        normalized_value="person@example.com",
        source_module="test",
    )
    relationship = Relationship(
        source_id="root",
        target_id=entity.id,
        relationship_type=RelationshipType.HAS_EMAIL,
        source_module="test",
    )
    result = ScanResult(
        scan_id="scan-roundtrip",
        status=ScanStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        request=ScanRequest(email="person@example.com"),
        entities=[entity],
        relationships=[relationship],
        stats=ScanStats(total_entities=1, total_relationships=1, by_type={"email": 1}),
    )

    store.save(result)
    reloaded = store.load(result.scan_id)

    assert reloaded is not None
    assert reloaded.entities[0].normalized_value == "person@example.com"
    assert reloaded.relationships[0].relationship_type == RelationshipType.HAS_EMAIL
    assert reloaded.request.email == "person@example.com"
    assert store.list()[0].scan_id == result.scan_id
    assert store.delete(result.scan_id) is True
    assert store.load(result.scan_id) is None


def test_scan_status_is_merged_atomically(tmp_path):
    store = ScanStore(tmp_path)
    store.save_status("scan-status", status="RUNNING", entity_count=1)
    store.save_status("scan-status", entity_count=3, module="Ahmia")
    status = store.load_status("scan-status")
    assert status["status"] == "RUNNING"
    assert status["entity_count"] == 3
    assert status["module"] == "Ahmia"


def windows_lock_error(code):
    error = PermissionError(errno.EACCES, "File temporarily locked")
    error.winerror = code
    return error


@pytest.mark.parametrize("winerror", [5, 32, 33])
@pytest.mark.parametrize("status_file", [False, True])
def test_temporary_windows_lock_preserves_snapshot_and_recovers(tmp_path, monkeypatch, winerror, status_file):
    store = ScanStore(tmp_path)
    store.save(ScanResult(scan_id="locked", status=ScanStatus.RUNNING))
    store.save_status("locked", status="RUNNING")
    original_replace = scan_store.os.replace
    attempts = []
    delays = []

    def locked_replace(source, destination):
        attempts.append(destination)
        if len(attempts) <= 3:
            previous = store.load_status("locked") if status_file else store.load("locked")
            assert (previous["status"] if status_file else previous.status.value) == "RUNNING"
            raise windows_lock_error(winerror)
        original_replace(source, destination)

    monkeypatch.setattr(scan_store.os, "replace", locked_replace)
    monkeypatch.setattr(scan_store.time, "sleep", delays.append)
    if status_file:
        store.save_status("locked", status="COMPLETED")
        assert store.load_status("locked")["status"] == "COMPLETED"
    else:
        store.save(ScanResult(scan_id="locked", status=ScanStatus.COMPLETED))
        assert store.load("locked").status == ScanStatus.COMPLETED
    assert len(attempts) == 4
    assert len(delays) == 3 and all(delay > 0 for delay in delays)
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("error", [windows_lock_error(5), PermissionError(errno.EACCES, "Denied"), OSError(errno.ENOSPC, "Disk full")])
def test_persistent_save_failure_keeps_previous_result(tmp_path, monkeypatch, error):
    store = ScanStore(tmp_path)
    store.save(ScanResult(scan_id="preserved", status=ScanStatus.RUNNING))
    attempts = []
    delays = []

    def fail_replace(*args):
        attempts.append(args)
        raise error

    monkeypatch.setattr(scan_store.os, "replace", fail_replace)
    monkeypatch.setattr(scan_store.time, "sleep", delays.append)
    with pytest.raises(type(error)):
        store.save(ScanResult(scan_id="preserved", status=ScanStatus.COMPLETED))
    assert store.load("preserved").status == ScanStatus.RUNNING
    assert len(attempts) <= 8
    assert sum(delays) < 1
    if getattr(error, "winerror", None) != 5:
        assert len(attempts) == 1 and not delays
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_dashboard_reads_and_scan_writes(tmp_path):
    writer = ScanStore(tmp_path)
    reader = ScanStore(tmp_path)
    result = ScanResult(scan_id="concurrent", status=ScanStatus.RUNNING)
    writer.save(result)
    writer.save_status(result.scan_id, status="RUNNING", entity_count=0)
    started = threading.Event()
    finished = threading.Event()

    def read_snapshots():
        reads = 0
        started.set()
        while not finished.is_set() or reads < 10:
            snapshot = reader.load(result.scan_id)
            status = reader.load_status(result.scan_id)
            assert snapshot is not None and snapshot.scan_id == result.scan_id
            assert status is not None and status["status"] in {"RUNNING", "COMPLETED"}
            reads += 1
        return reads

    with ThreadPoolExecutor(max_workers=1) as executor:
        reading = executor.submit(read_snapshots)
        try:
            assert started.wait(timeout=5)
            for count in range(30):
                result.stats.total_entities = count
                writer.save(result)
                writer.save_status(result.scan_id, entity_count=count)
            result.status = ScanStatus.COMPLETED
            writer.save(result)
            writer.save_status(result.scan_id, status="COMPLETED")
        finally:
            finished.set()
        assert reading.result(timeout=10) >= 10
    assert reader.load(result.scan_id).status == ScanStatus.COMPLETED
    assert reader.load_status(result.scan_id)["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_scan_completes_after_temporary_snapshot_lock(tmp_path, monkeypatch):
    import asyncio
    from rahasya.config import Settings
    from rahasya.core.orchestrator import Orchestrator

    config = Settings()
    config.storage.scan_dir = tmp_path
    config.redis.pubsub_enabled = False
    config.neo4j.enabled = False
    config.brain.enabled = False
    orchestrator = Orchestrator(config)

    class OfflineModule:
        name = "OfflineFixture"
        calls = 0

        async def safe_execute(self, entity, scan_id):
            self.calls += 1
            return []

    module = OfflineModule()
    orchestrator.module_registry.get_modules_for = lambda entity_type: [module]
    original_replace = scan_store.os.replace
    blocked = []

    def lock_existing_snapshot(source, destination):
        if destination.name == "snapshot-lock.json" and destination.exists() and len(blocked) < 3:
            blocked.append(destination)
            raise windows_lock_error(5)
        original_replace(source, destination)

    monkeypatch.setattr(scan_store.os, "replace", lock_existing_snapshot)
    scan_id = await orchestrator.start_scan(
        ScanRequest(username="synthetic-storage-test", agentic=False), scan_id="snapshot-lock",
    )
    await asyncio.wait_for(orchestrator._tasks[scan_id], timeout=10)
    result = orchestrator.scan_store.load(scan_id)
    assert len(blocked) == 3 and module.calls >= 1
    assert result.status == ScanStatus.COMPLETED and result.error is None
