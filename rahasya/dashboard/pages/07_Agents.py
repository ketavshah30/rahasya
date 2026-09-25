"""Persistent agent decisions, observations, and a cited model summary."""

import streamlit as st

from rahasya.dashboard.state import autorefresh_running, get_current_result, render_scan_detail_bar

st.title("Local AI agents")
result = get_current_result(st)
if result is None:
    st.info("Select an investigation or start a scan with local AI agents enabled.")
elif result.brain is None:
    st.info("This investigation used the standard module workflow.")
else:
    render_scan_detail_bar(st, "agents")
    brain = result.brain
    st.caption("Agent assessments are model judgments. Source attribution and confidence come from the discovery modules.")
    left, middle, right = st.columns(3)
    left.metric("Model calls", brain.model_calls)
    middle.metric("Tool calls", brain.actions)
    right.metric("Model tokens", brain.input_tokens + brain.output_tokens)
    if result.error:
        st.error(result.error)
    if brain.stop_reason:
        st.caption("Stopped because: " + brain.stop_reason)
    with st.expander("Model assignments"):
        st.json(brain.models)
    if brain.evidence_batches:
        st.subheader("Evidence filtering")
        st.caption("Full module observations are archived separately. Selection limits model workload; "
                   "deferred observations have not been reviewed and may contain relevant findings.")
        st.dataframe([batch.model_dump() for batch in brain.evidence_batches], hide_index=True, width="stretch")
    st.subheader("Model summary")
    if brain.report:
        st.caption("Partial summary of reviewed observations. Evidence references are checked; claim accuracy still needs review.")
        evidence = {e.id: e for e in result.entities}
        for claim in brain.report.claims:
            st.text(claim.text)
            for entity_id in claim.entity_ids:
                entity = evidence.get(entity_id)
                if entity:
                    with st.expander(f"Evidence: {entity.entity_type.value} / {entity.source_module} / {entity_id[:8]}"):
                        st.json({"value": entity.value, "source": entity.source_module,
                                 "evidence_urls": entity.evidence_urls, "confidence": entity.confidence})
        for limitation in brain.report.limitations:
            st.text(limitation)
        st.caption(f"Summary context: {len(brain.report_entity_ids)} of {len(result.entities)} stored entities.")
    else:
        st.info("No model summary is available yet. Limits or failures may prevent report generation.")
    st.subheader("Evidence assessments")
    if brain.assessments:
        st.dataframe([a.model_dump() for a in brain.assessments], hide_index=True, width="stretch")
    else:
        st.caption("No assessments recorded.")
    st.subheader("Activity")
    st.dataframe([e.model_dump(mode="json") for e in brain.events], hide_index=True, width="stretch")
    st.download_button("Download agent activity", brain.model_dump_json(indent=2),
                       file_name=f"rahasya_agents_{result.scan_id}.json", mime="application/json")
    autorefresh_running(st, result, "agents")
