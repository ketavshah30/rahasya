import os

import plotly.express as px
import streamlit as st

from rahasya.dashboard.state import autorefresh_running, render_scan_detail_bar, timeline_dataframe


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as stream:
            st.markdown(f"<style>{stream.read()}</style>", unsafe_allow_html=True)


load_css()
st.markdown("<h1 class='neon-text'>IDENTITY PRESENCE TIMELINE</h1>", unsafe_allow_html=True)
st.markdown("Profile creation, archive snapshots, breaches, dark-web sightings, and discovery events.")

result = render_scan_detail_bar(st, "timeline")
autorefresh_running(st, result, "timeline")
if result is not None:
    frame = timeline_dataframe(result)
    if frame.empty:
        st.info("No temporal evidence is available for this investigation yet.")
    else:
        event_types = sorted(frame["Event"].unique())
        selected = st.multiselect("Event types", event_types, default=event_types)
        filtered = frame[frame["Event"].isin(selected)].sort_values("Start")
        figure = px.timeline(
            filtered,
            x_start="Start",
            x_end="Finish",
            y="Identity",
            color="Event",
            hover_data=["Source", "URL", "Confidence", "Type"],
            title="Online identity evidence by first-seen date",
        )
        figure.update_yaxes(autorange="reversed")
        figure.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#d8ffe9",
            xaxis_title="Observed date (zoom or drag to inspect)",
            margin=dict(l=20, r=20, t=50, b=20),
        )
        st.plotly_chart(figure, width="stretch")
        st.markdown("### Evidence ledger")
        st.dataframe(filtered.drop(columns=["Finish"]), width="stretch")
