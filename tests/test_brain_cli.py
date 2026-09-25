import httpx
import pytest
from click.testing import CliRunner

from rahasya.__main__ import cli
from rahasya.brain.ollama import OllamaClient
from rahasya.config import Settings
from rahasya.core.models import ScanResult, ScanStatus


def test_brain_info_never_connects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline configuration display attempted to connect")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    result = CliRunner().invoke(cli, ["brain-info"])
    assert result.exit_code == 0, result.output
    assert "qwen3:4b" in result.output and "ollama" in result.output


@pytest.mark.parametrize("available,probe,expected_exit", [(True, True, 0), (True, False, 0), (False, False, 1)])
def test_brain_check_with_simulated_server(monkeypatch, available, probe, expected_exit):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}] if available else []})
        return httpx.Response(200, json={"done": True, "message": {"content": '{"module":null,"reason":"No tools"}'}})

    monkeypatch.setattr("rahasya.config.settings", Settings(_env_file=None))
    monkeypatch.setattr("rahasya.brain.ollama.OllamaClient",
                        lambda settings: OllamaClient(settings, transport=httpx.MockTransport(handle)))
    result = CliRunner().invoke(cli, ["brain-check"] + (["--probe"] if probe else []))
    assert result.exit_code == expected_exit, result.output
    assert calls == (["/api/tags", "/api/chat"] if probe else ["/api/tags"])


@pytest.mark.parametrize("flag,expected", [("--agentic", True), ("--no-agentic", False)])
def test_scan_passes_explicit_agent_mode_to_orchestrator(monkeypatch, flag, expected):
    requests = []

    class FakeOrchestrator:
        def __init__(self, settings):
            pass

        async def start_scan(self, request):
            requests.append(request)
            return "cli-test"

        def get_scan_result(self, scan_id):
            return ScanResult(scan_id=scan_id, status=ScanStatus.COMPLETED)

    monkeypatch.setattr("rahasya.core.orchestrator.Orchestrator", FakeOrchestrator)
    result = CliRunner().invoke(cli, ["scan", "--email", "example@example.org", flag])
    assert result.exit_code == 0, result.output
    assert requests[0].agentic is expected
