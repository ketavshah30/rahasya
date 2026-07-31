import pytest

try:
    from rahasya.core.graph import GraphManager
    from rahasya.core.models import EntityType, Entity, Relationship
    HAS_GRAPH = True
except ImportError:
    HAS_GRAPH = False

pytestmark = pytest.mark.skipif(not HAS_GRAPH, reason="Graph module not found")

@pytest.fixture
def graph():
    if not HAS_GRAPH: return None
    return GraphManager()

@pytest.fixture
def node1():
    if not HAS_GRAPH: return None
    return Entity(entity_type=EntityType.PERSON, value="A", normalized_value="a", source_module="test")

@pytest.fixture
def node2():
    if not HAS_GRAPH: return None
    return Entity(entity_type=EntityType.EMAIL, value="b@c.com", normalized_value="b@c.com", source_module="test")

def test_add_node(graph, node1):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    assert node1.id in graph.get_nodes()

def test_add_edge(graph, node1, node2):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    graph.add_node(node2)
    rel = Relationship(source_id=node1.id, target_id=node2.id, type="OWNS", source_module="test")
    graph.add_edge(rel)
    assert len(graph.get_edges()) == 1

def test_get_neighbors(graph, node1, node2):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    graph.add_node(node2)
    rel = Relationship(source_id=node1.id, target_id=node2.id, type="OWNS", source_module="test")
    graph.add_edge(rel)
    neighbors = graph.get_neighbors(node1.id)
    assert node2.id in neighbors

def test_get_subgraph(graph, node1, node2):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    graph.add_node(node2)
    sub = graph.get_subgraph(node1.id, radius=1)
    assert node1.id in sub.get_nodes()

def test_find_nodes(graph, node1):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    nodes = graph.find_nodes(entity_type=EntityType.PERSON, value="A")
    assert len(nodes) == 1

def test_get_stats(graph, node1, node2):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    graph.add_node(node2)
    stats = graph.get_stats()
    assert stats["nodes"] == 2

def test_export_pyvis(graph, node1):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    out = graph.export_pyvis()
    assert out is not None

def test_find_shortest_path(graph, node1, node2):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    graph.add_node(node2)
    rel = Relationship(source_id=node1.id, target_id=node2.id, type="OWNS", source_module="test")
    graph.add_edge(rel)
    path = graph.find_shortest_path(node1.id, node2.id)
    assert len(path) == 2

def test_connected_components(graph, node1, node2):
    if not HAS_GRAPH: return
    graph.add_node(node1)
    graph.add_node(node2)
    comps = graph.connected_components()
    assert len(comps) == 2
