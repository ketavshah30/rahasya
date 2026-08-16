from typing import List, Optional
try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

from rahasya.core.models import (
    Entity,
    EntityType,
    PartialEmailEntity,
    PartialPhoneEntity,
    PersonCluster,
    Relationship,
    RelationshipType,
)
from rahasya.correlation.graph_manager import GraphManager
from rahasya.config import Settings, settings
from rahasya.utils.logging import get_logger

logger = get_logger(__name__)

class EntityResolver:
    """Entity resolution engine to find matches and create relationships."""

    def __init__(self, graph: Optional[GraphManager] = None, config: Optional[Settings] = None):
        self.config = config or settings
        self.graph = graph or GraphManager(self.config)
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
        relationships.extend(await self._recovery_hint_match(entities))
        
        return self._deduplicate_relationships(relationships)
    
    async def _deterministic_match(self, entities: List[Entity]) -> List[Relationship]:
        """Exact email, phone, username matches across different sources."""
        relationships = []
        exact_types = {EntityType.EMAIL, EntityType.PHONE, EntityType.USERNAME}
        
        # Group by type and value
        grouped = {}
        for entity in entities:
            if entity.entity_type in exact_types:
                key = (entity.entity_type, entity.normalized_value)
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
                val1, val2 = p1.normalized_value, p2.normalized_value
                
                score = fuzz.token_sort_ratio(val1, val2)
                if score >= self.name_threshold:
                    rel = Relationship(
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
        
        usernames = {e.normalized_value: e for e in entities if e.entity_type == EntityType.USERNAME}
        emails = {e.normalized_value: e for e in entities if e.entity_type == EntityType.EMAIL}
        
        for entity in entities:
            if entity.entity_type == EntityType.SOCIAL_PROFILE:
                meta = entity.metadata or {}
                uname = meta.get("username", "").lower()
                email = meta.get("email", "").lower()
                
                if uname in usernames:
                    rel = Relationship(
                        source_id=entity.id,
                        target_id=usernames[uname].id,
                        relationship_type=RelationshipType.LINKED_TO,
                        confidence=0.85,
                        source_module="entity_resolver"
                    )
                    relationships.append(rel)
                    
                if email in emails:
                    rel = Relationship(
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
        photos = [e for e in entities if e.entity_type == EntityType.PHOTO]
        
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
                        
                        if distance <= 12:
                            conf = 1.0 - (distance / 64.0)
                            rel = Relationship(
                                source_id=p1.id,
                                target_id=p2.id,
                                relationship_type=(
                                    RelationshipType.SAME_AS
                                    if distance <= 6
                                    else RelationshipType.LIKELY_SAME
                                ),
                                confidence=conf,
                                source_module="entity_resolver"
                            )
                            relationships.append(rel)
                    except ValueError:
                        pass
                        
        return relationships

    async def _recovery_hint_match(self, entities: List[Entity]) -> List[Relationship]:
        """Lock masked provider hints to known values and cross-link shared hints."""
        relationships: List[Relationship] = []
        partials = [e for e in entities if e.entity_type in {EntityType.PARTIAL_EMAIL, EntityType.PARTIAL_PHONE}]
        for partial in partials:
            expected = EntityType.EMAIL if partial.entity_type == EntityType.PARTIAL_EMAIL else EntityType.PHONE
            matcher_cls = PartialEmailEntity if partial.entity_type == EntityType.PARTIAL_EMAIL else PartialPhoneEntity
            matcher = matcher_cls.model_validate(partial.model_dump())
            for known in entities:
                if known.entity_type == expected and matcher.matches_pattern(known.normalized_value):
                    relationships.append(Relationship(
                        source_id=partial.id,
                        target_id=known.id,
                        relationship_type=RelationshipType.ALT_ACCOUNT_OF,
                        confidence=0.92,
                        source_module="recovery_matcher",
                        metadata={"reason": "masked recovery hint matched known identifier"},
                    ))

        grouped = {}
        for partial in partials:
            grouped.setdefault((partial.entity_type, partial.normalized_value), []).append(partial)
        for group in grouped.values():
            for index, left in enumerate(group):
                for right in group[index + 1:]:
                    if left.id != right.id:
                        relationships.append(Relationship(
                            source_id=left.id,
                            target_id=right.id,
                            relationship_type=RelationshipType.SHARES_RECOVERY,
                            confidence=0.85,
                            source_module="recovery_matcher",
                        ))
        return relationships

    def build_person_clusters(
        self,
        entities: List[Entity],
        relationships: List[Relationship],
    ) -> List[PersonCluster]:
        """Build deterministic connected identity clusters for persistence/UI."""
        parents = {entity.id: entity.id for entity in entities}

        def find(item):
            while parents[item] != item:
                parents[item] = parents[parents[item]]
                item = parents[item]
            return item

        def union(left, right):
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        identity_edges = {
            RelationshipType.SAME_AS,
            RelationshipType.ALT_ACCOUNT_OF,
            RelationshipType.SHARES_RECOVERY,
            RelationshipType.HAS_EMAIL,
            RelationshipType.HAS_PHONE,
            RelationshipType.USES_USERNAME,
            RelationshipType.HAS_PROFILE,
        }
        for relationship in relationships:
            if (
                relationship.relationship_type in identity_edges
                and relationship.confidence >= self.config.scan.confidence_threshold
                and relationship.source_id in parents
                and relationship.target_id in parents
            ):
                union(relationship.source_id, relationship.target_id)
        grouped = {}
        for entity_id in parents:
            grouped.setdefault(find(entity_id), []).append(entity_id)
        return [
            PersonCluster(entity_ids=members, evidence=["resolved identity graph"])
            for members in grouped.values()
            if len(members) > 1
        ]
    
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

    def deterministic_match(self, left: Entity, right: Entity) -> bool:
        exact_types = {EntityType.EMAIL, EntityType.PHONE, EntityType.USERNAME}
        return (
            left.entity_type == right.entity_type
            and left.entity_type in exact_types
            and left.normalized_value.lower().strip() == right.normalized_value.lower().strip()
        )

    def fuzzy_name_match(self, left: Entity, right: Entity) -> bool:
        if left.entity_type != EntityType.PERSON or right.entity_type != EntityType.PERSON:
            return False
        if fuzz is None:
            return left.normalized_value.lower().strip() == right.normalized_value.lower().strip()
        score = fuzz.token_sort_ratio(left.normalized_value, right.normalized_value)
        return score >= self.name_threshold

    def deduplicate_relationships(self, rels: List[Relationship]) -> List[Relationship]:
        return self._deduplicate_relationships(rels)
