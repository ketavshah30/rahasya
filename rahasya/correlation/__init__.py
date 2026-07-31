"""Correlation engine package."""

from .graph_manager import GraphManager, GraphBackend, NetworkXBackend, Neo4jBackend
from .entity_resolver import EntityResolver
from .relationship_rules import RuleEngine, RelationshipRule, RuleCondition

__all__ = [
    "GraphManager",
    "GraphBackend",
    "NetworkXBackend",
    "Neo4jBackend",
    "EntityResolver",
    "RuleEngine",
    "RelationshipRule",
    "RuleCondition"
]
