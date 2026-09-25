"""Offline model configuration and role boundary checks."""

import socket

import pytest
from pydantic import ValidationError

from rahasya.brain.agents import describe_agents
from rahasya.brain.settings import BrainSettings
from rahasya.config import Settings


def test_nested_role_override_preserves_other_assignments(monkeypatch):
    monkeypatch.setenv("BRAIN__MODELS__COORDINATOR", "alternate-model")
    monkeypatch.setenv("BRAIN__MODELS__REVIEWER", "review-model")
    settings = Settings(_env_file=None)
    agents = {row["role"]: row for row in describe_agents(settings.brain)}
    assert agents["coordinator"]["model"] == "alternate-model"
    assert agents["reviewer"]["model"] == "review-model"
    assert agents["social"]["model"] == "qwen3:4b"


@pytest.mark.parametrize("assignments", [{"coordinatr": "model"}, {"reviewer": "  "}])
def test_invalid_assignments_fail_early(assignments):
    with pytest.raises(ValidationError):
        BrainSettings(models=assignments)


@pytest.mark.parametrize("model", ["qwen3:cloud", "qwen3:4b-cloud", "namespace/MODEL-CLOUD"])
def test_cloud_model_tags_are_rejected(model):
    with pytest.raises(ValidationError, match="cloud model tags are disabled"):
        BrainSettings(models={"coordinator": model})


def test_paid_provider_is_not_a_supported_configuration():
    with pytest.raises(ValidationError):
        BrainSettings(provider="openai")


def test_local_endpoint_can_be_overridden_for_docker(monkeypatch):
    monkeypatch.setenv("BRAIN__BASE_URL", "http://host.docker.internal:11434")
    settings = Settings(_env_file=None)
    assert settings.brain.base_url.host == "host.docker.internal"
    assert settings.brain.provider == "ollama"


def test_catalog_is_offline_and_restricts_discovery_roles(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Offline inspection attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", unexpected_connection)
    rows = describe_agents(BrainSettings())
    by_role = {row["role"]: row for row in rows}
    for role in ("coordinator", "reviewer", "reporter"):
        assert by_role[role]["planned_modules"] == []
    modules = [name for row in rows for name in row["planned_modules"]]
    assert len(modules) == len(set(modules))
    assert "RecoveryHintProbe" not in modules
    assert "HIBPPasswords" not in modules
