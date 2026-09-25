"""Validated decisions and durable, bounded agent activity."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rahasya.brain.settings import AgentRole


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Delegation(Decision):
    role: AgentRole | None
    reason: str = Field(min_length=1, max_length=400)


class ToolChoice(Decision):
    module: str | None
    reason: str = Field(min_length=1, max_length=400)


class Assessment(Decision):
    entity_id: str
    disposition: Literal["supported", "uncertain", "contradicted"]
    reason: str = Field(min_length=1, max_length=400)


class Review(Decision):
    assessments: list[Assessment] = Field(max_length=5)


class ReportClaim(Decision):
    text: str = Field(min_length=1, max_length=500)
    entity_ids: list[str] = Field(min_length=1, max_length=5)


class AgentReport(Decision):
    claims: list[ReportClaim] = Field(max_length=6)
    limitations: list[str] = Field(max_length=6)


class AgentEvent(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str
    role: str = "runtime"
    entity_id: str | None = None
    module: str | None = None
    outcome: str | None = None
    reason: str = ""
    result_ids: list[str] = Field(default_factory=list)


class EvidenceBatch(BaseModel):
    action: int
    module: str
    subject_id: str
    received: int
    duplicates: int
    already_known: int
    excluded: int
    unique_new: int
    selected: int
    deferred: int


class BrainState(BaseModel):
    mode: Literal["ollama"] = "ollama"
    models: dict[str, str] = Field(default_factory=dict)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    actions: int = 0
    stop_reason: str | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    assessments: list[Assessment] = Field(default_factory=list)
    report: AgentReport | None = None
    report_entity_ids: list[str] = Field(default_factory=list)
    evidence_batches: list[EvidenceBatch] = Field(default_factory=list)
