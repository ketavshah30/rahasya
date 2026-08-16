"""Live source, error, and network audit for each investigation."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from rahasya.config import settings
from rahasya.dashboard.state import autorefresh_running, render_scan_detail_bar
from rahasya.storage.network_audit import (
    NetworkAuditStore,
    audit_csv,
    audit_html_report,
    audit_json,
    summarize_events,
)


FAILURE_OUTCOMES = {"failed", "error", "http_error", "rate_limited", "timeout", "cancelled"}
DISPLAY_COLUMNS = [
    "timestamp", "source_module", "event_type", "outcome", "method", "status_code",
    "host", "url", "duration_ms", "attempt", "site", "provider_status", "error", "message",
]


def load_css() -> None:
    css_path = os.path.join(os.path.dirname(__file__), "..", "static", "style.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as stream:
            st.markdown(f"<style>{stream.read()}</style>", unsafe_allow_html=True)


def dataframe(events: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(events)
    for column in DISPLAY_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    return frame


load_css()
st.markdown("<h1 class='neon-text'>NETWORK & SOURCE LOG</h1>", unsafe_allow_html=True)
st.markdown(
    "Every source attempt, provider check, module state, HTTP response, timeout, and parse error "
    "recorded for the selected scan. API credentials in URL query strings are redacted."
)

result = render_scan_detail_bar(st, "network_log")
autorefresh_running(st, result, "network_log")

if result is not None:
    store = NetworkAuditStore(settings.storage.scan_dir)
    events = store.load(result.scan_id)
    summary = summarize_events(events)
    provider_checks = [event for event in events if event.get("event_type") == "provider_site_check"]

    metric_columns = st.columns(6)
    metric_columns[0].metric("All events", summary["total_events"])
    metric_columns[1].metric("HTTP attempts", summary["network_attempts"])
    metric_columns[2].metric("HTTP success", summary["successful_requests"])
    metric_columns[3].metric("HTTP failed", summary["failed_requests"])
    metric_columns[4].metric("Provider checks", len(provider_checks))
    metric_columns[5].metric("Unique hosts", summary["unique_hosts"])

    if not events:
        st.info(
            "No audit events exist for this scan. Audit capture applies to newly started scans; "
            "older saved scans cannot reconstruct past network traffic."
        )
    else:
        raw = dataframe(events)
        st.markdown("### Filters")
        filter_columns = st.columns([2, 2, 2, 2, 3])
        with filter_columns[0]:
            selected_modules = st.multiselect(
                "Source modules", sorted(raw["source_module"].dropna().astype(str).unique())
            )
        with filter_columns[1]:
            selected_events = st.multiselect(
                "Event types", sorted(raw["event_type"].dropna().astype(str).unique())
            )
        with filter_columns[2]:
            selected_outcomes = st.multiselect(
                "Outcomes", sorted(raw["outcome"].dropna().astype(str).unique())
            )
        with filter_columns[3]:
            failures_only = st.toggle("Failures only")
        with filter_columns[4]:
            search = st.text_input("Search URL, host, site, or error")

        filtered = raw.copy()
        if selected_modules:
            filtered = filtered[filtered["source_module"].isin(selected_modules)]
        if selected_events:
            filtered = filtered[filtered["event_type"].isin(selected_events)]
        if selected_outcomes:
            filtered = filtered[filtered["outcome"].isin(selected_outcomes)]
        if failures_only:
            filtered = filtered[filtered["outcome"].isin(FAILURE_OUTCOMES)]
        if search:
            searchable = filtered[["url", "host", "site", "error", "message"]].fillna("").astype(str)
            filtered = filtered[searchable.apply(
                lambda row: row.str.contains(search, case=False, regex=False).any(), axis=1
            )]

        st.markdown(f"### Chronological event log ({len(filtered)} shown)")
        st.dataframe(
            filtered[DISPLAY_COLUMNS].iloc[::-1],
            width="stretch",
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("URL", display_text=r"https?://([^/]+).*"),
                "duration_ms": st.column_config.NumberColumn("Duration (ms)", format="%.2f"),
            },
        )

        sites = raw[raw["url"].notna() & raw["url"].astype(str).ne("")].copy()
        if not sites.empty:
            st.markdown("### Sites, portals, and web links checked")
            sites["source_or_portal"] = sites["site"].fillna(sites["host"]).fillna("unknown")
            sites["is_failure"] = sites["outcome"].isin(FAILURE_OUTCOMES)
            site_summary = sites.groupby(
                ["source_module", "source_or_portal"], dropna=False
            ).agg(
                checks=("event_type", "size"),
                failures=("is_failure", "sum"),
                last_outcome=("outcome", "last"),
                last_status=("status_code", "last"),
                web_link=("url", "last"),
            ).reset_index().sort_values(["failures", "checks"], ascending=False)
            st.dataframe(
                site_summary,
                width="stretch",
                hide_index=True,
                column_config={"web_link": st.column_config.LinkColumn("Last web link")},
            )

        network = raw[raw["event_type"] == "network_request"].copy()
        if not network.empty:
            st.markdown("### Host summary")
            network["is_failure"] = network["outcome"].isin(FAILURE_OUTCOMES)
            network["is_success"] = network["outcome"].eq("success")
            host_summary = network.groupby("host", dropna=False).agg(
                attempts=("event_type", "size"),
                successes=("is_success", "sum"),
                failures=("is_failure", "sum"),
                average_ms=("duration_ms", "mean"),
                last_status=("status_code", "last"),
                last_outcome=("outcome", "last"),
            ).reset_index().sort_values(["failures", "attempts"], ascending=False)
            st.dataframe(host_summary, width="stretch", hide_index=True)

        st.markdown("### Source/module summary")
        source_summary = raw.groupby("source_module", dropna=False).agg(
            events=("event_type", "size"),
            last_outcome=("outcome", "last"),
            last_event=("event_type", "last"),
        ).reset_index()
        source_summary["network_attempts"] = source_summary["source_module"].map(
            network.groupby("source_module").size() if not network.empty else {}
        ).fillna(0).astype(int)
        source_summary["provider_checks"] = source_summary["source_module"].map(
            raw[raw["event_type"] == "provider_site_check"].groupby("source_module").size()
        ).fillna(0).astype(int)
        st.dataframe(source_summary, width="stretch", hide_index=True)

        errors = raw[raw["outcome"].isin(FAILURE_OUTCOMES)]
        st.markdown(f"### Errors, timeouts, and rate limits ({len(errors)})")
        if errors.empty:
            st.success("No failed network or module events were recorded.")
        else:
            st.dataframe(errors[DISPLAY_COLUMNS].iloc[::-1], width="stretch", hide_index=True)

        st.markdown("### Download complete log report")
        downloads = st.columns(3)
        downloads[0].download_button(
            "Download HTML report",
            data=audit_html_report(result.scan_id, events),
            file_name=f"rahasya_network_audit_{result.scan_id[:8]}.html",
            mime="text/html",
            width="stretch",
        )
        downloads[1].download_button(
            "Download CSV log",
            data=audit_csv(events),
            file_name=f"rahasya_network_audit_{result.scan_id[:8]}.csv",
            mime="text/csv",
            width="stretch",
        )
        downloads[2].download_button(
            "Download JSON log",
            data=audit_json(events),
            file_name=f"rahasya_network_audit_{result.scan_id[:8]}.json",
            mime="application/json",
            width="stretch",
        )
