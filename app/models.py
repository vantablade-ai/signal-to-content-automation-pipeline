from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class Signal(StrictModel):
    signal_id: str = Field(min_length=1, max_length=100)
    source_type: str = Field(min_length=1, max_length=30)
    source_reference: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=1, max_length=5000)
    author_reference: str | None = Field(default=None, max_length=100)
    published_at: str | None = None
    tags: tuple[str, ...] = ()
    source_metadata: dict[str, str] = Field(default_factory=dict)
    content_hash: str


class Eligibility(StrictModel):
    signal_id: str
    eligible: bool
    reasons: tuple[str, ...] = ()


class Classification(StrictModel):
    signal_id: str
    category: str
    relevance: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    evidence_quality: int = Field(ge=0, le=100)
    content_potential: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=300)


class RankedCandidate(StrictModel):
    signal_id: str
    classification: Classification
    relevance_component: float
    content_component: float
    evidence_component: float
    novelty_component: float
    ranking_score: float


class ContentBrief(StrictModel):
    brief_id: str
    signal_id: str
    topic: str
    core_claim: str
    supporting_points: tuple[str, ...] = Field(min_length=1, max_length=4)
    source_references: tuple[str, ...] = Field(min_length=1)
    target_audience: str
    angle: str
    key_takeaways: tuple[str, ...] = Field(min_length=1, max_length=3)
    limitations: tuple[str, ...]
    unsupported_claims_to_avoid: tuple[str, ...]
    recommended_format: str


class ScriptSection(StrictModel):
    heading: str = Field(max_length=100)
    text: str = Field(min_length=1, max_length=1000)


class ScriptArtifact(StrictModel):
    script_id: str
    brief_id: str
    title: str = Field(min_length=1, max_length=120)
    hook: str = Field(min_length=1, max_length=300)
    sections: tuple[ScriptSection, ...] = Field(min_length=1, max_length=6)
    voiceover_text: str = Field(min_length=1, max_length=4000)
    caption_text: str = Field(min_length=1, max_length=4000)
    source_references: tuple[str, ...] = Field(min_length=1)
    estimated_duration_seconds: int = Field(ge=5, le=600)


class ArtifactRecord(StrictModel):
    artifact_id: str
    artifact_type: str
    path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    created_at: str
    producer_stage: str
    producer_version: str
    media_type: str | None = None


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    status: StageStatus = StageStatus.PENDING
    fingerprint: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    provider: str | None = None
    provider_version: str | None = None
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    error: dict[str, str] | None = None
    blocked_by: list[str] = Field(default_factory=list)
    reuse_count: int = 0
    last_reused_at: str | None = None
    reused: bool = False


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    pipeline_version: str = "1.0.0"
    created_at: str
    updated_at: str
    source_configuration: dict[str, Any]
    stage_records: dict[str, StageRecord]
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)
    selected_signal_id: str | None = None
    provider_metadata: dict[str, str] = Field(default_factory=dict)
    failures: list[dict[str, str]] = Field(default_factory=list)
    completion_state: str = "INCOMPLETE"


class RawSignal(StrictModel):
    source_type: str
    source_reference: str
    title: str
    body: str = ""
    author_reference: str | None = None
    published_at: str | None = None
    tags: tuple[str, ...] = ()
    source_metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("title", "body")
    @classmethod
    def normalize_whitespace(cls, value: str) -> str:
        return " ".join(value.split())


