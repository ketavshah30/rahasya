import uuid
from typing import List
try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

from rahasya.core.models import Entity, EntityType, Relationship, RelationshipType
from rahasya.correlation.graph_manager import GraphManager
from rahasya.config import Settings
from rahasya.utils.logging import get_logger

logger = get_logger(__name__)

class EntityResolver:
    """Entity resolution engine to find matches and create relationships."""

    def __init__(self, graph: GraphManager, config: Settings):
        self.graph = graph
        self.config = config
        self.name_threshold = 85
        self.hash_threshold = 10
    
    async def resolve(self, entities: List[Entity]) -> List[Relationship]:
        """Main entry: find all matches among entities, return SAME_AS relationships."""
        if not entities:
            return []
            
        relationships = []
        relationships.extend(await self._deterministic_match(entities))
        relationships.extend(await self._fuzzy_name_match(entities))
        relationships.extend(await self._cross_source_match(entities))
        relationships.extend(await self._photo_match(entities))
        
        return self._deduplicate_relationships(relationships)
    
    async def _deterministic_match(self, entities: List[Entity]) -> List[Relationship]:
        """Exact email, phone, username matches across different sources."""
        relationships = []
        exact_types = {EntityType.EMAIL, EntityType.PHONE, EntityType.USERNAME}
        
        # Group by type and value
        grouped = {}
        for entity in entities:
            if entity.entity_type in exact_types:
                key = (entity.entity_type, str(entity.value).lower().strip())
                grouped.setdefault(key, []).append(entity)
                
        for (etype, val), group in grouped.items():
            if len(group) > 1:
                # Pairwise comparison
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        e1, e2 = group[i], group[j]
                        # Create relationship if from different sources/modules or just duplicate
                        if e1.id != e2.id:
                            rel = Relationship(
                                id=uuid.uuid4(),
                                source_id=e1.id,
                                target_id=e2.id,
                                relationship_type=RelationshipType.SAME_AS,
                                confidence=1.0,
                                source_module="entity_resolver"
                            )
                            relationships.append(rel)
                            
        return relationships
    
    async def _fuzzy_name_match(self, entities: List[Entity]) -> List[Relationship]:
        """RapidFuzz token_sort_ratio on PERSON entities."""
        if fuzz is None:
            logger.warning("RapidFuzz not installed, skipping fuzzy name match.")
            return []
            
        relationships = []
        persons = [e for e in entities if e.entity_type == EntityType.PERSON]
        
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                p1, p2 = persons[i], persons[j]
                
                # Check scan_id and other context if needed, here we just do value
                val1, val2 = str(p1.value).lower(), str(p2.value).lower()
                
                score = fuzz.token_sort_ratio(val1, val2)
                if score >= self.name_threshold:
                    rel = Relationship(
                        id=uuid.uuid4(),
                        source_id=p1.id,
                        target_id=p2.id,
                        relationship_type=RelationshipType.SAME_AS,
                        confidence=score / 100.0,
                        source_module="entity_resolver"
                    )
                    relationships.append(rel)
                    
        return relationships
    
    async def _cross_source_match(self, entities: List[Entity]) -> List[Relationship]:
        """Cross source heuristics."""
        relationships = []
        # For simplicity, if we have two profiles with identical usernames or emails
        # in different contexts (e.g. metadata context linking), we create a link.
        # This implementation requires broader graph context in a real scenario,
        # but here we'll map common profile linkages within the provided batch.
        
        usernames = {str(e.value).lower(): e for e in entities if e.entity_type == EntityType.USERNAME}
        emails = {str(e.value).lower(): e for e in entities if e.entity_type == EntityType.EMAIL}
        
        for entity in entities:
            if entity.entity_type == EntityType.PROFILE:
                meta = entity.metadata or {}
                uname = meta.get("username", "").lower()
                email = meta.get("email", "").lower()
                
                if uname in usernames:
                    rel = Relationship(
                        id=uuid.uuid4(),
                        source_id=entity.id,
                        target_id=usernames[uname].id,
                        relationship_type=RelationshipType.LINKED_TO,
                        confidence=0.85,
                        source_module="entity_resolver"
                    )
                    relationships.append(rel)
                    
                if email in emails:
                    rel = Relationship(
                        id=uuid.uuid4(),
                        source_id=entity.id,
                        target_id=emails[email].id,
                        relationship_type=RelationshipType.LINKED_TO,
                        confidence=0.9,
                        source_module="entity_resolver"
                    )
                    relationships.append(rel)
                    
        return relationships
    
    async def _photo_match(self, entities: List[Entity]) -> List[Relationship]:
        """Compare perceptual hashes of PHOTO entities."""
        relationships = []
        photos = [e for e in entities if e.entity_type == EntityType.IMAGE]
        
        for i in range(len(photos)):
            for j in range(i + 1, len(photos)):
                p1, p2 = photos[i], photos[j]
                
                h1 = (p1.metadata or {}).get("phash")
                h2 = (p2.metadata or {}).get("phash")
                
                if h1 and h2:
                    # Calculate Hamming distance (assuming hex string hashes of same length)
                    try:
                        bin1 = bin(int(h1, 16))[2:].zfill(64)
                        bin2 = bin(int(h2, 16))[2:].zfill(64)
                        distance = sum(c1 != c2 for c1, c2 in zip(bin1, bin2))
                        
                        if distance <= self.hash_threshold:
                            conf = 1.0 - (distance / 64.0)
                            rel = Relationship(
                                id=uuid.uuid4(),
                                source_id=p1.id,
                                target_id=p2.id,
                                relationship_type=RelationshipType.SAME_AS,
                                confidence=conf,
                                source_module="entity_resolver"
                            )
                            relationships.append(rel)
                    except ValueError:
                        pass
                        
        return relationships
    
    def _deduplicate_relationships(self, rels: List[Relationship]) -> List[Relationship]:
        """Remove duplicate (A,B) == (B,A) relationships."""
        seen = set()
        deduped = []
        for rel in rels:
            # Create a canonical key for the pair
            pair = tuple(sorted([str(rel.source_id), str(rel.target_id)]))
            rel_type = rel.relationship_type.value
            key = (*pair, rel_type)
            
            if key not in seen:
                seen.add(key)
                deduped.append(rel)
                
        return deduped
