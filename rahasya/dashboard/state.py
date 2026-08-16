"""Shared, durable dashboard state and background scan execution."""

from __future__ import annotations

import asyncio
import html
import json
import math
import socket
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import networkx as nx
import pandas as pd

from rahasya.config import settings
from rahasya.core.models import Entity, EntityType, ScanRequest, ScanResult, ScanStatus
from rahasya.core.orchestrator import Orchestrator
from rahasya.storage.scan_store import ScanStore
from rahasya.storage.network_audit import record_audit_event


TERMINAL_STATUSES = {ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED}
SCAN_STORE = ScanStore(settings.storage.scan_dir)
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rahasya-scan")
_FUTURES: Dict[str, Future] = {}
_ACTIVE_ORCHESTRATORS: Dict[str, tuple[Orchestrator, asyncio.AbstractEventLoop]] = {}


RISK_RUBRIC = {
    "Identity Exposure": "Email 8 (max 16), phone 10 (max 20), location 10 (max 20), social profile 3 (max 18), photo/partial identifier 5 (max 15).",
    "Credential Leaks": "Each breach is weighted by severity (low 8, medium 18, high 30, critical 40) plus sensitive leaked fields, capped at 100.",
    "Dark Web Activity": "Mentions use diminishing weights (18/sqrt(rank)) so repeated copies do not inflate the score linearly.",
    "Platform Footprint": "Five points per unique public platform, capped at 50, plus account/linkability signals.",
    "Relationship Exposure": "High-confidence recovery, alternate-account, family, and employment edges carry 8–20 points each.",
}


def ensure_dashboard_state(st) -> None:
    """Hydrate selection from disk; session state stores only UI selection."""
    if "current_scan_id" not in st.session_state:
        st.session_state.current_scan_id = None
    scans = SCAN_STORE.list()
    known_ids = {scan.scan_id for scan in scans}
    if st.session_state.current_scan_id not in known_ids:
        st.session_state.current_scan_id = scans[0].scan_id if scans else None


def save_uploaded_photo(uploaded_file) -> Optional[str]:
    if uploaded_file is None:
        return None
    upload_dir = Path("data/cache/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(uploaded_file.name).suffix.lower() or ".jpg"
    path = upload_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}{suffix}"
    path.write_bytes(uploaded_file.getbuffer())
    return str(path)


def module_category(module_name: str) -> str:
    lowered = module_name.lower()
    if any(part in lowered for part in ("maigret", "sherlock", "whatsmyname", "social", "recovery")):
        return "social"
    if any(part in lowered for part in ("hibp", "intel", "leak", "breach")):
        return "breach"
    if any(part in lowered for part in ("ahmia", "onion", "dread", "hudson")):
        return "darkweb"
    if any(part in lowered for part in ("exif", "imagehash", "wayback", "archive")):
        return "multimedia"
    return "other"


def _filtered_module_getter(orchestrator: Orchestrator, enabled_modules: Dict[str, bool]):
    original_get_modules_for = orchestrator.module_registry.get_modules_for

    def get_modules_for(entity_type: EntityType):
        return [
            module
            for module in original_get_modules_for(entity_type)
            if enabled_modules.get(module_category(module.name), True)
        ]

    return get_modules_for


def _request_from_data(request_data: Dict[str, Any]) -> ScanRequest:
    return ScanRequest(
        name=request_data.get("name") or None,
        email=request_data.get("email") or None,
        phone=request_data.get("phone") or None,
        username=request_data.get("username") or None,
        photo_path=request_data.get("photo_path") or None,
        location=request_data.get("location") or None,
        age_range=request_data.get("age_range") or None,
        max_depth=int(request_data.get("max_depth") or settings.scan.max_depth),
        max_entities=int(request_data.get("max_entities") or settings.scan.max_entities),
    )


async def _run_scan_async(request_data: Dict[str, Any], scan_id: Optional[str] = None) -> ScanResult:
    config = settings.model_copy(deep=True)
    config.scan.max_depth = int(request_data.get("max_depth") or config.scan.max_depth)
    config.scan.max_entities = int(request_data.get("max_entities") or config.scan.max_entities)
    config.scan.max_time_minutes = int(request_data.get("timeout") or config.scan.max_time_minutes)
    config.scan.confidence_threshold = float(
        request_data.get("confidence_threshold")
        if request_data.get("confidence_threshold") is not None
        else config.scan.confidence_threshold
    )

    orchestrator = Orchestrator(config)
    orchestrator.module_registry.get_modules_for = _filtered_module_getter(
        orchestrator, request_data.get("modules", {})
    )
    request = _request_from_data(request_data)
    scan_id = await orchestrator.start_scan(request, scan_id=scan_id)
    for module_class in orchestrator.module_registry.get_all_modules().values():
        module_name = getattr(module_class, "name", module_class.__name__)
        category = module_category(module_name)
        if not request_data.get("modules", {}).get(category, True):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                scan_id=scan_id,
                source_module=module_name,
                root=config.storage.scan_dir,
                message=f"The {category} module category was disabled for this scan",
                skip_reason="category_disabled",
            )
    _ACTIVE_ORCHESTRATORS[scan_id] = (orchestrator, asyncio.get_running_loop())
    deadline_seconds = max(10, config.scan.max_time_minutes * 60 + 5)
    started = asyncio.get_running_loop().time()
    try:
        while True:
            result = orchestrator.get_scan_result(scan_id)
            if result.status in TERMINAL_STATUSES:
                return result
            if asyncio.get_running_loop().time() - started > deadline_seconds:
                await orchestrator.cancel_scan(scan_id)
                return orchestrator.get_scan_result(scan_id)
            await asyncio.sleep(0.25)
    finally:
        _ACTIVE_ORCHESTRATORS.pop(scan_id, None)


def run_scan(request_data: Dict[str, Any]) -> ScanResult:
    """Blocking compatibility API used by CLI/tests."""
    return asyncio.run(_run_scan_async(request_data))


def _background_worker(scan_id: str, request_data: Dict[str, Any]) -> None:
    try:
        asyncio.run(_run_scan_async(request_data, scan_id=scan_id))
    except BaseException as exc:
        SCAN_STORE.mark_failed(scan_id, f"{type(exc).__name__}: {exc}")


def submit_background_scan(request_data: Dict[str, Any]) -> str:
    """Return a scan id immediately and execute via Celery or a local thread."""
    scan_id = str(uuid.uuid4())
    request = _request_from_data(request_data)
    SCAN_STORE.save(ScanResult(scan_id=scan_id, status=ScanStatus.PENDING, request=request))
    SCAN_STORE.save_status(
        scan_id,
        status=ScanStatus.PENDING.value,
        depth=0,
        module=None,
        entity_count=0,
        relationship_count=0,
        max_depth=request.max_depth,
        max_entities=request.max_entities,
    )

    if settings.celery.enabled:
        try:
            from rahasya.celery_app import app

            app.send_task("rahasya.tasks.scan_tasks.execute_scan", args=[scan_id, request_data])
            return scan_id
        except Exception as exc:
            SCAN_STORE.save_status(scan_id, dispatch_warning=f"Celery unavailable: {exc}; using local thread")

    _FUTURES[scan_id] = _EXECUTOR.submit(_background_worker, scan_id, request_data)
    return scan_id


def cancel_background_scan(scan_id: str) -> bool:
    active = _ACTIVE_ORCHESTRATORS.get(scan_id)
    if active:
        orchestrator, loop = active
        asyncio.run_coroutine_threadsafe(orchestrator.cancel_scan(scan_id), loop)
        return True
    result = SCAN_STORE.load(scan_id)
    if result and result.status not in TERMINAL_STATUSES:
        result.status = ScanStatus.CANCELLED
        result.completed_at = datetime.now(timezone.utc)
        SCAN_STORE.save(result)
        SCAN_STORE.save_status(scan_id, status=ScanStatus.CANCELLED.value, module=None)
        return True
    return False


def result_to_dict(result: ScanResult) -> Dict[str, Any]:
    return result.model_dump(mode="json")


def result_from_dict(data: Dict[str, Any]) -> ScanResult:
    return ScanResult.model_validate(data)


def get_current_result(st) -> Optional[ScanResult]:
    ensure_dashboard_state(st)
    return SCAN_STORE.load(st.session_state.current_scan_id) if st.session_state.current_scan_id else None


def get_result_options(st=None) -> Dict[str, str]:
    if st is not None:
        ensure_dashboard_state(st)
    options: Dict[str, str] = {}
    for result in SCAN_STORE.list():
        request = result.request
        target = next(
            (value for value in (
                request.name if request else None,
                request.username if request else None,
                request.email if request else None,
                request.phone if request else None,
            ) if value),
            result.entities[0].value if result.entities else "Unknown target",
        )
        options[f"{result.scan_id[:8]} · {target} · {result.status.value}"] = result.scan_id
    return options


def render_scan_detail_bar(st, key: str) -> Optional[ScanResult]:
    """Render the shared investigation switcher used on every page."""
    ensure_dashboard_state(st)
    options = get_result_options()
    if not options:
        st.info("No persisted investigations yet.")
        return None
    labels = list(options)
    current = st.session_state.current_scan_id
    index = next((i for i, label in enumerate(labels) if options[label] == current), 0)
    left, middle, right = st.columns([5, 2, 2])
    with left:
        label = st.selectbox("Active investigation", labels, index=index, key=f"scan_switch_{key}")
    st.session_state.current_scan_id = options[label]
    result = SCAN_STORE.load(options[label])
    progress = SCAN_STORE.load_status(options[label]) or {}
    middle.metric("Status", progress.get("status", result.status.value if result else "N/A"))
    right.metric("Entities", progress.get("entity_count", result.stats.total_entities if result else 0))
    return result


def autorefresh_running(st, result: Optional[ScanResult], key: str) -> None:
    if result is None or result.status in TERMINAL_STATUSES:
        return
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(
            interval=settings.scan.poll_interval_seconds * 1000,
            key=f"active_scan_refresh_{key}",
        )
    except ImportError:
        st.caption("Install streamlit-autorefresh for automatic live polling.")


def probe_system_status(timeout: float = 0.2) -> Dict[str, str]:
    def probe(host: str, port: int) -> str:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return "Online"
        except OSError:
            return "N/A"

    db = urlparse(settings.db.url.replace("postgresql+asyncpg", "postgresql"))
    redis = urlparse(settings.redis.url)
    return {
        "PostgreSQL": probe(db.hostname or "localhost", db.port or 5432),
        "Redis": probe(redis.hostname or "localhost", redis.port or 6379),
        "Tor": probe("127.0.0.1", settings.tor.socks_port),
    }


def entities_to_dataframe(entities: Iterable[Entity]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Entity": entity.value,
            "Type": entity.entity_type.value,
            "Discovered": entity.discovered_at,
            "Source": entity.source_module,
            "Confidence": entity.confidence,
            "Depth": entity.depth,
        }
        for entity in entities
    ])


def timeline_dataframe(result: ScanResult) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for entity in result.entities:
        metadata = entity.metadata or {}
        event_type = metadata.get("event_type")
        occurred_at = metadata.get("occurred_at") or metadata.get("timestamp")
        if entity.entity_type == EntityType.TIMELINE_EVENT:
            event_type = event_type or getattr(entity, "event_type", "timeline_event")
            occurred_at = occurred_at or getattr(entity, "occurred_at", None)
        elif entity.entity_type in {EntityType.BREACH_RECORD, EntityType.LEAK_RECORD}:
            event_type = event_type or "breach_disclosed"
            occurred_at = occurred_at or getattr(entity, "breach_date", None)
        elif entity.entity_type == EntityType.SOCIAL_PROFILE and getattr(entity, "created_at", None):
            event_type = event_type or "profile_created"
            occurred_at = occurred_at or getattr(entity, "created_at")
        if occurred_at:
            parsed = pd.to_datetime(occurred_at, utc=True, errors="coerce")
            if pd.isna(parsed):
                continue
        else:
            parsed = pd.to_datetime(entity.discovered_at, utc=True)
            event_type = event_type or "discovered"
        identity = metadata.get("subject") or metadata.get("platform") or entity.value
        rows.append({
            "Start": parsed,
            "Finish": parsed + timedelta(hours=12),
            "Identity": str(identity),
            "Event": str(event_type).replace("_", " ").title(),
            "Source": entity.source_module,
            "URL": metadata.get("url") or getattr(entity, "source_url", None),
            "Confidence": entity.confidence,
            "Type": entity.entity_type.value,
        })
    return pd.DataFrame(rows)


def graph_payload(result: ScanResult) -> Dict[str, Any]:
    clusters = person_clusters(result)
    return {
        "scan_id": result.scan_id,
        "nodes": [
            {
                "id": entity.id,
                "label": entity.value,
                "type": entity.entity_type.value,
                "source": entity.source_module,
                "confidence": entity.confidence,
                "cluster": clusters.get(entity.id),
                "metadata": entity.metadata,
            }
            for entity in result.entities
        ],
        "edges": [
            {
                "source": rel.source_id,
                "target": rel.target_id,
                "type": rel.relationship_type.value,
                "confidence": rel.confidence,
                "source_module": rel.source_module,
                "metadata": rel.metadata,
            }
            for rel in result.relationships
        ],
    }


def person_clusters(result: ScanResult) -> Dict[str, str]:
    graph = nx.Graph()
    graph.add_nodes_from(entity.id for entity in result.entities)
    cluster_edges = {
        "SAME_AS", "LIKELY_SAME", "ALT_ACCOUNT_OF", "SHARES_RECOVERY",
        "HAS_EMAIL", "HAS_PHONE", "USES_USERNAME", "HAS_PROFILE",
    }
    graph.add_edges_from(
        (rel.source_id, rel.target_id)
        for rel in result.relationships
        if rel.relationship_type.value in cluster_edges and rel.confidence >= 0.6
    )
    mapping: Dict[str, str] = {}
    for index, members in enumerate(nx.connected_components(graph), start=1):
        if len(members) > 1:
            mapping.update({member: f"PC-{index:03d}" for member in members})
    return mapping


def shortest_path(result: ScanResult, source_id: str, target_id: str) -> List[str]:
    graph = nx.Graph()
    graph.add_nodes_from(entity.id for entity in result.entities)
    graph.add_edges_from((rel.source_id, rel.target_id) for rel in result.relationships)
    try:
        return nx.shortest_path(graph, source_id, target_id)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []


def calculate_risk(result: ScanResult) -> Dict[str, Any]:
    entities = result.entities
    counts = result.stats.by_type
    social = [e for e in entities if e.entity_type == EntityType.SOCIAL_PROFILE]
    platforms = {str((e.metadata or {}).get("platform") or getattr(e, "platform", "unknown")).lower() for e in social}

    identity = min(100, (
        min(counts.get("email", 0) * 8, 16)
        + min(counts.get("phone", 0) * 10, 20)
        + min(counts.get("location", 0) * 10, 20)
        + min(len(social) * 3, 18)
        + min((counts.get("photo", 0) + counts.get("partial_email", 0) + counts.get("partial_phone", 0)) * 5, 15)
    ))

    severity_weight = {"low": 8, "medium": 18, "high": 30, "critical": 40}
    sensitive_fields = {"password", "passwords", "credit card", "ssn", "phone", "address", "dob"}
    credential = 0
    breach_entities = [e for e in entities if e.entity_type in {EntityType.BREACH_RECORD, EntityType.LEAK_RECORD}]
    for breach in breach_entities:
        severity = str(getattr(breach, "severity", None) or (breach.metadata or {}).get("severity", "medium")).lower()
        credential += severity_weight.get(severity, 18)
        fields = {str(value).lower() for value in getattr(breach, "data_types_leaked", [])}
        credential += min(20, len(fields & sensitive_fields) * 5)
    credential = min(100, credential)

    dark_count = counts.get("dark_web_mention", 0)
    dark = min(100, round(sum(18 / math.sqrt(rank) for rank in range(1, dark_count + 1))))
    footprint = min(100, len(platforms) * 5 + min(30, len(social) * 2))
    edge_weights = {
        "SHARES_RECOVERY": 20, "ALT_ACCOUNT_OF": 16, "PARENT_OF": 12,
        "SIBLING_OF": 10, "SPOUSE_OF": 10, "WORKS_WITH": 8, "EMPLOYED_AT": 8,
    }
    relationship = min(100, round(sum(
        edge_weights.get(rel.relationship_type.value, 0) * rel.confidence
        for rel in result.relationships
    )))
    categories = {
        "Identity Exposure": identity,
        "Credential Leaks": credential,
        "Dark Web Activity": dark,
        "Platform Footprint": footprint,
        "Relationship Exposure": relationship,
    }
    weights = {
        "Identity Exposure": 0.20,
        "Credential Leaks": 0.30,
        "Dark Web Activity": 0.25,
        "Platform Footprint": 0.10,
        "Relationship Exposure": 0.15,
    }
    overall = round(sum(categories[name] * weights[name] for name in categories))
    reasons = [
        {"category": name, "score": score, "reason": RISK_RUBRIC[name]}
        for name, score in sorted(categories.items(), key=lambda item: item[1], reverse=True)
        if score > 0
    ][:5]
    recommendations: List[str] = []
    if credential:
        named_breaches = sorted({
            str(getattr(entity, "breach_name", None) or entity.value)
            for entity in breach_entities
        })[:5]
        recommendations.append(
            f"Rotate passwords on {', '.join(named_breaches)}, avoid reuse, and enable phishing-resistant MFA."
        )
    if dark:
        recommendations.append("Monitor exposed identifiers and review each dark-web source before taking action.")
    if footprint >= 20:
        named_platforms = ", ".join(sorted(platforms)[:5])
        recommendations.append(
            f"Audit privacy settings on {named_platforms}; delete dormant profiles that are no longer needed."
        )
    if relationship:
        recommendations.append("Remove public recovery, family, and employer clues that make account correlation easy.")
    if identity >= 20:
        recommendations.append("Use separate contact aliases for public profiles and sensitive accounts.")
    if not recommendations:
        recommendations.append("Maintain unique passwords, MFA, and periodic breach monitoring.")
    return {
        "overall_score": overall,
        "categories": categories,
        "reasons": reasons,
        "recommendations": recommendations,
        "rubric": RISK_RUBRIC,
    }


def build_html_report(result: ScanResult) -> str:
    risk = calculate_risk(result)
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(entity.entity_type.value)}</td>"
        f"<td>{html.escape(str(entity.value))}</td>"
        f"<td>{html.escape(entity.source_module)}</td>"
        f"<td>{entity.confidence:.2f}</td></tr>"
        for entity in result.entities
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Rahasya Report {html.escape(result.scan_id)}</title><style>
body{{font-family:monospace;background:#07110d;color:#d8ffe9}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #1b9b61;padding:8px;text-align:left}}th{{background:#0b2017}}</style></head>
<body><h1>Rahasya Investigation Report</h1><p><strong>Scan ID:</strong> {html.escape(result.scan_id)}</p>
<p><strong>Status:</strong> {result.status.value}</p><p><strong>Entities:</strong> {result.stats.total_entities}</p>
<p><strong>Relationships:</strong> {result.stats.total_relationships}</p><p><strong>Risk Score:</strong> {risk['overall_score']}/100</p>
<h2>Entities</h2><table><thead><tr><th>Type</th><th>Value</th><th>Source</th><th>Confidence</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


def result_json(result: ScanResult) -> str:
    return json.dumps(result_to_dict(result), indent=2)
