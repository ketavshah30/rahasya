import os
from datetime import datetime

import streamlit as st

from rahasya.dashboard.state import (
    ensure_dashboard_state,
    result_to_dict,
    run_scan,
    save_uploaded_photo,
)


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()
ensure_dashboard_state(st)

st.markdown("<h1 class='neon-text'>INITIATE NEW SCAN</h1>", unsafe_allow_html=True)
st.markdown("Enter target identifiers to run a real local Rahasya scan.")

with st.form("new_scan_form"):
    col1, col2 = st.columns(2)

    with col1:
        name = st.text_input("Full Name", placeholder="e.g. John Doe")
        email = st.text_input("Email Address", placeholder="e.g. john@example.com")
        phone = st.text_input("Phone Number", placeholder="e.g. +91 9876543210")
        username = st.text_input("Username", placeholder="e.g. johndoe99")

    with col2:
        location = st.text_input("Location", placeholder="e.g. Ahmedabad, Gujarat, India")
        age_range = st.text_input("Age Range", placeholder="e.g. 20-25")
        photo = st.file_uploader("Target Photo", type=["jpg", "png", "jpeg"])

    with st.expander("Advanced Scan Configuration"):
        conf_col1, conf_col2 = st.columns(2)
        with conf_col1:
            max_depth = st.slider("Max Recursion Depth", 1, 5, 1)
            max_entities = st.slider("Max Entities", 50, 5000, 300)
            timeout = st.slider("Timeout (minutes)", 1, 120, 5)
        with conf_col2:
            st.markdown("**Modules to Enable**")
            mod_social = st.checkbox("Social Media Profiling", value=True)
            mod_breach = st.checkbox("Data Breach Check", value=True)
            mod_darkweb = st.checkbox("Dark Web Mentions", value=False)
            mod_multimedia = st.checkbox("Multimedia Analysis", value=True)
            confidence_threshold = st.slider("Min Confidence Score", 0.0, 1.0, 0.5)

    submit_button = st.form_submit_button("INITIATE SCAN", use_container_width=True)

if submit_button:
    if not any([name, email, phone, username, photo, location]):
        st.error("Please provide at least one target identifier.")
    else:
        photo_path = save_uploaded_photo(photo)
        request_data = {
            "name": name.strip(),
            "email": email.strip(),
            "phone": phone.strip(),
            "username": username.strip(),
            "location": location.strip(),
            "age_range": age_range.strip(),
            "photo_path": photo_path,
            "max_depth": max_depth,
            "max_entities": max_entities,
            "timeout": timeout,
            "confidence_threshold": confidence_threshold,
            "modules": {
                "social": mod_social,
                "breach": mod_breach,
                "darkweb": mod_darkweb,
                "multimedia": mod_multimedia,
            },
        }

        with st.status("Running real Rahasya scan...", expanded=True) as status:
            st.write(f"[{datetime.now().strftime('%H:%M:%S')}] Normalizing target inputs")
            st.write(f"[{datetime.now().strftime('%H:%M:%S')}] Dispatching enabled modules")
            result = run_scan(request_data)
            st.write(f"[{datetime.now().strftime('%H:%M:%S')}] Scan finished with status {result.status.value}")

            st.session_state.current_scan_id = result.scan_id
            st.session_state.scan_results[result.scan_id] = result_to_dict(result)
            st.session_state.scans[result.scan_id] = {
                "status": result.status.value,
                "targets": name or username or email or phone or location or "Unknown Target",
                "progress": 100,
                "entities": result.stats.total_entities,
                "relationships": result.stats.total_relationships,
            }
            status.update(label="Scan complete", state="complete")

        st.success(f"Scan complete. ID: {result.scan_id}")

        metric_cols = st.columns(4)
        metric_cols[0].metric("Status", result.status.value)
        metric_cols[1].metric("Entities", result.stats.total_entities)
        metric_cols[2].metric("Relationships", result.stats.total_relationships)
        metric_cols[3].metric("Depth", result.stats.depth_reached)

        if result.stats.by_type:
            st.markdown("### Entity Breakdown")
            st.json(result.stats.by_type)

        st.info("Open Kundli Graph, Timeline, Exposure Report, or Export from the sidebar to view this scan.")
