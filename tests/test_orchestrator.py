import pytest
from unittest.mock import Mock

try:
    from rahasya.core.orchestrator import Orchestrator
    from rahasya.core.models import ScanRequest, EntityType
    HAS_ORCH = True
except ImportError:
    HAS_ORCH = False

pytestmark = pytest.mark.skipif(not HAS_ORCH, reason="Orchestrator module not found")

@pytest.fixture
def orchestrator(settings):
    if not HAS_ORCH: return None
    return Orchestrator(settings)

def test_generate_seed_entities(orchestrator, sample_scan_request):
    if not HAS_ORCH: return
    seeds = orchestrator.generate_seed_entities(sample_scan_request)
    assert len(seeds) > 0
    types = [s.entity_type for s in seeds]
    assert EntityType.PERSON in types

def test_seed_from_email(orchestrator):
    if not HAS_ORCH: return
    seeds = orchestrator.seed_from_email("test@example.com")
    types = [s.entity_type for s in seeds]
    assert EntityType.EMAIL in types
    assert EntityType.USERNAME in types

def test_seed_from_name(orchestrator):
    if not HAS_ORCH: return
    seeds = orchestrator.seed_from_name("John Doe")
    types = [s.entity_type for s in seeds]
    assert EntityType.PERSON in types
    assert EntityType.USERNAME in types

def test_seed_from_phone(orchestrator):
    if not HAS_ORCH: return
    seeds = orchestrator.seed_from_phone("+1234567890")
    assert len(seeds) == 1
    assert seeds[0].entity_type == EntityType.PHONE

def test_seed_from_username(orchestrator):
    if not HAS_ORCH: return
    seeds = orchestrator.seed_from_username("johndoe")
    assert len(seeds) == 1
    assert seeds[0].entity_type == EntityType.USERNAME

def test_register_entity_dedup(orchestrator, sample_entity):
    if not HAS_ORCH: return
    orchestrator.register_entity(sample_entity)
    orchestrator.register_entity(sample_entity)
    assert len(orchestrator.entities) == 1

def test_infer_relationship_type(orchestrator, sample_entities):
    if not HAS_ORCH: return
    person = sample_entities[0]
    email = sample_entities[1]
    rel = orchestrator.infer_relationship_type(person, email)
    assert rel is not None

def test_get_scan_result(orchestrator):
    if not HAS_ORCH: return
    res = orchestrator.get_scan_result("123")
    assert res.scan_id == "123"
