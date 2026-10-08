
import plotly.graph_objects as go
import streamlit as st
from rahasya.dashboard.ui import page_header

from rahasya.dashboard.state import autorefresh_running, calculate_risk, render_scan_detail_bar


page_header('Exposure report', 'Understand the scoring rubric, inspect the findings, and decide what to address.')


result = render_scan_detail_bar(st, "exposure")
autorefresh_running(st, result, "exposure")
if result is not None:
    st.caption("Heuristic score based on stored observations. It is not a probability of compromise; uncertain associations may contribute. Review attribution before acting.")
    data = calculate_risk(result)
    categories = list(data["categories"])
    scores = [data["categories"][name] for name in categories]

    gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=data["overall_score"],
        title={"text": "Weighted exposure score", "font": {"color": "#dce7f4"}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#dce7f4"},
            "bar": {"color": "#7de2c3"},
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": "#79bce8",
            "steps": [
                {"range": [0, 33], "color": "rgba(16,185,129,.18)"},
                {"range": [33, 66], "color": "rgba(245,158,11,.22)"},
                {"range": [66, 100], "color": "rgba(239,68,68,.25)"},
            ],
        },
    ))
    gauge.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="#dce7f4", height=350)

    radar = go.Figure(go.Scatterpolar(
        r=scores + scores[:1],
        theta=categories + categories[:1],
        fill="toself",
        line_color="#79bce8",
        fillcolor="rgba(0,229,255,.18)",
    ))
    radar.update_layout(
        polar={"bgcolor": "rgba(0,0,0,0)", "radialaxis": {"range": [0, 100], "gridcolor": "#304257"}},
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#dce7f4",
        showlegend=False,
        height=350,
    )
    left, right = st.columns(2)
    left.plotly_chart(gauge, width="stretch")
    right.plotly_chart(radar, width="stretch")

    st.markdown("### What contributes to the score")
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
