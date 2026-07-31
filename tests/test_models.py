import pytest
from datetime import datetime
try:
    from pydantic import ValidationError
except ImportError:
    pass

try:
    from rahasya.core.models import (
        Entity, EntityType, SourceReliability, PersonEntity, 
        EmailEntity, PhoneEntity, ScanRequest, ScanResult, Relationship
    )
    HAS_MODELS = True
except ImportError:
    HAS_MODELS = False

pytestmark = pytest.mark.skipif(not HAS_MODELS, reason="Models module not found")

def test_entity_creation():
    if not HAS_MODELS: return
    entity = Entity(
        entity_type=EntityType.PERSON,
        value="John Doe",
        normalized_value="john doe",
        source_module="test"
    )
    assert entity.entity_type == EntityType.PERSON
    assert entity.value == "John Doe"
    assert entity.confidence == 1.0
    assert entity.metadata == {}
    assert entity.depth == 0
    assert hasattr(entity, "id")
    assert hasattr(entity, "discovered_at")

def test_entity_validation_confidence():
    if not HAS_MODELS: return
    with pytest.raises(ValidationError):
        Entity(
            entity_type=EntityType.PERSON,
            value="John",
            normalized_value="john",
            source_module="test",
            confidence=1.5
        )
    with pytest.raises(ValidationError):
        Entity(
            entity_type=EntityType.PERSON,
            value="John",
            normalized_value="john",
            source_module="test",
            confidence=-0.1
        )

def test_entity_subtypes():
    if not HAS_MODELS: return
    person = PersonEntity(entity_type=EntityType.PERSON, value="John", normalized_value="john", source_module="test")
    assert person.entity_type == EntityType.PERSON

    email = EmailEntity(entity_type=EntityType.EMAIL, value="j@e.com", normalized_value="j@e.com", source_module="test")
    assert email.entity_type == EntityType.EMAIL

    phone = PhoneEntity(entity_type=EntityType.PHONE, value="123", normalized_value="123", source_module="test")
    assert phone.entity_type == EntityType.PHONE

def test_scan_request_creation():
    if not HAS_MODELS: return
    req = ScanRequest(target_name="John")
    assert req.target_name == "John"
    assert req.max_depth == 3

def test_scan_result_creation():
    if not HAS_MODELS: return
    res = ScanResult(scan_id="123", status="completed", entities_found=10, relationships_found=5)
    assert res.entities_found == 10

def test_relationship_creation():
    if not HAS_MODELS: return
    rel = Relationship(source_id="1", target_id="2", type="OWNS", source_module="test")
    assert rel.source_id == "1"
    assert rel.target_id == "2"

def test_enums():
    if not HAS_MODELS: return
    assert EntityType.PERSON.value == "person"
    assert SourceReliability.HIGH.value == "high"

def test_serialization():
    if not HAS_MODELS: return
    entity = Entity(
        entity_type=EntityType.PERSON,
        value="John Doe",
        normalized_value="john doe",
        source_module="test"
    )
    data = entity.model_dump()
    assert data["value"] == "John Doe"
    entity_copy = Entity.model_validate(data)
    assert entity_copy.id == entity.id
