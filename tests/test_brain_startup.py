"""Exercise the launcher with a fake docker command; never start any service."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not SHELL, reason="PowerShell is needed to test the Windows launcher")


def launch(tmp_path, *, failure=0, skip_pull=False, existing_env=False):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[1] / "scripts" / "start-local-ai.ps1"
    shutil.copyfile(source, scripts / source.name)
    (tmp_path / ".env.example").write_text("EXAMPLE=1\n")
    if existing_env:
        (tmp_path / ".env").write_text("EXISTING=1\n")
    wrapper = tmp_path / "test-launch.ps1"
    wrapper.write_text("""
$ErrorActionPreference = 'Stop'
$script:callCount = 0
function docker {
    $script:callCount += 1
    ConvertTo-Json -Compress -InputObject @($args) | Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.jsonl')
    $global:LASTEXITCODE = if ($script:callCount -eq FAILURE) { 1 } else { 0 }
}
try {
    & (Join-Path $PSScriptRoot 'scripts/start-local-ai.ps1') -CpuOnly SKIP_PULL
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
""".replace("FAILURE", str(failure)).replace("SKIP_PULL", "-SkipModelPull" if skip_pull else ""))
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(wrapper)], capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    return result, calls


def test_launcher_creates_env_and_orders_startup_with_fake_docker(tmp_path):
    result, calls = launch(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == 5
    assert calls[0][-2:] == ["config", "--quiet"]
    assert calls[1][-1] == "ollama"
    assert calls[2][-2:] == ["pull", "qwen3:4b"]
    assert calls[3][-2:] == ["web", "worker"]
    assert calls[4][-2:] == ["brain-check", "--probe"]
    assert (tmp_path / ".env").read_text() == "EXAMPLE=1\n"


def test_launcher_preserves_env_and_can_skip_download(tmp_path):
    result, calls = launch(tmp_path, skip_pull=True, existing_env=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == 4
    assert not any("pull" in call for call in calls)
    assert (tmp_path / ".env").read_text() == "EXISTING=1\n"


@pytest.mark.parametrize("failure", [1, 2, 3, 4, 5])
def test_launcher_stops_on_each_failed_step(tmp_path, failure):
    result, calls = launch(tmp_path, failure=failure)
    assert result.returncode == 1
    assert len(calls) == failure
    assert "Local AI is ready" not in result.stdout
