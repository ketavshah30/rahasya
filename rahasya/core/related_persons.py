"""Related-person selection (FIXES_NEW.md §G.2).

Given a set of entities and relationships discovered during a scan, this
module identifies candidate related persons (parents, siblings, spouses,
colleagues) and returns the top-N by evidence count.

Operator decisions (§5, choices 2-5):
  * max_related_depth = 1: related persons never spawn further related persons.
  * max_related_persons_per_scan = 3 (hard cap).
  * Tie-breaker: (Option A) top 3 by number of *distinct providers*
    attesting the relationship. Ties broken by relationship strength
    (PARENT_OF > SIBLING_OF > SPOUSE_OF > KNOWS), then by first-discovered
    timestamp.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set

from rahasya.core.models import Entity, EntityType, Relationship, RelationshipType


# Higher rank = stronger relationship. Used as a secondary tie-breaker.
RELATIONSHIP_STRENGTH: Dict[RelationshipType, int] = {
    RelationshipType.PARENT_OF: 40,
    RelationshipType.SIBLING_OF: 30,
    RelationshipType.SPOUSE_OF: 20,
    RelationshipType.KNOWS: 10,
    RelationshipType.WORKS_WITH: 10,
    RelationshipType.LINKED_TO: 5,
}

# Relationship types that indicate "this is another person we should look
# into." Anything else is ignored by the related-person selector.
RELATED_PERSON_EDGE_TYPES: Set[RelationshipType] = {
    RelationshipType.PARENT_OF,
    RelationshipType.SIBLING_OF,
    RelationshipType.SPOUSE_OF,
    RelationshipType.KNOWS,
    RelationshipType.WORKS_WITH,
}


@dataclass
class RelatedPersonCandidate:
    """A candidate for related-person expansion."""

    entity_id: str
    entity: Entity
    edges: List[Relationship] = field(default_factory=list)

    @property
    def evidence_score(self) -> int:
        """Number of distinct providers attesting the relationship."""
        return len({edge.source_module for edge in self.edges if edge.source_module})

    @property
    def top_strength(self) -> int:
        return max(
            (RELATIONSHIP_STRENGTH.get(edge.relationship_type, 0) for edge in self.edges),
            default=0,
        )

    @property
    def first_seen(self) -> datetime:
        return min(
            (edge.discovered_at for edge in self.edges),
            default=datetime.now(timezone.utc),
        )


def select_related_persons(
    entities: Iterable[Entity],
    relationships: Iterable[Relationship],
    seed_entity_ids: Set[str],
    cap: int = 3,
) -> List[RelatedPersonCandidate]:
    """Return up to `cap` related-person candidates ranked by evidence.

    Args:
        entities: All entities discovered so far in the scan.
        relationships: All relationships discovered so far.
        seed_entity_ids: IDs of the primary target's own entities — these
            are NOT candidates for related-person expansion.
        cap: Hard cap (default 3).

    Returns:
        List of RelatedPersonCandidate ordered by (evidence_score desc,
        top_strength desc, first_seen asc). Never longer than `cap`.
    """
    entity_by_id = {entity.id: entity for entity in entities}
    seed_ids = set(seed_entity_ids)

    candidates: Dict[str, RelatedPersonCandidate] = {}
    for rel in relationships:
        if rel.relationship_type not in RELATED_PERSON_EDGE_TYPES:
            continue
        # Consider both endpoints — either one might be the "other person."
        for endpoint in (rel.source_id, rel.target_id):
            if endpoint in seed_ids:
                continue
            other = entity_by_id.get(endpoint)
            if other is None or other.entity_type != EntityType.PERSON:
                continue
            candidate = candidates.setdefault(
                endpoint, RelatedPersonCandidate(entity_id=endpoint, entity=other)
            )
            candidate.edges.append(rel)

    ordered = sorted(
        candidates.values(),
        key=lambda c: (
            -c.evidence_score,
            -c.top_strength,
            c.first_seen,
        ),
    )
    return ordered[: max(0, cap)]
