import os

import plotly.graph_objects as go
import streamlit as st

from rahasya.dashboard.state import calculate_risk, get_current_result


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()

st.markdown("<h1 class='neon-text'>EXPOSURE REPORT</h1>", unsafe_allow_html=True)
st.markdown("Risk and footprint exposure assessment for the active scan.")

result = get_current_result(st)
if result is None:
    st.info("No active scan result found. Run a scan from New Scan first.")
else:
    data = calculate_risk(result)

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=data["overall_score"],
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": "Overall Risk Score", "font": {"color": "#e2e8f0", "size": 24}},
        gauge={
            "axis": {"range": [None, 100], "tickwidth": 1, "tickcolor": "#e2e8f0"},
            "bar": {"color": "#ff003c"},
            "bgcolor": "rgba(0,0,0,0)",
            "borderwidth": 2,
            "bordercolor": "#00f0ff",
            "steps": [
                {"range": [0, 33], "color": "rgba(16, 185, 129, 0.3)"},
                {"range": [33, 66], "color": "rgba(245, 158, 11, 0.3)"},
                {"range": [66, 100], "color": "rgba(239, 68, 68, 0.3)"},
            ],
        },
    ))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font={"color": "#e2e8f0"})
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### Scan Summary")
    cols = st.columns(4)
    cols[0].metric("Entities", result.stats.total_entities)
    cols[1].metric("Relationships", result.stats.total_relationships)
    cols[2].metric("Depth", result.stats.depth_reached)
    cols[3].metric("Modules Run", result.stats.modules_run)

    st.markdown("### Risk Breakdown")
    col1, col2 = st.columns(2)

    for i, (category, score) in enumerate(data["categories"].items()):
        col = col1 if i % 2 == 0 else col2
        color = "#ef4444" if score > 70 else "#f59e0b" if score > 30 else "#10b981"
        with col:
            st.markdown(f"""
            <div class="glass-card">
                <h4 style="margin:0;">{category}</h4>
                <div style="display: flex; align-items: center; margin-top: 10px;">
                    <div style="flex-grow: 1; background: rgba(255,255,255,0.1); height: 10px; border-radius: 5px;">
                        <div style="width: {score}%; background: {color}; height: 100%; border-radius: 5px;"></div>
                    </div>
                    <span style="margin-left: 15px; font-family: monospace; color: {color}">{score}/100</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("### Entity Breakdown")
    st.json(result.stats.by_type)

    st.markdown("### Recommendations")
    if result.stats.by_type.get("breach_record", 0) or result.stats.by_type.get("leak_record", 0):
        st.warning("Breach or leak records were found. Rotate affected passwords and enable MFA.")
    if result.stats.by_type.get("dark_web_mention", 0):
        st.warning("Dark web mentions were found. Review context and monitor exposed identifiers.")
    if result.stats.by_type.get("social_profile", 0):
        st.info("Review privacy settings on discovered social profiles.")
    if data["overall_score"] == 0:
        st.success("No high-risk indicators were discovered by enabled modules.")
