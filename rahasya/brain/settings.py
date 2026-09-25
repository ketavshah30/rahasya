"""Local model routing and bounded investigation settings."""

from enum import Enum
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    IDENTITY = "identity"
    SOCIAL = "social"
    EXPOSURE = "exposure"
    DARKWEB = "darkweb"
    MEDIA_ARCHIVE = "media_archive"
    REVIEWER = "reviewer"
    REPORTER = "reporter"


class ModelAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    coordinator: str = Field(default="qwen3:4b", min_length=1)
    identity: str = Field(default="qwen3:4b", min_length=1)
    social: str = Field(default="qwen3:4b", min_length=1)
    exposure: str = Field(default="qwen3:4b", min_length=1)
    darkweb: str = Field(default="qwen3:4b", min_length=1)
    media_archive: str = Field(default="qwen3:4b", min_length=1)
    reviewer: str = Field(default="qwen3:4b", min_length=1)
    reporter: str = Field(default="qwen3:4b", min_length=1)

    @field_validator("*")
    @classmethod
    def reject_cloud_tags(cls, value: str) -> str:
        # An early configuration check, not a replacement for disabling cloud
        # features on the Ollama server. Arbitrary local aliases may exist.
        if "cloud" in value.lower():
            raise ValueError("Use a downloaded local model; cloud model tags are disabled")
        return value

    def for_role(self, role: AgentRole) -> str:
        return getattr(self, AgentRole(role).value)


class BrainSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["ollama"] = "ollama"
    base_url: AnyHttpUrl = Field(default=AnyHttpUrl("http://127.0.0.1:11434"))
    models: ModelAssignments = Field(default_factory=ModelAssignments)
    enabled: bool = False
    context_length: int = Field(default=4096, ge=2048, le=32768)
    max_output_tokens: int = Field(default=512, ge=128, le=2048)
    request_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_model_calls: int = Field(default=60, ge=4, le=500)
    max_actions: int = Field(default=15, ge=1, le=100)
    max_actions_per_entity: int = Field(default=3, ge=1, le=10)
    max_candidates_per_tool: int = Field(default=20, ge=5, le=100)

    @field_validator("base_url")
    @classmethod
    def require_local_endpoint(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.host not in {"localhost", "127.0.0.1", "[::1]", "host.docker.internal", "ollama"}:
            raise ValueError("Ollama must use localhost, host.docker.internal, or the local ollama service")
        if value.username or value.password or value.query or value.fragment or value.path not in {None, "/"}:
            raise ValueError("Use an Ollama server origin without credentials, path, or query")
        return value
