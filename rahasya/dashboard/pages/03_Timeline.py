import os

import plotly.express as px
import streamlit as st

from rahasya.dashboard.state import entities_to_dataframe, get_current_result


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()

st.markdown("<h1 class='neon-text'>TEMPORAL TIMELINE</h1>", unsafe_allow_html=True)
st.markdown("Chronological view of entities discovered in the active scan.")

result = get_current_result(st)
if result is None:
    st.info("No active scan result found. Run a scan from New Scan first.")
else:
    df = entities_to_dataframe(result.entities)

    if df.empty:
        st.info("No timeline data available for this scan.")
    else:
        types = st.multiselect("Filter by Type", sorted(df["Type"].unique()), default=sorted(df["Type"].unique()))
        filtered_df = df[df["Type"].isin(types)]

        fig = px.scatter(
            filtered_df,
            x="Discovered",
            y="Type",
            color="Type",
            hover_data=["Entity", "Source", "Confidence", "Depth"],
            title="Entity Discovery Timeline",
        )
        fig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#e2e8f0",
            xaxis_title="Time of Discovery",
            yaxis_title="Entity Type",
            margin=dict(l=20, r=20, t=40, b=20),
        )

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Raw Event Log")
        st.dataframe(filtered_df.sort_values(by="Discovered", ascending=False), use_container_width=True)
