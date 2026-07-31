import streamlit as st
import asyncio
import time
import uuid
import os
from datetime import datetime

# Page config is inherited from main app.py, but we can load CSS
def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
load_css()

st.markdown("<h1 class='neon-text'>INITIATE NEW SCAN</h1>", unsafe_allow_html=True)
st.markdown("Enter target identifiers to begin the intelligence gathering process.")

# Mocking orchestrator/task call
def dispatch_scan_task(request_data):
    # In a real app: from rahasya.tasks.scan_tasks import run_scan; run_scan.delay(request_data)
    scan_id = str(uuid.uuid4())
    st.session_state.current_scan_id = scan_id
    st.session_state.scans[scan_id] = {
        "status": "RUNNING",
        "targets": request_data.get("name", "") or request_data.get("username", "") or "Unknown Target",
        "progress": 0
    }
    return scan_id

with st.form("new_scan_form"):
    col1, col2 = st.columns(2)
    
    with col1:
        name = st.text_input("👤 Full Name", placeholder="e.g. John Doe")
        email = st.text_input("✉️ Email Address", placeholder="e.g. john@example.com")
        phone = st.text_input("📞 Phone Number", placeholder="e.g. +1234567890")
        username = st.text_input("👾 Username", placeholder="e.g. johndoe99")
        
    with col2:
        location = st.text_input("📍 Location", placeholder="e.g. New York, USA")
        age_range = st.text_input("📅 Age Range", placeholder="e.g. 25-35")
        photo = st.file_uploader("📷 Target Photo", type=["jpg", "png", "jpeg"])
        
    with st.expander("⚙️ Advanced Scan Configuration"):
        conf_col1, conf_col2 = st.columns(2)
        with conf_col1:
            max_depth = st.slider("Max Recursion Depth", 1, 5, 3)
            max_entities = st.slider("Max Entities", 100, 5000, 1000)
            timeout = st.slider("Timeout (minutes)", 5, 120, 60)
        with conf_col2:
            st.markdown("**Modules to Enable**")
            mod_social = st.checkbox("Social Media Profiling", value=True)
            mod_breach = st.checkbox("Data Breach Check", value=True)
            mod_darkweb = st.checkbox("Dark Web Mentions", value=False)
            mod_multimedia = st.checkbox("Multimedia Analysis", value=True)
            confidence_threshold = st.slider("Min Confidence Score", 0.0, 1.0, 0.5)

    submit_button = st.form_submit_button("INITIATE SCAN 🚀", use_container_width=True)

if submit_button:
    if not any([name, email, phone, username, photo]):
        st.error("Please provide at least one target identifier.")
    else:
        req_data = {
            "name": name, "email": email, "phone": phone, 
            "username": username, "location": location,
            "max_depth": max_depth, "max_entities": max_entities,
            "modules": {
                "social": mod_social, "breach": mod_breach,
                "darkweb": mod_darkweb, "multimedia": mod_multimedia
            }
        }
        scan_id = dispatch_scan_task(req_data)
        st.success(f"Scan initiated! ID: {scan_id}")
        
        # Live progress UI mock
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_feed = st.empty()
        
        # Simulate progress
        for i in range(1, 101, 10):
            time.sleep(0.3)
            progress_bar.progress(i)
            status_text.markdown(f"**Progress:** {i}% - Extracting entities...")
            log_feed.code(f"[{datetime.now().strftime('%H:%M:%S')}] Found new entity link at depth {max_depth}...")
            
        progress_bar.progress(100)
        status_text.markdown("✅ **Scan Complete**")
        st.session_state.scans[scan_id]["status"] = "COMPLETED"
        st.session_state.scans[scan_id]["progress"] = 100
        st.balloons()
