import os

import plotly.graph_objects as go
import streamlit as st

from rahasya.dashboard.state import autorefresh_running, calculate_risk, render_scan_detail_bar


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as stream:
            st.markdown(f"<style>{stream.read()}</style>", unsafe_allow_html=True)


load_css()
st.markdown("<h1 class='neon-text'>EXPOSURE & RISK MODEL</h1>", unsafe_allow_html=True)
st.markdown("A documented, evidence-weighted assessment—not a raw result count.")

result = render_scan_detail_bar(st, "exposure")
autorefresh_running(st, result, "exposure")
if result is not None:
    data = calculate_risk(result)
    categories = list(data["categories"])
    scores = [data["categories"][name] for name in categories]

    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=data["overall_score"],
        title={"text": "Weighted exposure score", "font": {"color": "#d8ffe9"}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#d8ffe9"},
            "bar": {"color": "#00ff88"},
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "#00e5ff",
            "steps": [
                {"range": [0, 33], "color": "rgba(16,185,129,.18)"},
                {"range": [33, 66], "color": "rgba(245,158,11,.22)"},
                {"range": [66, 100], "color": "rgba(239,68,68,.25)"},
            ],
        },
    ))
    gauge.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#d8ffe9", height=350)

    radar = go.Figure(go.Scatterpolar(
        r=scores + scores[:1],
        theta=categories + categories[:1],
        fill="toself",
        line_color="#00e5ff",
        fillcolor="rgba(0,229,255,.18)",
    ))
    radar.update_layout(
        polar={"bgcolor": "rgba(0,0,0,0)", "radialaxis": {"range": [0, 100], "gridcolor": "#214d38"}},
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#d8ffe9",
        showlegend=False,
        height=350,
    )
    left, right = st.columns(2)
    left.plotly_chart(gauge, width="stretch")
    right.plotly_chart(radar, width="stretch")

    st.markdown("### Why this score is high")
    if data["reasons"]:
        for reason in data["reasons"]:
            st.markdown(f"- **{reason['category']} ({reason['score']}/100):** {reason['reason']}")
    else:
        st.success("No scored exposure indicators were discovered by enabled modules.")

    st.markdown("### Recommended actions")
    for recommendation in data["recommendations"]:
        st.markdown(f"- {recommendation}")

    with st.expander("Scoring rubric"):
        for category, rubric in data["rubric"].items():
            st.markdown(f"**{category}:** {rubric}")
