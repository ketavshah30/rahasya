import json
import logging
from pyvis.network import Network
from rahasya.core.models import EntityType, RelationshipType

logger = logging.getLogger(__name__)

COLOR_MAP = {
    EntityType.PERSON.value: "#8b5cf6",
    EntityType.EMAIL.value: "#00d4ff",
    EntityType.PHONE.value: "#10b981",
    EntityType.USERNAME.value: "#f59e0b",
    EntityType.SOCIAL_PROFILE.value: "#ec4899",
    EntityType.BREACH_RECORD.value: "#ef4444",
    EntityType.DARK_WEB_MENTION.value: "#dc2626",
    EntityType.URL.value: "#6366f1",
    EntityType.PHOTO.value: "#14b8a6",
    EntityType.LOCATION.value: "#22c55e",
}

def build_pyvis_graph(entities, relationships, config=None):
    """
    Builds a PyVis HTML string from entities and relationships.
    """
    net = Network(height="750px", width="100%", bgcolor="#0a0e17", font_color="#e2e8f0")
    
    # Optional physics config
    net.barnes_hut(gravity=-8000, central_gravity=0.3, spring_length=150)
    
    # Add nodes
    for entity in entities:
        color = COLOR_MAP.get(entity.entity_type.value, "#ffffff")
        net.add_node(
            entity.id,
            label=str(entity.value)[:20] + ("..." if len(str(entity.value)) > 20 else ""),
            title=f"Type: {entity.entity_type.name}\nValue: {entity.value}",
            color=color,
            size=15
        )
        
    # Add edges
    for rel in relationships:
        net.add_edge(
            rel.source_id,
            rel.target_id,
            title=f"Type: {rel.relationship_type.name}\nConfidence: {rel.confidence}",
            value=rel.confidence, # width of edge
            color="rgba(255, 255, 255, 0.4)"
        )
        
    return net.generate_html()
