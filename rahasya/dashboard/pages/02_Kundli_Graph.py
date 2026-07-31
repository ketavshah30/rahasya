import streamlit as st
import os
import json
import streamlit.components.v1 as components
from pyvis.network import Network

def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
load_css()

st.markdown("<h1 class='neon-text'>KUNDLI GRAPH</h1>", unsafe_allow_html=True)
st.markdown("Interactive entity relationship visualization.")

def get_mock_graph_data():
    return {
        "nodes": [
            {"id": "n1", "label": "John Doe", "group": "PERSON", "title": "Target"},
            {"id": "n2", "label": "johndoe@example.com", "group": "EMAIL", "title": "Breached Email"},
            {"id": "n3", "label": "+1234567890", "group": "PHONE", "title": "Registered Phone"},
            {"id": "n4", "label": "johndoe99", "group": "USERNAME", "title": "Twitter Handle"},
            {"id": "n5", "label": "LinkedIn Profile", "group": "SOCIAL_PROFILE", "title": "LinkedIn"},
        ],
        "edges": [
            {"from": "n1", "to": "n2", "value": 1, "title": "Used in"},
            {"from": "n1", "to": "n3", "value": 1, "title": "Owned by"},
            {"from": "n1", "to": "n4", "value": 0.8, "title": "Alias"},
            {"from": "n4", "to": "n5", "value": 0.6, "title": "Linked account"}
        ]
    }

color_map = {
    "PERSON": "#8b5cf6",
    "EMAIL": "#00d4ff",
    "PHONE": "#10b981",
    "USERNAME": "#f59e0b",
    "SOCIAL_PROFILE": "#ec4899",
    "BREACH_RECORD": "#ef4444",
    "DARK_WEB_MENTION": "#dc2626",
    "URL": "#6366f1",
    "PHOTO": "#14b8a6",
    "LOCATION": "#22c55e"
}

with st.sidebar:
    st.header("Graph Filters")
    entity_types = st.multiselect("Entity Types", list(color_map.keys()), default=list(color_map.keys())[:5])
    min_confidence = st.slider("Min Confidence", 0.0, 1.0, 0.0)
    search_node = st.text_input("Search Node")

data = get_mock_graph_data()

net = Network(height="600px", width="100%", bgcolor="#0a0e17", font_color="#e2e8f0", directed=True)
net.force_atlas_2based()

for node in data["nodes"]:
    if node["group"] in entity_types:
        color = color_map.get(node["group"], "#ffffff")
        net.add_node(node["id"], label=node["label"], title=node["title"], color=color, size=20)

for edge in data["edges"]:
    if edge["value"] >= min_confidence:
        net.add_edge(edge["from"], edge["to"], title=edge["title"], width=edge["value"] * 3)

# Save to html
path = "html_graph.html"
net.save_graph(path)

# Show stats overlay
col1, col2, col3 = st.columns(3)
col1.metric("Nodes", len(data["nodes"]))
col2.metric("Edges", len(data["edges"]))
col3.metric("Components", 1)

with open(path, 'r', encoding='utf-8') as HtmlFile:
    source_code = HtmlFile.read()
    components.html(source_code, height=650)
