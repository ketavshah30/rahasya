
import streamlit as st
from rahasya.dashboard.ui import page_header

from rahasya.dashboard.state import (
    build_html_report,
    entities_to_dataframe,
    get_current_result,
    get_result_options,
    graph_payload,
    render_scan_detail_bar,
    result_json,
    SCAN_STORE,
)
from rahasya.storage.network_audit import NetworkAuditStore, audit_html_report



page_header('Export & share', 'Take your selected assessment, source trail, and evidence with you.')


result = render_scan_detail_bar(st, "export")
if result is None:
    st.info("Start an assessment or load the synthetic case in Demo studio to export a report.")
else:
    st.markdown("### Export Formats")

    col1, col2, col3, col4 = st.columns(4)

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
            width="stretch",
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
            width="stretch",
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
            width="stretch",
        )

    with col4:
        audit_events = NetworkAuditStore(SCAN_STORE.root).load(result.scan_id)
        st.markdown("""
        <div class="glass-card" style="text-align: center;">
            <h3 style="color: var(--primary-cyan)">Network Audit</h3>
            <p style="font-size: 0.9em; color: var(--text-muted)">Sources visited, HTTP outcomes, provider checks, and errors.</p>
        </div>
        """, unsafe_allow_html=True)
        st.download_button(
            "Download Audit HTML",
            data=audit_html_report(result.scan_id, audit_events),
            file_name=f"rahasya_network_audit_{result.scan_id[:8]}.html",
            mime="text/html",
            width="stretch",
            disabled=not audit_events,
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
