import streamlit as st
import os
import plotly.graph_objects as go

def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
load_css()

st.markdown("<h1 class='neon-text'>EXPOSURE REPORT</h1>", unsafe_allow_html=True)
st.markdown("Automated risk and footprint exposure assessment.")

def get_mock_risk_data():
    return {
        "overall_score": 78,
        "categories": {
            "Identity Exposure": 85,
            "Credential Leaks": 92,
            "Dark Web Mentions": 45,
            "Location Tracking": 60
        }
    }

data = get_mock_risk_data()

if not data:
    st.info("No risk data available.")
else:
    # Gauge Chart
    fig = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = data["overall_score"],
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': "Overall Risk Score", 'font': {'color': '#e2e8f0', 'size': 24}},
        gauge = {
            'axis': {'range': [None, 100], 'tickwidth': 1, 'tickcolor': "#e2e8f0"},
            'bar': {'color': "#ff003c"},
            'bgcolor': "rgba(0,0,0,0)",
            'borderwidth': 2,
            'bordercolor': "#00f0ff",
            'steps': [
                {'range': [0, 33], 'color': 'rgba(16, 185, 129, 0.3)'},
                {'range': [33, 66], 'color': 'rgba(245, 158, 11, 0.3)'},
                {'range': [66, 100], 'color': 'rgba(239, 68, 68, 0.3)'}
            ]
        }
    ))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font={'color': "#e2e8f0"})
    st.plotly_chart(fig, use_container_width=True)
    
    st.markdown("### Risk Breakdown")
    col1, col2 = st.columns(2)
    
    cats = list(data["categories"].items())
    
    for i, (cat, score) in enumerate(cats):
        col = col1 if i % 2 == 0 else col2
        with col:
            color = "#ef4444" if score > 70 else "#f59e0b" if score > 30 else "#10b981"
            st.markdown(f"""
            <div class="glass-card">
                <h4 style="margin:0;">{cat}</h4>
                <div style="display: flex; align-items: center; margin-top: 10px;">
                    <div style="flex-grow: 1; background: rgba(255,255,255,0.1); height: 10px; border-radius: 5px;">
                        <div style="width: {score}%; background: {color}; height: 100%; border-radius: 5px;"></div>
                    </div>
                    <span style="margin-left: 15px; font-family: monospace; color: {color}">{score}/100</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
    st.markdown("### Recommendations")
    st.info("💡 **Action Item**: Change passwords for breached emails immediately.")
    st.info("💡 **Action Item**: Review privacy settings on LinkedIn and Twitter.")
