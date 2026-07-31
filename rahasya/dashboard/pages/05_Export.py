import os

import streamlit as st

from rahasya.dashboard.state import (
    build_html_report,
    entities_to_dataframe,
    get_current_result,
    get_result_options,
    graph_payload,
    result_from_dict,
    result_json,
)


def load_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


load_css()

st.markdown("<h1 class='neon-text'>DATA EXPORT</h1>", unsafe_allow_html=True)
st.markdown("Export real scan results and intelligence reports.")

options = get_result_options(st)
if not options:
    st.info("No scan results available. Run a scan from New Scan first.")
else:
    labels = list(options.keys())
    default_label = labels[0]
    if st.session_state.current_scan_id:
        for label, scan_id in options.items():
            if scan_id == st.session_state.current_scan_id:
                default_label = label
                break

    selected_label = st.selectbox("Available Scans", labels, index=labels.index(default_label))
    selected_scan_id = options[selected_label]
    result = result_from_dict(st.session_state.scan_results[selected_scan_id])

    st.markdown("### Export Formats")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("""
        <div class="glass-card" style="text-align: center;">
            <h3 style="color: var(--primary-cyan)">HTML Report</h3>
            <p style="font-size: 0.9em; color: var(--text-muted)">Stylized report with summary, risk score, and entities.</p>
        </div>
        """, unsafe_allow_html=True)
        st.download_button(
            "Download HTML",
            data=build_html_report(result),
            file_name=f"rahasya_report_{result.scan_id[:8]}.html",
            mime="text/html",
            use_container_width=True,
        )

    with col2:
        st.markdown("""
        <div class="glass-card" style="text-align: center;">
            <h3 style="color: var(--primary-cyan)">CSV Data</h3>
            <p style="font-size: 0.9em; color: var(--text-muted)">Tabular data of all discovered entities.</p>
        </div>
        """, unsafe_allow_html=True)
        csv_data = entities_to_dataframe(result.entities).to_csv(index=False)
        st.download_button(
            "Download CSV",
            data=csv_data,
            file_name=f"rahasya_entities_{result.scan_id[:8]}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with col3:
        st.markdown("""
        <div class="glass-card" style="text-align: center;">
            <h3 style="color: var(--primary-cyan)">JSON Result</h3>
            <p style="font-size: 0.9em; color: var(--text-muted)">Raw scan result including graph relationships.</p>
        </div>
        """, unsafe_allow_html=True)
        st.download_button(
            "Download JSON",
            data=result_json(result),
            file_name=f"rahasya_scan_{result.scan_id[:8]}.json",
            mime="application/json",
            use_container_width=True,
        )

    st.markdown("### Preview")
    st.json({
        "scan_id": result.scan_id,
        "status": result.status.value,
        "summary": {
            "total_entities": result.stats.total_entities,
            "total_relationships": result.stats.total_relationships,
            "by_type": result.stats.by_type,
        },
        "graph": graph_payload(result),
    })
