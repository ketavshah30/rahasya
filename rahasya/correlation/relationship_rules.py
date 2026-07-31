import yaml
import uuid
from typing import List, Any, Optional
from pydantic import BaseModel, Field

from rahasya.core.models import Entity, Relationship, RelationshipType
from rahasya.utils.logging import get_logger

logger = get_logger(__name__)


class RuleCondition(BaseModel):
    field: str
    operator: str  # eq, ne, in, contains, gt, lt
    value: Any


class RelationshipRule(BaseModel):
    name: str
    description: str
    source_conditions: List[RuleCondition]
    target_conditions: List[RuleCondition]
    relationship_type: RelationshipType
    confidence: float
    enabled: bool = True


class RuleEngine:
    def __init__(self, rules_path: str):
        self.rules = self._load_rules(rules_path)
        logger.info(f"Loaded {len(self.rules)} relationship rules from {rules_path}")

    def evaluate(self, source: Entity, target: Entity) -> Optional[Relationship]:
        """Check all rules, return relationship if any rule matches."""
        for rule in self.rules:
            if not rule.enabled:
                continue

            if self._match_conditions(source, rule.source_conditions) and \
               self._match_conditions(target, rule.target_conditions):
                return Relationship(
                    id=uuid.uuid4(),
                    source_id=source.id,
                    target_id=target.id,
                    relationship_type=rule.relationship_type,
                    confidence=rule.confidence,
                    source_module="rule_engine"
                )
                
            # Check reverse match as well
            if self._match_conditions(target, rule.source_conditions) and \
               self._match_conditions(source, rule.target_conditions):
                return Relationship(
                    id=uuid.uuid4(),
                    source_id=target.id,
                    target_id=source.id,
                    relationship_type=rule.relationship_type,
                    confidence=rule.confidence,
                    source_module="rule_engine"
                )
                
        return None

    def _match_conditions(self, entity: Entity, conditions: List[RuleCondition]) -> bool:
        if not conditions:
            return True
            
        for condition in conditions:
            # Resolve field value
            if hasattr(entity, condition.field):
                actual_val = getattr(entity, condition.field)
                # handle enums
                if hasattr(actual_val, 'value'):
                    actual_val = actual_val.value
            elif entity.metadata and condition.field in entity.metadata:
                actual_val = entity.metadata[condition.field]
            else:
                return False

            if not self._evaluate_operator(actual_val, condition.operator, condition.value):
                return False
                
        return True

    def _evaluate_operator(self, actual: Any, operator: str, expected: Any) -> bool:
        try:
            if operator == 'eq':
                return actual == expected
            elif operator == 'ne':
                return actual != expected
            elif operator == 'in':
                return actual in expected
            elif operator == 'contains':
                return expected in actual
            elif operator == 'gt':
                return actual > expected
            elif operator == 'lt':
                return actual < expected
        except TypeError:
            return False
            
        return False

    def _load_rules(self, path: str) -> List[RelationshipRule]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                
            rules = []
            for rule_dict in data.get('rules', []):
                # Ensure relationship_type is correct enum
                try:
                    rule_dict['relationship_type'] = RelationshipType(rule_dict['relationship_type'])
                    rule = RelationshipRule(**rule_dict)
                    rules.append(rule)
                except ValueError as ve:
                    logger.warning(f"Invalid rule '{rule_dict.get('name')}': {ve}")
            return rules
        except Exception as e:
            logger.error(f"Failed to load rules from {path}: {e}")
            return []
