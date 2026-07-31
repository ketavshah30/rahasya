import asyncio
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
import networkx as nx

try:
    from neo4j import AsyncGraphDatabase, AsyncDriver
except ImportError:
    AsyncGraphDatabase = None
    AsyncDriver = None

from rahasya.core.models import Entity, Relationship
from rahasya.config import Settings, settings
from rahasya.utils.logging import get_logger

logger = get_logger(__name__)


class GraphBackend(ABC):
    """Abstract base class for Graph Backends."""
    
    @abstractmethod
    async def add_node(self, entity: Entity) -> str:
        """Add a node to the graph and return its ID."""
        pass

    @abstractmethod
    async def add_edge(self, source_id: str, target_id: str, rel: Relationship) -> str:
        """Add an edge between two nodes."""
        pass

    @abstractmethod
    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a node by its ID."""
        pass

    @abstractmethod
    async def get_neighbors(self, node_id: str, depth: int = 1, rel_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Get neighboring nodes up to a certain depth."""
        pass

    @abstractmethod
    async def get_subgraph(self, center_id: str, radius: int = 2) -> Dict[str, Any]:
        """Get a subgraph around a specific node."""
        pass

    @abstractmethod
    async def find_nodes(self, entity_type: Optional[str] = None, value: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        """Find nodes based on attributes."""
        pass

    @abstractmethod
    async def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Retrieve all nodes in the graph."""
        pass

    @abstractmethod
    async def get_all_edges(self) -> List[Dict[str, Any]]:
        """Retrieve all edges in the graph."""
        pass

    @abstractmethod
    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the graph."""
        pass

    @abstractmethod
    async def export_pyvis(self) -> Dict[str, Any]:
        """Export the graph for PyVis visualization."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear the graph."""
        pass


class NetworkXBackend(GraphBackend):
    """In-memory graph backend using NetworkX."""
    
    def __init__(self):
        self.graph = nx.DiGraph()
        self.lock = asyncio.Lock()

    async def add_node(self, entity: Entity) -> str:
        async with self.lock:
            node_id = str(entity.id)
            self.graph.add_node(
                node_id,
                entity_type=entity.entity_type.value,
                value=entity.value,
                confidence=entity.confidence,
                metadata=entity.metadata,
                scan_id=entity.scan_id or entity.metadata.get("scan_id")
            )
            return node_id

    async def add_edge(self, source_id: str, target_id: str, rel: Relationship) -> str:
        async with self.lock:
            edge_id = str(rel.id)
            self.graph.add_edge(
                source_id,
                target_id,
                id=edge_id,
                rel_type=rel.relationship_type.value,
                confidence=rel.confidence,
                metadata=rel.metadata
            )
            return edge_id

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        async with self.lock:
            if self.graph.has_node(node_id):
                node_data = dict(self.graph.nodes[node_id])
                node_data["id"] = node_id
                return node_data
            return None

    async def get_neighbors(self, node_id: str, depth: int = 1, rel_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        async with self.lock:
            if not self.graph.has_node(node_id):
                return []
            
            neighbors_set = set()
            current_level = {node_id}
            
            for _ in range(depth):
                next_level = set()
                for n in current_level:
                    for neighbor in self.graph.successors(n):
                        if rel_types:
                            edge_data = self.graph.get_edge_data(n, neighbor)
                            if edge_data and edge_data.get("rel_type") in rel_types:
                                next_level.add(neighbor)
                        else:
                            next_level.add(neighbor)
                    
                    for neighbor in self.graph.predecessors(n):
                        if rel_types:
                            edge_data = self.graph.get_edge_data(neighbor, n)
                            if edge_data and edge_data.get("rel_type") in rel_types:
                                next_level.add(neighbor)
                        else:
                            next_level.add(neighbor)
                
                neighbors_set.update(next_level)
                current_level = next_level
            
            # Remove the original node
            if node_id in neighbors_set:
                neighbors_set.remove(node_id)
                
            return [{"id": n, **self.graph.nodes[n]} for n in neighbors_set]

    async def get_subgraph(self, center_id: str, radius: int = 2) -> Dict[str, Any]:
        async with self.lock:
            if not self.graph.has_node(center_id):
                return {"nodes": [], "edges": []}
            
            subgraph_nodes = {center_id}
            current_level = {center_id}
            
            for _ in range(radius):
                next_level = set()
                for n in current_level:
                    next_level.update(self.graph.successors(n))
                    next_level.update(self.graph.predecessors(n))
                subgraph_nodes.update(next_level)
                current_level = next_level
                
            sub_g = self.graph.subgraph(subgraph_nodes)
            
            nodes = [{"id": n, **sub_g.nodes[n]} for n in sub_g.nodes()]
            edges = [{"source": u, "target": v, **d} for u, v, d in sub_g.edges(data=True)]
            
            return {"nodes": nodes, "edges": edges}

    async def find_nodes(self, entity_type: Optional[str] = None, value: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        async with self.lock:
            result = []
            for n, data in self.graph.nodes(data=True):
                match = True
                if entity_type and data.get("entity_type") != entity_type:
                    match = False
                if value and data.get("value") != value:
                    match = False
                for k, v in filters.items():
                    if data.get(k) != v and data.get("metadata", {}).get(k) != v:
                        match = False
                if match:
                    result.append({"id": n, **data})
            return result

    async def get_all_nodes(self) -> List[Dict[str, Any]]:
        async with self.lock:
            return [{"id": n, **d} for n, d in self.graph.nodes(data=True)]

    async def get_all_edges(self) -> List[Dict[str, Any]]:
        async with self.lock:
            return [{"source": u, "target": v, **d} for u, v, d in self.graph.edges(data=True)]

    async def get_stats(self) -> Dict[str, Any]:
        async with self.lock:
            return {
                "num_nodes": self.graph.number_of_nodes(),
                "num_edges": self.graph.number_of_edges(),
            }

    async def export_pyvis(self) -> Dict[str, Any]:
        async with self.lock:
            nodes = []
            for n, d in self.graph.nodes(data=True):
                color = "#97c2fc"
                if str(d.get("entity_type")).lower() == "person":
                    color = "#fb7e81"
                elif str(d.get("entity_type")).lower() == "email":
                    color = "#7be141"
                
                nodes.append({
                    "id": n,
                    "label": d.get("value", n),
                    "title": f"Type: {d.get('entity_type')}<br>Value: {d.get('value')}",
                    "color": color
                })
                
            edges = []
            for u, v, d in self.graph.edges(data=True):
                edges.append({
                    "from": u,
                    "to": v,
                    "label": d.get("rel_type", ""),
                    "title": f"Confidence: {d.get('confidence', 0.0)}"
                })
                
            return {"nodes": nodes, "edges": edges}

    async def clear(self) -> None:
        async with self.lock:
            self.graph.clear()


class Neo4jBackend(GraphBackend):
    """Graph backend using Neo4j database."""
    
    def __init__(self, uri: str, user: str, password: str):
        if AsyncGraphDatabase is None:
            raise ImportError("neo4j driver is not installed. Use 'pip install neo4j'.")
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        
    async def initialize(self):
        # Create indexes
        async with self.driver.session() as session:
            await session.run("CREATE INDEX entity_id IF NOT EXISTS FOR (n:Entity) ON (n.id)")
            await session.run("CREATE INDEX entity_value IF NOT EXISTS FOR (n:Entity) ON (n.value)")
            
    async def add_node(self, entity: Entity) -> str:
        query = """
        MERGE (n:Entity {id: $id})
        SET n.type = $type,
            n.value = $value,
            n.confidence = $confidence,
            n.scan_id = $scan_id
        RETURN n.id as id
        """
        async with self.driver.session() as session:
            result = await session.run(query, 
                                       id=str(entity.id), 
                                       type=entity.entity_type.value,
                                       value=entity.value,
                                       confidence=entity.confidence,
                                       scan_id=entity.scan_id)
            record = await result.single()
            return record["id"]

    async def add_edge(self, source_id: str, target_id: str, rel: Relationship) -> str:
        # Cypher doesn't allow dynamic relationship types in MERGE easily, so we use apoc if possible, 
        # but standard way is to build the query dynamically.
        rel_type = rel.relationship_type.value
        query = f"""
        MATCH (s:Entity {{id: $source_id}}), (t:Entity {{id: $target_id}})
        MERGE (s)-[r:{rel_type} {{id: $rel_id}}]->(t)
        SET r.confidence = $confidence
        RETURN r.id as id
        """
        async with self.driver.session() as session:
            result = await session.run(query, 
                                       source_id=source_id, 
                                       target_id=target_id,
                                       rel_id=str(rel.id),
                                       confidence=rel.confidence)
            record = await result.single()
            return record["id"] if record else str(rel.id)

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        query = "MATCH (n:Entity {id: $id}) RETURN n"
        async with self.driver.session() as session:
            result = await session.run(query, id=node_id)
            record = await result.single()
            if record:
                node = record["n"]
                return dict(node)
            return None

    async def get_neighbors(self, node_id: str, depth: int = 1, rel_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        # This is a simplified version; depth can be handled via variable length paths.
        rel_filter = ""
        if rel_types:
            rel_filter = ":" + "|".join(rel_types)
            
        query = f"MATCH (n:Entity {{id: $id}})-[{rel_filter}*1..{depth}]-(m:Entity) RETURN DISTINCT m"
        async with self.driver.session() as session:
            result = await session.run(query, id=node_id)
            neighbors = []
            async for record in result:
                neighbors.append(dict(record["m"]))
            return neighbors

    async def get_subgraph(self, center_id: str, radius: int = 2) -> Dict[str, Any]:
        query = f"""
        MATCH p = (n:Entity {{id: $id}})-[*0..{radius}]-(m:Entity)
        RETURN nodes(p) AS nodes, relationships(p) AS rels
        """
        nodes = {}
        edges = []
        async with self.driver.session() as session:
            result = await session.run(query, id=center_id)
            async for record in result:
                for node in record["nodes"]:
                    nodes[node["id"]] = dict(node)
                for rel in record["rels"]:
                    edges.append({
                        "source": rel.start_node["id"],
                        "target": rel.end_node["id"],
                        "rel_type": rel.type,
                        "id": rel["id"],
                        "confidence": rel["confidence"]
                    })
        return {"nodes": list(nodes.values()), "edges": edges}

    async def find_nodes(self, entity_type: Optional[str] = None, value: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        conditions = []
        params = {}
        if entity_type:
            conditions.append("n.type = $type")
            params["type"] = entity_type
        if value:
            conditions.append("n.value = $value")
            params["value"] = value
            
        for k, v in filters.items():
            conditions.append(f"n.{k} = ${k}")
            params[k] = v
            
        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"MATCH (n:Entity) {where_clause} RETURN n"
        
        async with self.driver.session() as session:
            result = await session.run(query, **params)
            nodes = []
            async for record in result:
                nodes.append(dict(record["n"]))
            return nodes

    async def get_all_nodes(self) -> List[Dict[str, Any]]:
        query = "MATCH (n:Entity) RETURN n"
        async with self.driver.session() as session:
            result = await session.run(query)
            return [dict(record["n"]) async for record in result]

    async def get_all_edges(self) -> List[Dict[str, Any]]:
        query = "MATCH (s)-[r]->(t) RETURN s.id as source, t.id as target, type(r) as rel_type, r as properties"
        async with self.driver.session() as session:
            result = await session.run(query)
            edges = []
            async for record in result:
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "rel_type": record["rel_type"],
                    **dict(record["properties"])
                })
            return edges

    async def get_stats(self) -> Dict[str, Any]:
        async with self.driver.session() as session:
            node_res = await session.run("MATCH (n) RETURN count(n) as c")
            node_rec = await node_res.single()
            edge_res = await session.run("MATCH ()-[r]->() RETURN count(r) as c")
            edge_rec = await edge_res.single()
            return {
                "num_nodes": node_rec["c"],
                "num_edges": edge_rec["c"]
            }

    async def export_pyvis(self) -> Dict[str, Any]:
        nodes = await self.get_all_nodes()
        edges = await self.get_all_edges()
        
        pyvis_nodes = []
        for d in nodes:
            color = "#97c2fc"
            if str(d.get("type")).lower() == "person":
                color = "#fb7e81"
            elif str(d.get("type")).lower() == "email":
                color = "#7be141"
            
            pyvis_nodes.append({
                "id": d.get("id"),
                "label": d.get("value", d.get("id")),
                "title": f"Type: {d.get('type')}<br>Value: {d.get('value')}",
                "color": color
            })
            
        pyvis_edges = []
        for d in edges:
            pyvis_edges.append({
                "from": d.get("source"),
                "to": d.get("target"),
                "label": d.get("rel_type", ""),
                "title": f"Confidence: {d.get('confidence', 0.0)}"
            })
            
        return {"nodes": pyvis_nodes, "edges": pyvis_edges}

    async def clear(self) -> None:
        async with self.driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")

    async def close(self):
        await self.driver.close()


class GraphManager:
    """Facade for graph operations."""
    
    def __init__(self, config: Optional[Settings] = None):
        self.config = config or settings

        neo4j_settings = getattr(self.config, "neo4j", None)
        use_neo4j = getattr(neo4j_settings, "enabled", False) is True

        if use_neo4j:
            try:
                self.backend = Neo4jBackend(
                    neo4j_settings.uri,
                    neo4j_settings.user,
                    neo4j_settings.password
                )
                logger.info("Initialized Neo4j Graph Backend")
            except Exception as e:
                logger.warning(f"Failed to initialize Neo4j: {e}. Falling back to NetworkX.")
                self.backend = NetworkXBackend()
        else:
            self.backend = NetworkXBackend()
            logger.info("Initialized NetworkX Graph Backend")
            
    async def add_node(self, entity: Entity) -> str:
        return await self.backend.add_node(entity)

    async def add_edge(self, source_id: str, target_id: str, rel: Relationship) -> str:
        return await self.backend.add_edge(source_id, target_id, rel)

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        return await self.backend.get_node(node_id)

    async def get_neighbors(self, node_id: str, depth: int = 1, rel_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await self.backend.get_neighbors(node_id, depth, rel_types)

    async def get_subgraph(self, center_id: str, radius: int = 2) -> Dict[str, Any]:
        return await self.backend.get_subgraph(center_id, radius)

    async def find_nodes(self, entity_type: Optional[str] = None, value: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        return await self.backend.find_nodes(entity_type, value, **filters)

    async def get_all_nodes(self) -> List[Dict[str, Any]]:
        return await self.backend.get_all_nodes()

    async def get_all_edges(self) -> List[Dict[str, Any]]:
        return await self.backend.get_all_edges()

    async def get_stats(self) -> Dict[str, Any]:
        return await self.backend.get_stats()

    async def export_pyvis(self) -> Dict[str, Any]:
        return await self.backend.export_pyvis()

    async def clear(self):
        await self.backend.clear()

    async def get_entity_graph(self, scan_id: str) -> Dict[str, Any]:
        """Get the full graph for a specific scan."""
        nodes = await self.find_nodes(scan_id=scan_id)
        node_ids = {n["id"] for n in nodes}
        
        edges = []
        all_edges = await self.get_all_edges()
        for edge in all_edges:
            if edge["source"] in node_ids and edge["target"] in node_ids:
                edges.append(edge)
                
        return {"nodes": nodes, "edges": edges}

    async def find_connected_components(self) -> List[List[Dict[str, Any]]]:
        """Find groups of related entities (connected components)."""
        if isinstance(self.backend, NetworkXBackend):
            components = list(nx.weakly_connected_components(self.backend.graph))
            result = []
            for comp in components:
                comp_nodes = []
                for node_id in comp:
                    node = await self.get_node(node_id)
                    if node:
                        comp_nodes.append(node)
                result.append(comp_nodes)
            return result
        else:
            # For Neo4j, implementing community detection (connected components) usually requires Graph Data Science library.
            # Simplified fallback:
            logger.warning("find_connected_components not optimally implemented for Neo4j backend without GDS.")
            return []

    async def find_shortest_path(self, source_id: str, target_id: str) -> List[str]:
        """Find shortest path between two entities."""
        if isinstance(self.backend, NetworkXBackend):
            try:
                # Treat as undirected for path finding
                undirected = self.backend.graph.to_undirected()
                return nx.shortest_path(undirected, source=source_id, target=target_id)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return []
        else:
            query = """
            MATCH p = shortestPath((s:Entity {id: $source_id})-[*]-(t:Entity {id: $target_id}))
            RETURN [n in nodes(p) | n.id] as path
            """
            async with self.backend.driver.session() as session:
                result = await session.run(query, source_id=source_id, target_id=target_id)
                record = await result.single()
                if record:
                    return record["path"]
                return []

    async def calculate_centrality(self) -> Dict[str, float]:
        """Find most connected entities."""
        if isinstance(self.backend, NetworkXBackend):
            return nx.degree_centrality(self.backend.graph)
        else:
            query = """
            MATCH (n:Entity)-[r]-()
            RETURN n.id as id, count(r) as degree
            ORDER BY degree DESC
            """
            result_dict = {}
            async with self.backend.driver.session() as session:
                result = await session.run(query)
                async for record in result:
                    # simplistic degree centrality
                    result_dict[record["id"]] = float(record["degree"])
            return result_dict
