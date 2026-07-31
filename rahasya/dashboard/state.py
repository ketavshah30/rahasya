import asyncio
import copy
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from rahasya.config import settings
from rahasya.core.models import Entity, EntityType, Relationship, ScanRequest, ScanResult, ScanStatus
from rahasya.core.orchestrator import Orchestrator


TERMINAL_STATUSES = {ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED}


def ensure_dashboard_state(st) -> None:
    if "current_scan_id" not in st.session_state:
        st.session_state.current_scan_id = None
    if "scans" not in st.session_state:
        st.session_state.scans = {}
    if "scan_results" not in st.session_state:
        st.session_state.scan_results = {}


def save_uploaded_photo(uploaded_file) -> Optional[str]:
    if uploaded_file is None:
        return None

    upload_dir = Path("data/cache/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(uploaded_file.name).suffix.lower() or ".jpg"
    path = upload_dir / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}{suffix}"
    path.write_bytes(uploaded_file.getbuffer())
    return str(path)


def module_category(module_name: str) -> str:
    lowered = module_name.lower()
    if any(part in lowered for part in ("maigret", "sherlock", "whatsmyname")):
        return "social"
    if any(part in lowered for part in ("hibp", "intel", "leak")):
        return "breach"
    if any(part in lowered for part in ("ahmia", "onion")):
        return "darkweb"
    if any(part in lowered for part in ("exif", "imagehash", "wayback", "archive")):
        return "multimedia"
    return "other"


def _filtered_module_getter(orchestrator: Orchestrator, enabled_modules: Dict[str, bool]):
    original_get_modules_for = orchestrator.module_registry.get_modules_for

    def get_modules_for(entity_type: EntityType):
        modules = original_get_modules_for(entity_type)
        return [
            module
            for module in modules
            if enabled_modules.get(module_category(module.name), True)
        ]

    return get_modules_for


async def _run_scan_async(request_data: Dict[str, Any]) -> ScanResult:
    config = settings.model_copy(deep=True)
    config.scan.max_depth = int(request_data.get("max_depth") or config.scan.max_depth)
    config.scan.max_entities = int(request_data.get("max_entities") or config.scan.max_entities)
    config.scan.max_time_minutes = int(request_data.get("timeout") or config.scan.max_time_minutes)
    config.scan.confidence_threshold = float(
        request_data.get("confidence_threshold") or config.scan.confidence_threshold
    )

    orchestrator = Orchestrator(config)
    orchestrator.module_registry.get_modules_for = _filtered_module_getter(
        orchestrator,
        request_data.get("modules", {}),
    )

    request = ScanRequest(
        name=request_data.get("name") or None,
        email=request_data.get("email") or None,
        phone=request_data.get("phone") or None,
        username=request_data.get("username") or None,
        photo_path=request_data.get("photo_path") or None,
        location=request_data.get("location") or None,
        age_range=request_data.get("age_range") or None,
    )

    scan_id = await orchestrator.start_scan(request)
    deadline_seconds = max(10, config.scan.max_time_minutes * 60 + 5)
    started = asyncio.get_running_loop().time()

    while True:
        result = orchestrator.get_scan_result(scan_id)
        if result.status in TERMINAL_STATUSES:
            return result
        if asyncio.get_running_loop().time() - started > deadline_seconds:
            await orchestrator.cancel_scan(scan_id)
            return orchestrator.get_scan_result(scan_id)
        await asyncio.sleep(0.25)


def run_scan(request_data: Dict[str, Any]) -> ScanResult:
    return asyncio.run(_run_scan_async(request_data))


def result_to_dict(result: ScanResult) -> Dict[str, Any]:
    return result.model_dump(mode="json")


def result_from_dict(data: Dict[str, Any]) -> ScanResult:
    return ScanResult.model_validate(data)


def get_current_result(st) -> Optional[ScanResult]:
    ensure_dashboard_state(st)
    scan_id = st.session_state.current_scan_id
    if not scan_id:
        return None
    data = st.session_state.scan_results.get(scan_id)
    return result_from_dict(data) if data else None


def get_result_options(st) -> Dict[str, str]:
    ensure_dashboard_state(st)
    options = {}
    for scan_id, data in st.session_state.scan_results.items():
        result = result_from_dict(data)
        target = "Unknown Target"
        if result.entities:
            target = result.entities[0].value
        options[f"{scan_id[:8]} - {target}"] = scan_id
    return options


def entities_to_dataframe(entities: Iterable[Entity]) -> pd.DataFrame:
    rows = []
    for entity in entities:
        rows.append({
            "Entity": entity.value,
            "Type": entity.entity_type.value,
            "Discovered": entity.discovered_at,
            "Source": entity.source_module,
            "Confidence": entity.confidence,
            "Depth": entity.depth,
        })
    return pd.DataFrame(rows)


def graph_payload(result: ScanResult) -> Dict[str, Any]:
    return {
        "scan_id": result.scan_id,
        "nodes": [
            {
                "id": entity.id,
                "label": entity.value,
                "type": entity.entity_type.value,
                "source": entity.source_module,
                "confidence": entity.confidence,
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


def calculate_risk(result: ScanResult) -> Dict[str, Any]:
    counts = result.stats.by_type
    breach_count = counts.get(EntityType.BREACH_RECORD.value, 0) + counts.get(EntityType.LEAK_RECORD.value, 0)
    darkweb_count = counts.get(EntityType.DARK_WEB_MENTION.value, 0)
    social_count = counts.get(EntityType.SOCIAL_PROFILE.value, 0)
    location_count = counts.get(EntityType.LOCATION.value, 0)
    email_count = counts.get(EntityType.EMAIL.value, 0)
    phone_count = counts.get(EntityType.PHONE.value, 0)

    categories = {
        "Identity Exposure": min(100, 20 + (email_count + phone_count) * 15 + social_count * 5),
        "Credential Leaks": min(100, breach_count * 35),
        "Dark Web Mentions": min(100, darkweb_count * 35),
        "Location Tracking": min(100, location_count * 40),
    }
    overall = round(sum(categories.values()) / len(categories)) if categories else 0
    return {"overall_score": overall, "categories": categories}


def build_html_report(result: ScanResult) -> str:
    risk = calculate_risk(result)
    rows = "\n".join(
        f"<tr><td>{entity.entity_type.value}</td><td>{entity.value}</td><td>{entity.source_module}</td><td>{entity.confidence:.2f}</td></tr>"
        for entity in result.entities
    )
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Rahasya Report {result.scan_id}</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #0a0e17; color: #e2e8f0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #334155; padding: 8px; text-align: left; }}
    th {{ background: #111827; }}
  </style>
</head>
<body>
  <h1>Rahasya Scan Report</h1>
  <p><strong>Scan ID:</strong> {result.scan_id}</p>
  <p><strong>Status:</strong> {result.status.value}</p>
  <p><strong>Entities:</strong> {result.stats.total_entities}</p>
  <p><strong>Relationships:</strong> {result.stats.total_relationships}</p>
  <p><strong>Risk Score:</strong> {risk["overall_score"]}/100</p>
  <h2>Entities</h2>
  <table>
    <thead><tr><th>Type</th><th>Value</th><th>Source</th><th>Confidence</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""


def result_json(result: ScanResult) -> str:
    return json.dumps(result_to_dict(result), indent=2)
