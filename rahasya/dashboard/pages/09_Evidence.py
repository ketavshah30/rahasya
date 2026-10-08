"""Searchable evidence, with uncertainty and original source references."""
import streamlit as st
from rahasya.brain.evidence import fact_view
from rahasya.dashboard.state import render_scan_detail_bar, autorefresh_running
from rahasya.dashboard.ui import page_header, section

page_header("Evidence explorer", "Look beyond a match. Inspect the source, attribution, and review status.", "Investigation / Evidence")
result = render_scan_detail_bar(st, "evidence")
autorefresh_running(st, result, "evidence")
if result:
    search_col, type_col, review_col = st.columns([2, 1, 1])
    search = search_col.text_input("Search evidence", placeholder="Identifier, source, or value")
    kinds = type_col.multiselect("Evidence types", sorted({e.entity_type.value for e in result.entities}))
    reviews = review_col.multiselect("Review status", ["supported", "uncertain", "contradicted", "pending", "unreviewed"])
    visible = [e for e in result.entities if
               (not kinds or e.entity_type.value in kinds) and
               (not reviews or e.metadata.get("agent_review", "unreviewed") in reviews) and
               (not search or search.casefold() in (str(e.value) + " " + e.source_module).casefold())]
    columns = st.columns(3)
    columns[0].metric("Visible observations", len(visible))
    columns[1].metric("Source-attested", sum(e.is_ground_truth for e in visible))
    columns[2].metric("Awaiting clarification", sum(e.metadata.get("agent_review") in {"uncertain", "pending"} for e in visible))
    section("Evidence ledger", "Select a record below to inspect its details")
    st.dataframe([{"Value": e.value, "Type": e.entity_type.value, "Source": e.source_module,
                   "Review": e.metadata.get("agent_review", "unreviewed"), "Confidence": e.confidence}
                  for e in visible], hide_index=True, width="stretch")
    if visible:
        choices = {f"{e.value} · {e.id}": e for e in visible}
        item = choices[st.selectbox("Inspect a record", list(choices))]
        a, b = st.columns([1, 1])
        with a.container(border=True):
            st.subheader("What the agent sees")
            st.json(fact_view(item))
        with b.container(border=True):
            st.subheader("Source & context")
            st.write("Provider confidence:", item.confidence)
            st.write("Review:", item.metadata.get("agent_review", "unreviewed"))
            st.caption("Source attestation and model support do not independently prove real-world identity.")
            if item.evidence_urls:
                st.json({"evidence_urls": item.evidence_urls})
            else:
                st.caption("No source URL was attached to this record.")
            with st.expander("Stored metadata"):
                st.json(item.metadata)
    else:
        st.info("No observations match these filters. Clear a filter or try another search.")
