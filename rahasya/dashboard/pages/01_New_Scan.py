import os
import streamlit as st

from rahasya.dashboard.state import (
    SCAN_STORE,
    autorefresh_running,
    ensure_dashboard_state,
    get_current_result,
    render_scan_detail_bar,
    save_uploaded_photo,
    submit_background_scan,
)


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()
ensure_dashboard_state(st)

st.markdown("<h1 class='neon-text'>INITIATE NEW SCAN</h1>", unsafe_allow_html=True)
st.markdown("Enter target identifiers. The investigation continues in the background across page changes and refreshes.")

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

    submit_button = st.form_submit_button("INITIATE SCAN", width="stretch")

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

        scan_id = submit_background_scan(request_data)
        st.session_state.current_scan_id = scan_id
        st.success(f"Investigation dispatched. ID: {scan_id}")
        st.info("You can now open CIA Web, Timeline, Exposure Report, or Export; this scan will keep running.")

active = get_current_result(st)
if active is not None:
    st.markdown("### Active investigation")
    render_scan_detail_bar(st, "new_scan")
    status = SCAN_STORE.load_status(active.scan_id) or {}
    max_depth_value = max(1, int(status.get("max_depth") or 1))
    depth_value = min(max_depth_value, int(status.get("depth") or 0))
    entity_limit = max(1, int(status.get("max_entities") or 1))
    entity_count = int(status.get("entity_count") or 0)
    progress_value = max(depth_value / max_depth_value, min(0.99, entity_count / entity_limit))
    if active.status.value in {"COMPLETED", "FAILED", "CANCELLED"}:
        progress_value = 1.0
    st.progress(progress_value, text=f"Depth {depth_value}/{max_depth_value} · {entity_count} entities")
    if status.get("module"):
        st.code(f"MODULES IN FLIGHT :: {status['module']}")
    autorefresh_running(st, active, "new_scan")
