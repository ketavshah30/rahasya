"""Synchronous compatibility facade for graph management.

The production graph API in :mod:`rahasya.correlation.graph_manager` is async.
This wrapper keeps older synchronous callers working without changing the
orchestrator's async graph backend.
"""

import asyncio
from typing import Any, Dict, List, Optional

from rahasya.config import Settings
from rahasya.core.models import Entity, Relationship
from rahasya.correlation.graph_manager import GraphManager as AsyncGraphManager


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("The synchronous graph facade cannot run inside an active event loop.")


class _Subgraph:
    def __init__(self, payload: Dict[str, Any]):
        self.payload = payload

    def get_nodes(self) -> List[str]:
        return [node["id"] for node in self.payload.get("nodes", [])]

    def get_edges(self) -> List[Dict[str, Any]]:
        return self.payload.get("edges", [])


class GraphManager:
    def __init__(self, config: Optional[Settings] = None):
        self._async = AsyncGraphManager(config)

    def add_node(self, entity: Entity) -> str:
        return _run(self._async.add_node(entity))

    def add_edge(self, *args) -> str:
        if len(args) == 1 and isinstance(args[0], Relationship):
            rel = args[0]
            return _run(self._async.add_edge(rel.source_id, rel.target_id, rel))
        if len(args) == 3:
            return _run(self._async.add_edge(args[0], args[1], args[2]))
        raise TypeError("add_edge expects Relationship or source_id, target_id, relationship")

    def get_nodes(self) -> List[str]:
        return [node["id"] for node in _run(self._async.get_all_nodes())]

    def get_edges(self) -> List[Dict[str, Any]]:
        return _run(self._async.get_all_edges())

    def get_neighbors(self, node_id: str, depth: int = 1) -> List[str]:
        return [node["id"] for node in _run(self._async.get_neighbors(node_id, depth))]

    def get_subgraph(self, center_id: str, radius: int = 2) -> _Subgraph:
        return _Subgraph(_run(self._async.get_subgraph(center_id, radius)))

    def find_nodes(self, entity_type=None, value: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        etype = entity_type.value if hasattr(entity_type, "value") else entity_type
        return _run(self._async.find_nodes(etype, value, **filters))

    def get_stats(self) -> Dict[str, int]:
        stats = _run(self._async.get_stats())
        return {
            "nodes": stats.get("num_nodes", 0),
            "edges": stats.get("num_edges", 0),
            **stats,
        }

    def export_pyvis(self) -> Dict[str, Any]:
        return _run(self._async.export_pyvis())

    def find_shortest_path(self, source_id: str, target_id: str) -> List[str]:
        return _run(self._async.find_shortest_path(source_id, target_id))

    def connected_components(self) -> List[List[Dict[str, Any]]]:
        return _run(self._async.find_connected_components())


__all__ = ["GraphManager"]
