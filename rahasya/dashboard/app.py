import os

import streamlit as st

from rahasya.dashboard.state import (
    SCAN_STORE,
    cancel_background_scan,
    ensure_dashboard_state,
    get_result_options,
    probe_system_status,
    render_scan_detail_bar,
)


st.set_page_config(
    page_title="Rahasya | Investigation Terminal",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as stream:
            st.markdown(f"<style>{stream.read()}</style>", unsafe_allow_html=True)


load_css()
ensure_dashboard_state(st)


def render_sidebar():
    with st.sidebar:
        st.markdown("<div class='sidebar-logo'><h1 class='neon-text'>RAHASYA // OSINT</h1><p class='matrix-text'>CORRELATION TERMINAL</p></div>", unsafe_allow_html=True)
        st.markdown("---")
        st.markdown("### SYSTEM STATUS")
        for service, status in probe_system_status().items():
            icon = "🟢" if status == "Online" else "⚪"
            st.markdown(f"{icon} **{service}**: {status}")

        st.markdown("---")
        st.markdown("### ACTIVE INVESTIGATION")
        if st.session_state.current_scan_id:
            scan_id = st.session_state.current_scan_id
            result = SCAN_STORE.load(scan_id)
            progress = SCAN_STORE.load_status(scan_id) or {}
            request = result.request if result else None
            target = next((item for item in (
                request.name if request else None,
                request.username if request else None,
                request.email if request else None,
                request.phone if request else None,
            ) if item), "Unknown target")
            st.code(f"ID       {scan_id[:12]}\nTARGET   {target}\nSTATUS   {progress.get('status', 'N/A')}\nENTITIES {progress.get('entity_count', 0)}")
            if progress.get("status") in {"PENDING", "RUNNING"} and st.button("Cancel active scan", width="stretch"):
                cancel_background_scan(scan_id)
                st.rerun()
        else:
            st.caption("No investigation selected")

        st.markdown("---")
        st.markdown("### RECENT SCANS")
        for label, scan_id in list(get_result_options().items())[:8]:
            if st.button(label, key=f"recent_{scan_id}", width="stretch"):
                st.session_state.current_scan_id = scan_id
                st.rerun()
        st.markdown("---")
        st.caption("OPERATOR // local-user · single-user mode")


render_sidebar()
st.markdown("""
<div class="hero-section">
  <h1 class="hero-title">Rahasya <span class="accent">Terminal</span></h1>
  <p class="hero-subtitle">Persistent open-source intelligence, timeline analysis, and CIA-style correlation web.</p>
</div>
""", unsafe_allow_html=True)

result = render_scan_detail_bar(st, "home") if get_result_options() else None

features = st.columns(4)
cards = [
    ("DEEP SEARCH", "Recursive entity discovery across enabled public sources."),
    ("CIA WEB", "Filterable identity clusters, recovery links, and explainable paths."),
    ("TIMELINE", "First-seen profile, archive, breach, and mention evidence."),
    ("RISK MODEL", "Documented, severity-weighted exposure scoring and actions."),
]
for column, (title, copy) in zip(features, cards):
    column.markdown(f"<div class='glass-card feature-card'><h3>{title}</h3><p>{copy}</p></div>", unsafe_allow_html=True)

st.markdown("### Recent investigation ledger")
scans = SCAN_STORE.list()
if not scans:
    st.info("No persisted scans found. Open New Scan to begin.")
else:
    st.dataframe([
        {
            "scan_id": scan.scan_id,
            "status": scan.status.value,
            "target": next((value for value in (
                scan.request.name if scan.request else None,
                scan.request.username if scan.request else None,
                scan.request.email if scan.request else None,
                scan.request.phone if scan.request else None,
            ) if value), "Unknown"),
            "entities": scan.stats.total_entities,
            "relationships": scan.stats.total_relationships,
            "started": scan.started_at,
        }
        for scan in scans
    ], width="stretch")
