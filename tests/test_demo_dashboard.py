"""Test the faculty demo without external lookups or models."""
from pathlib import Path
import pytest
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest
from rahasya.dashboard import state
from rahasya.dashboard.demo import load_demo, DEMO_ID
from rahasya.dashboard.ui import is_demo
from rahasya.storage.scan_store import ScanStore
from rahasya.storage.network_audit import NetworkAuditStore
PAGES = Path(__file__).resolve().parents[1] / "rahasya/dashboard/pages"

@pytest.fixture
def demo_store(tmp_path, monkeypatch):
    store = ScanStore(tmp_path)
    monkeypatch.setattr(state, "SCAN_STORE", store)
    return store

def test_demo_is_idempotent_offline_and_keeps_synthetic_labels(demo_store, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("The demo tried to start a live scan")
    monkeypatch.setattr(state, "submit_background_scan", forbidden)
    first = load_demo(demo_store)
    second = load_demo(demo_store)
    assert first.scan_id == second.scan_id == DEMO_ID
    assert len(demo_store.list()) == 1 and is_demo(second)
    assert second.brain.model_calls == second.brain.actions == 0
    assert len(NetworkAuditStore(demo_store.root).load(DEMO_ID)) == 3
    assert "SYNTHETIC DEMO" in state.build_html_report(second)
    assert all(e.metadata["synthetic_demo"] for e in second.entities)
    assert {r.source_id for r in second.relationships} <= {e.id for e in second.entities}

@pytest.mark.parametrize("filename", [
    "00_Overview.py", "01_New_Scan.py", "02_CIA_Web.py", "03_Timeline.py",
    "04_Exposure_Report.py", "05_Export.py", "06_Network_Log.py", "07_Agents.py",
    "08_Demo_Studio.py", "09_Evidence.py",
])
def test_all_presentation_pages_render_synthetic_case(demo_store, filename):
    load_demo(demo_store)
    app = AppTest.from_file(str(PAGES / filename)).run(timeout=25)
    assert not app.exception, [e.message for e in app.exception]
    assert any("SYNTHETIC DEMO" in item.value for item in app.markdown)

def test_demo_lab_runs_filtering_without_model_calls(demo_store):
    app = AppTest.from_file(str(PAGES / "08_Demo_Studio.py")).run(timeout=25)
    next(b for b in app.button if b.label == "Run evidence filtering").click().run(timeout=25)
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Input records"] == "10,000"
    assert metrics["Working candidates"] == "20"
    assert metrics["Review shortlist"] == "5"
    assert metrics["Model calls"] == "0"
    assert app.session_state["demo_reduction"]["strong_selected"] is True
    assert not demo_store.list()

def test_evidence_filters_and_no_results_state(demo_store):
    load_demo(demo_store)
    app = AppTest.from_file(str(PAGES / "09_Evidence.py")).run(timeout=25)
    next(e for e in app.multiselect if e.label == "Review status").set_value(["uncertain"]).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Visible observations").value == "2"
    app.text_input[0].set_value("does-not-exist-123").run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Visible observations").value == "0"

def test_empty_overview_and_navigation_entry(demo_store):
    app = AppTest.from_file(str(PAGES / "00_Overview.py")).run(timeout=25)
    assert not app.exception
    assert any("Your workspace is ready" in e.value for e in app.info)
    shell = AppTest.from_file(str(PAGES.parent / "app.py")).run(timeout=25)
    assert not shell.exception


def test_failed_assessment_displays_saved_error(demo_store):
    from rahasya.core.models import ScanResult, ScanStatus

    demo_store.save(ScanResult(
        scan_id="failed-save", status=ScanStatus.FAILED,
        error="PermissionError: [WinError 5] Access is denied",
    ))
    # Orchestrator status sidecars may omit the error; use the result fallback.
    demo_store.save_status("failed-save", status="FAILED")
    app = AppTest.from_file(str(PAGES / "09_Evidence.py")).run(timeout=25)
    assert not app.exception
    assert any("WinError 5" in item.value for item in app.error)
