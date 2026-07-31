import streamlit as st
import os
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
load_css()

st.markdown("<h1 class='neon-text'>TEMPORAL TIMELINE</h1>", unsafe_allow_html=True)
st.markdown("Chronological view of footprint discovery.")

def get_mock_timeline_data():
    now = datetime.now()
    return pd.DataFrame([
        {"Entity": "John Doe", "Type": "PERSON", "Discovered": now - timedelta(minutes=50), "Source": "Seed", "Confidence": 1.0},
        {"Entity": "johndoe@example.com", "Type": "EMAIL", "Discovered": now - timedelta(minutes=45), "Source": "Clearbit", "Confidence": 0.9},
        {"Entity": "+1234567890", "Type": "PHONE", "Discovered": now - timedelta(minutes=40), "Source": "Truecaller", "Confidence": 0.85},
        {"Entity": "johndoe99", "Type": "USERNAME", "Discovered": now - timedelta(minutes=30), "Source": "Sherlock", "Confidence": 0.8},
        {"Entity": "LinkedIn Profile", "Type": "SOCIAL_PROFILE", "Discovered": now - timedelta(minutes=20), "Source": "Google Dorks", "Confidence": 0.75},
        {"Entity": "Breach: Collection1", "Type": "BREACH_RECORD", "Discovered": now - timedelta(minutes=10), "Source": "HIBP", "Confidence": 0.95},
    ])

df = get_mock_timeline_data()

if df.empty:
    st.info("No timeline data available. Please run a scan first.")
else:
    types = st.multiselect("Filter by Type", df["Type"].unique(), default=list(df["Type"].unique()))
    filtered_df = df[df["Type"].isin(types)]

    fig = px.scatter(
        filtered_df, 
        x="Discovered", 
        y="Type", 
        color="Type", 
        hover_data=["Entity", "Source", "Confidence"],
        title="Entity Discovery Timeline"
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#e2e8f0",
        xaxis_title="Time of Discovery",
        yaxis_title="Entity Type",
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("### Raw Event Log")
    st.dataframe(filtered_df.sort_values(by="Discovered", ascending=False), use_container_width=True)
