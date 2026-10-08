import streamlit as st
from rahasya.dashboard import state
from rahasya.dashboard.demo import load_demo
from rahasya.dashboard.ui import load_theme, hero, section, demo_notice, evidence_rows, target_name, is_demo

load_theme()
st.markdown('<div class="topline"><span>WORKSPACE / OVERVIEW</span>'
            '<span class="pill">◈ Consent-based research</span></div>', unsafe_allow_html=True)
hero()
left, middle, right = st.columns([1.1, 1.1, 2.8])
if left.button("＋ New assessment", type="primary", width="stretch"):
    st.switch_page("pages/01_New_Scan.py")
if middle.button("▷ Explore the demo", width="stretch"):
    demo = load_demo(state.SCAN_STORE)
    st.session_state.current_scan_id = demo.scan_id
    st.switch_page("pages/08_Demo_Studio.py")
right.caption("A guided, synthetic case. No searches or model setup required.")

result = state.get_current_result(st)
scans = state.SCAN_STORE.list()
section("Workspace at a glance", "Counts from your saved assessments")
metrics = st.columns(4)
metrics[0].metric("Saved assessments", len(scans))
metrics[1].metric("Active investigations", sum(s.status.value in {"PENDING", "RUNNING"} for s in scans))
metrics[2].metric("Selected evidence", len(result.entities) if result else 0)
metrics[3].metric("Connections", len(result.relationships) if result else 0)
section("From scattered signals to a clearer picture", "Your investigation workflow")
cards = [
    ("01 / DISCOVER", "Start with an identifier", "Select sources and set a bounded investigation scope.", "New assessment", "pages/01_New_Scan.py"),
    ("02 / CONNECT", "Follow the evidence", "Inspect source-backed connections and unresolved associations.", "Relationship map", "pages/02_CIA_Web.py"),
    ("03 / UNDERSTAND", "See the decisions", "Explore the specialist roles, evidence filtering, and review trail.", "AI agents", "pages/07_Agents.py"),
    ("04 / ACT", "Review your exposure", "Read the scoring rubric and turn findings into practical actions.", "Exposure report", "pages/04_Exposure_Report.py"),
]
for col, (step, title, copy, label, page) in zip(st.columns(4), cards):
    with col.container(border=True):
        st.markdown(f'<div class="feature-icon">{step}</div>', unsafe_allow_html=True)
        st.subheader(title)
        st.markdown(f'<div class="feature-copy">{copy}</div>', unsafe_allow_html=True)
        if st.button(label + " →", key="overview_" + step, width="stretch"):
            st.switch_page(page)
if result:
    section("Inside the selected assessment", target_name(result))
    demo_notice(result)
    a, b = st.columns([1.6, 1])
    with a.container(border=True):
        st.subheader("Evidence preview")
        evidence_rows(result.entities[:5])
        if st.button("Explore all evidence →"):
            st.switch_page("pages/09_Evidence.py")
    with b.container(border=True):
        st.subheader("An accountable workflow")
        st.markdown('<div class="workflow-line">Collect → Normalize → <strong>Filter</strong> → Review → Explain</div>', unsafe_allow_html=True)
        st.write("Every conclusion should lead back to a source. Uncertain matches remain visible for human review.")
        st.caption("Model judgments are advisory. Missing results do not establish absence of exposure.")
        if st.button("Open source log →"):
            st.switch_page("pages/06_Network_Log.py")
section("Assessment library", "Choose a saved investigation to continue")
if not scans:
    st.info("Your workspace is ready. Start a new assessment, or use the synthetic demo to explore every view.")
else:
    options = {f"{target_name(s)} · {s.scan_id[:12]}": s.scan_id for s in scans}
    active = next((i for i, value in enumerate(options.values()) if value == st.session_state.current_scan_id), 0)
    choose, open_col = st.columns([4, 1])
    choice = choose.selectbox("Saved assessments", list(options), index=active, label_visibility="collapsed")
    if open_col.button("Open assessment", width="stretch"):
        st.session_state.current_scan_id = options[choice]
        st.rerun()
    st.dataframe([{"Assessment": target_name(s), "Data": "Synthetic" if is_demo(s) else "Investigation",
                   "Status": s.status.value, "Evidence": len(s.entities), "Connections": len(s.relationships)}
                  for s in scans], hide_index=True, width="stretch")
