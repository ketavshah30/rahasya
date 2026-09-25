"""Verify agent state survives reload and is visible through the dashboard."""

from datetime import datetime, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from rahasya.brain.contracts import AgentEvent, AgentReport, BrainState, EvidenceBatch, ReportClaim
from rahasya.core.models import Entity, EntityType, ScanRequest, ScanResult, ScanStatus
from rahasya.storage.scan_store import ScanStore


def test_agent_dashboard_renders_saved_report(tmp_path, monkeypatch):
    from rahasya.dashboard import state

    store = ScanStore(tmp_path)
    monkeypatch.setattr(state, "SCAN_STORE", store)
    store.save(ScanResult(
        scan_id="ui-test", status=ScanStatus.COMPLETED,
        started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
        request=ScanRequest(email="example@example.org", agentic=True),
        entities=[Entity(id="evidence", entity_type=EntityType.USERNAME, value="example-user",
                         normalized_value="example-user", source_module="GitHubEmailSearch")],
        brain=BrainState(
            model_calls=4, actions=1,
            report=AgentReport(claims=[ReportClaim(text="A source returned a username.", entity_ids=["evidence"])],
                               limitations=["A partial summary"]),
            report_entity_ids=["evidence"],
            events=[AgentEvent(kind="tool_finished", module="GitHubEmailSearch", outcome="success")],
            evidence_batches=[EvidenceBatch(action=1, module="GitHubEmailSearch", subject_id="seed",
                received=10000, duplicates=0, already_known=0, excluded=0, unique_new=10000,
                selected=20, deferred=9980)],
        ),
    ))
    page = Path(__file__).resolve().parents[1] / "rahasya/dashboard/pages/07_Agents.py"
    app = AppTest.from_file(str(page)).run(timeout=15)
    assert not app.exception
    assert app.title[0].value == "Local AI agents"
    assert any(item.value == "Evidence filtering" for item in app.subheader)
    assert any(item.value == "A source returned a username." for item in app.text)
    app.run(timeout=15)
    assert not app.exception
    assert next(metric.value for metric in app.metric if metric.label == "Model calls") == "4"


def test_request_preserves_agent_mode_for_worker():
    from rahasya.dashboard.state import _request_from_data

    assert _request_from_data({"email": "example@example.org", "agentic": True}).agentic is True
    assert _request_from_data({"email": "example@example.org", "agentic": False}).agentic is False
