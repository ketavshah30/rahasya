import streamlit as st
import os
import json
import pandas as pd

def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
load_css()

st.markdown("<h1 class='neon-text'>DATA EXPORT</h1>", unsafe_allow_html=True)
st.markdown("Export scan results and intelligence reports.")

st.markdown("### Select Scan to Export")
# Mock selection
scan_id = st.selectbox("Available Scans", ["Scan-1234 (John Doe)", "Scan-5678 (Jane Smith)"])

st.markdown("### Export Formats")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("""
    <div class="glass-card" style="text-align: center;">
        <h3 style="color: var(--primary-cyan)">📄 HTML Report</h3>
        <p style="font-size: 0.9em; color: var(--text-muted)">Comprehensive stylized report with graphs and risk analysis.</p>
    </div>
    """, unsafe_allow_html=True)
    st.download_button("Download HTML", data="<html><body><h1>Report</h1></body></html>", file_name="report.html", mime="text/html", use_container_width=True)

with col2:
    st.markdown("""
    <div class="glass-card" style="text-align: center;">
        <h3 style="color: var(--primary-cyan)">📊 CSV Data</h3>
        <p style="font-size: 0.9em; color: var(--text-muted)">Tabular data of all discovered entities and metadata.</p>
    </div>
    """, unsafe_allow_html=True)
    st.download_button("Download CSV", data="Entity,Type,Confidence\nJohn Doe,PERSON,1.0", file_name="entities.csv", mime="text/csv", use_container_width=True)

with col3:
    st.markdown("""
    <div class="glass-card" style="text-align: center;">
        <h3 style="color: var(--primary-cyan)">🔗 JSON Graph</h3>
        <p style="font-size: 0.9em; color: var(--text-muted)">Raw JSON graph format compatible with Gephi/Maltego.</p>
    </div>
    """, unsafe_allow_html=True)
    mock_json = json.dumps({"nodes": [], "edges": []})
    st.download_button("Download JSON", data=mock_json, file_name="graph.json", mime="application/json", use_container_width=True)

st.markdown("### Preview")
st.code("""
{
    "scan_id": "Scan-1234",
    "targets": ["John Doe"],
    "summary": {
        "total_entities": 45,
        "risk_score": 78
    },
    "entities": [ ... ]
}
""", language="json")
