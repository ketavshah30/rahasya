"""Rahasya's shared application shell."""
from pathlib import Path
import streamlit as st
from rahasya.dashboard.state import ensure_dashboard_state, get_current_result, cancel_background_scan
from rahasya.dashboard.ui import load_theme, target_name, is_demo, footer

st.set_page_config(page_title="Rahasya · Digital Footprint Intelligence", page_icon="◈",
                   layout="wide", initial_sidebar_state="auto")
load_theme()
ensure_dashboard_state(st)
st.logo(str(Path(__file__).with_name("static") / "logo.svg"), size="large")

pages = {
    "Workspace": [
        st.Page("pages/00_Overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
        st.Page("pages/01_New_Scan.py", title="New assessment", icon=":material/add_circle:"),
        st.Page("pages/08_Demo_Studio.py", title="Demo studio", icon=":material/play_circle:"),
    ],
    "Investigation": [
        st.Page("pages/09_Evidence.py", title="Evidence explorer", icon=":material/manage_search:"),
        st.Page("pages/02_CIA_Web.py", title="Relationship map", icon=":material/hub:"),
        st.Page("pages/03_Timeline.py", title="Timeline", icon=":material/timeline:"),
        st.Page("pages/07_Agents.py", title="AI agents", icon=":material/neurology:"),
    ],
    "Review & share": [
        st.Page("pages/04_Exposure_Report.py", title="Exposure report", icon=":material/shield:"),
        st.Page("pages/06_Network_Log.py", title="Source log", icon=":material/receipt_long:"),
        st.Page("pages/05_Export.py", title="Export", icon=":material/download:"),
    ],
}
navigation = st.navigation(pages)
with st.sidebar:
    st.divider()
    result = get_current_result(st)
    st.caption("CURRENT ASSESSMENT")
    if result:
        st.text(target_name(result))
        st.caption(("SYNTHETIC DEMO" if is_demo(result) else result.status.value) + " · " + result.scan_id[:12])
        if result.status.value in {"RUNNING", "PENDING"} and st.button("Stop assessment", width="stretch"):
            cancel_background_scan(result.scan_id)
            st.rerun()
    else:
        st.caption("Start an assessment or open the guided demo.")
    st.divider()
    st.caption("LOCAL WORKSPACE")
    st.caption("Consent-based research · single-user prototype")
navigation.run()
footer()
