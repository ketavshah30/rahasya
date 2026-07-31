import streamlit as st
import asyncio
import os
from datetime import datetime

# Set must be the first Streamlit command
st.set_page_config(
    page_title="Rahasya | OSINT Platform",
    page_icon="👁️",
    layout="wide",
    initial_sidebar_state="expanded"
)

def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css()

def initialize_session():
    if "current_scan_id" not in st.session_state:
        st.session_state.current_scan_id = None
    if "scans" not in st.session_state:
        st.session_state.scans = {}

initialize_session()

def render_sidebar():
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-logo">
                <h1 class="neon-text">RAHASYA <span style='font-size: 0.5em;'>OSINT</span></h1>
                <p class="matrix-text">Digital Footprint Intelligence</p>
            </div>
            """, 
            unsafe_allow_html=True
        )
        
        st.markdown("---")
        st.markdown("### SYSTEM STATUS")
        
        # Mock connectivity checks
        st.markdown("🟢 **PostgreSQL**: Connected")
        st.markdown("🟢 **Redis**: Connected")
        st.markdown("🟡 **Tor**: Standby")
        
        st.markdown("---")
        if st.session_state.current_scan_id:
            st.info(f"Active Scan: {st.session_state.current_scan_id[:8]}...")
            if st.button("Cancel Scan"):
                st.session_state.current_scan_id = None
                st.warning("Scan Cancelled.")

def render_main():
    st.markdown(
        """
        <div class="hero-section">
            <h1 class="hero-title">Rahasya <span class="accent">Terminal</span></h1>
            <p class="hero-subtitle">Advanced Open-Source Intelligence & Threat Mapping</p>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("""
        <div class="glass-card feature-card">
            <h3>🔍 Deep Search</h3>
            <p>Multi-dimensional entity extraction across social and deep web.</p>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div class="glass-card feature-card">
            <h3>🕸️ Network Graph</h3>
            <p>Interactive visualization of entity relationships.</p>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown("""
        <div class="glass-card feature-card">
            <h3>⏱️ Timeline</h3>
            <p>Temporal analysis of digital footprints.</p>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        st.markdown("""
        <div class="glass-card feature-card">
            <h3>⚠️ Risk Scoring</h3>
            <p>Automated exposure analysis and threat quantification.</p>
        </div>
        """, unsafe_allow_html=True)
        
    st.markdown("### Recent Scans")
    
    if st.session_state.scans:
        for s_id, scan in st.session_state.scans.items():
            st.markdown(f"""
            <div class="glass-card list-card">
                <strong>ID:</strong> {s_id[:8]} | <strong>Status:</strong> {scan.get('status', 'N/A')} | <strong>Targets:</strong> {scan.get('targets', 'N/A')}
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No recent scans found. Go to 'New Scan' to begin.")

def main():
    render_sidebar()
    render_main()

if __name__ == "__main__":
    main()
