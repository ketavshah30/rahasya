
import plotly.express as px
import streamlit as st
from rahasya.dashboard.ui import page_header

from rahasya.dashboard.state import autorefresh_running, render_scan_detail_bar, timeline_dataframe


page_header('Evidence timeline', 'Follow profile history, archive snapshots, incidents, and discovery dates.')


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
            font_color="#dce7f4",
            xaxis_title="Observed date (zoom or drag to inspect)",
            margin=dict(l=20, r=20, t=50, b=20),
        )
        st.plotly_chart(figure, width="stretch")
        st.markdown("### Evidence ledger")
        st.dataframe(filtered.drop(columns=["Finish"]), width="stretch")
