import streamlit as st
from rahasya.core.models import Entity, EntityType

def render_entity_card(entity: Entity):
    """
    Renders a detailed card for a single entity based on its type.
    """
    st.markdown(f"### {entity.entity_type.name}")
    st.markdown(f"**Value:** {entity.value}")
    
    if entity.metadata:
        st.markdown("#### Details")
        for k, v in entity.metadata.items():
            st.markdown(f"- **{k}:** {v}")
