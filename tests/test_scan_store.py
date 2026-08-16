from datetime import datetime, timezone

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
