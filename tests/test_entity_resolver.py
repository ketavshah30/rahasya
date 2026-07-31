import pytest

try:
    from rahasya.core.entity_resolver import EntityResolver
    from rahasya.core.models import EntityType, Entity, Relationship
    HAS_RESOLVER = True
except ImportError:
    HAS_RESOLVER = False

pytestmark = pytest.mark.skipif(not HAS_RESOLVER, reason="EntityResolver module not found")

@pytest.fixture
def resolver():
    if not HAS_RESOLVER: return None
    return EntityResolver()

def test_deterministic_match(resolver):
    if not HAS_RESOLVER: return
    e1 = Entity(entity_type=EntityType.EMAIL, value="a@b.com", normalized_value="a@b.com", source_module="m1")
    e2 = Entity(entity_type=EntityType.EMAIL, value="A@B.COM", normalized_value="a@b.com", source_module="m2")
    assert resolver.deterministic_match(e1, e2) is True

def test_fuzzy_name_match(resolver):
    if not HAS_RESOLVER: return
    e1 = Entity(entity_type=EntityType.PERSON, value="John Doe", normalized_value="john doe", source_module="m1")
    e2 = Entity(entity_type=EntityType.PERSON, value="john doe", normalized_value="john doe", source_module="m2")
    assert resolver.fuzzy_name_match(e1, e2) is True

def test_no_false_positive(resolver):
    if not HAS_RESOLVER: return
    e1 = Entity(entity_type=EntityType.PERSON, value="John", normalized_value="john", source_module="m1")
    e2 = Entity(entity_type=EntityType.PERSON, value="Jane", normalized_value="jane", source_module="m2")
    assert resolver.deterministic_match(e1, e2) is False

def test_deduplicate_relationships(resolver):
    if not HAS_RESOLVER: return
    r1 = Relationship(source_id="1", target_id="2", type="KNOWS", source_module="m1")
    r2 = Relationship(source_id="2", target_id="1", type="KNOWS", source_module="m2")
    deduped = resolver.deduplicate_relationships([r1, r2])
    assert len(deduped) == 1
