import streamlit as st
from rahasya.dashboard.ui import page_header
from rahasya.config import settings

from rahasya.dashboard.state import (
    SCAN_STORE,
    autorefresh_running,
    ensure_dashboard_state,
    get_current_result,
    render_scan_detail_bar,
    save_uploaded_photo,
    submit_background_scan,
)


ensure_dashboard_state(st)

page_header('New assessment', 'Begin with an identifier, choose your sources, and define the investigation scope.')


st.caption("Use identifiers you own or have permission to assess. For the presentation, Demo studio uses fictional data without external lookups.")

with st.form("new_scan_form"):
    agentic = st.checkbox(
        "Use local AI agents", value=settings.brain.enabled,
        help="Requires Ollama and downloaded models. Agents select tools and assess observations locally.",
    )
    col1, col2 = st.columns(2)

    with col1:
        name = st.text_input("Full Name", placeholder="Name within the agreed scope")
        email = st.text_input("Email Address", placeholder="you@example.org")
        phone = st.text_input("Phone Number", placeholder="e.g. +91 9876543210")
        username = st.text_input("Username", placeholder="Your public handle")

    with col2:
        location = st.text_input("Location", placeholder="e.g. Ahmedabad, Gujarat, India")
        age_range = st.text_input("Age Range", placeholder="e.g. 20-25")
        photo = st.file_uploader("Reference photo", type=["jpg", "png", "jpeg"])

    with st.expander("Scope, sources & limits"):
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

    submit_button = st.form_submit_button("Start assessment", width="stretch", type="primary")

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
            "agentic": agentic,
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
        st.info("Open the Relationship map, Timeline, AI agents, or Exposure report. The assessment continues in the background.")

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
