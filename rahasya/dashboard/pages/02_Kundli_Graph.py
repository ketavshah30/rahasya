import os

import streamlit as st
import streamlit.components.v1 as components

from rahasya.dashboard.components.graph_viewer import build_pyvis_graph
from rahasya.dashboard.state import get_current_result


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()

st.markdown("<h1 class='neon-text'>KUNDLI GRAPH</h1>", unsafe_allow_html=True)
st.markdown("Interactive entity relationship visualization for the active scan.")

result = get_current_result(st)

if result is None:
    st.info("No active scan result found. Run a scan from New Scan first.")
else:
    all_types = sorted({entity.entity_type.value for entity in result.entities})

    with st.sidebar:
        st.header("Graph Filters")
        entity_types = st.multiselect("Entity Types", all_types, default=all_types)
        min_confidence = st.slider("Min Confidence", 0.0, 1.0, 0.0)
        search_node = st.text_input("Search Node")

    filtered_entities = [
        entity
        for entity in result.entities
        if entity.entity_type.value in entity_types
        and entity.confidence >= min_confidence
        and (not search_node or search_node.lower() in str(entity.value).lower())
    ]
    filtered_ids = {entity.id for entity in filtered_entities}
    filtered_relationships = [
        rel
        for rel in result.relationships
        if rel.source_id in filtered_ids
        and rel.target_id in filtered_ids
        and rel.confidence >= min_confidence
    ]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Scan", result.scan_id[:8])
    col2.metric("Nodes", len(filtered_entities))
    col3.metric("Edges", len(filtered_relationships))
    col4.metric("Status", result.status.value)

    if not filtered_entities:
        st.warning("No entities match the selected filters.")
    else:
        html = build_pyvis_graph(filtered_entities, filtered_relationships)
        components.html(html, height=780, scrolling=True)

        with st.expander("Raw Entities"):
            st.dataframe(
                [
                    {
                        "type": entity.entity_type.value,
                        "value": entity.value,
                        "source": entity.source_module,
                        "confidence": entity.confidence,
                        "depth": entity.depth,
                    }
                    for entity in filtered_entities
                ],
                use_container_width=True,
            )
