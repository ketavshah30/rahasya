import os

import streamlit as st
import streamlit.components as components

from rahasya.dashboard.components.graph_viewer import build_pyvis_graph
from rahasya.dashboard.state import (
    autorefresh_running,
    person_clusters,
    render_scan_detail_bar,
    shortest_path,
)


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as stream:
            st.markdown(f"<style>{stream.read()}</style>", unsafe_allow_html=True)


load_css()
st.markdown("<h1 class='neon-text'>CIA WEB // CORRELATION MATRIX</h1>", unsafe_allow_html=True)
st.markdown("Filter identities, inspect evidence, and trace why two datapoints are connected.")

result = render_scan_detail_bar(st, "cia_web")
autorefresh_running(st, result, "cia_web")

if result is not None:
    all_types = sorted({entity.entity_type.value for entity in result.entities})
    all_relationships = sorted({rel.relationship_type.value for rel in result.relationships})
    with st.sidebar:
        st.header("Correlation filters")
        entity_types = st.multiselect("Entity types", all_types, default=all_types)
        relationship_types = st.multiselect("Relationship types", all_relationships, default=all_relationships)
        min_confidence = st.slider("Minimum confidence", 0.0, 1.0, 0.0)
        search_node = st.text_input("Search nodes")
        physics = st.selectbox(
            "Physics preset",
            ["Force-Directed", "Hierarchical (by depth)", "Cluster-focused"],
        )

    filtered_entities = [
        entity for entity in result.entities
        if entity.entity_type.value in entity_types
        and entity.confidence >= min_confidence
        and (not search_node or search_node.casefold() in str(entity.value).casefold())
    ]
    filtered_ids = {entity.id for entity in filtered_entities}
    filtered_relationships = [
        rel for rel in result.relationships
        if rel.source_id in filtered_ids
        and rel.target_id in filtered_ids
        and rel.relationship_type.value in relationship_types
        and rel.confidence >= min_confidence
    ]

    labels = {f"{entity.value} [{entity.entity_type.value}] · {entity.id[:6]}": entity.id for entity in filtered_entities}
    path = []
    with st.expander("Connection path finder", expanded=False):
        if len(labels) >= 2:
            left, right = st.columns(2)
            source_label = left.selectbox("From", list(labels), key="path_source")
            remaining = [label for label in labels if label != source_label]
            target_label = right.selectbox("To", remaining, key="path_target")
            path = shortest_path(result, labels[source_label], labels[target_label])
            by_id = {entity.id: entity.value for entity in result.entities}
            if path:
                st.success(" → ".join(str(by_id.get(node_id, node_id)) for node_id in path))
                path_ids = set(path)
                filtered_entities = [entity for entity in result.entities if entity.id in filtered_ids | path_ids]
                filtered_ids = {entity.id for entity in filtered_entities}
                filtered_relationships = [
                    rel for rel in result.relationships
                    if rel.source_id in filtered_ids and rel.target_id in filtered_ids
                    and rel.relationship_type.value in relationship_types
                    and rel.confidence >= min_confidence
                ]
            else:
                st.warning("No connection exists between the selected nodes in this scan.")
        else:
            st.caption("At least two visible nodes are required.")

    metrics = st.columns(4)
    metrics[0].metric("Investigation", result.scan_id[:8])
    metrics[1].metric("Visible nodes", len(filtered_entities))
    metrics[2].metric("Visible edges", len(filtered_relationships))
    metrics[3].metric("Person clusters", len(set(person_clusters(result).values())))

    if not filtered_entities:
        st.warning("No entities match the selected filters.")
    else:
        graph_html = build_pyvis_graph(
            filtered_entities,
            filtered_relationships,
            clusters=person_clusters(result),
            physics=physics,
            highlight_path=path,
        )
        components.v1.html(graph_html, height=800, scrolling=True)
        with st.expander("Visible entity evidence"):
            st.dataframe(
                [{
                    "type": entity.entity_type.value,
                    "value": entity.value,
                    "source": entity.source_module,
                    "confidence": entity.confidence,
                    "depth": entity.depth,
                    "cluster": person_clusters(result).get(entity.id),
                } for entity in filtered_entities],
                width="stretch",
            )
