from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(item.capitalize() for item in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True, extra="forbid")


class ProblemDetail(ApiModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    correlation_id: UUID
    retryable: bool
    current_version: int | None = None


class BuildManifest(ApiModel):
    service: Literal["sopara-control-plane"] = "sopara-control-plane"
    revision: str
    image_digest: str
    api_schema_version: int


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"


class SessionBootstrap(ApiModel):
    subject: str
    csrf_token: str
    authenticated_at: datetime


class Capability(ApiModel):
    name: str
    allowed: bool
    reason_code: str


class ReadinessSnapshot(ApiModel):
    schema_version: int
    revision: int
    stream_cursor: str
    generated_at: datetime
    last_domain_event_at: datetime | None
    last_ingest_at: datetime | None
    mode: Literal["PRIVATE_RESEARCH"] = "PRIVATE_RESEARCH"
    operability: str
    evidence_class: str
    source_health: str
    window_state: str
    incident_count: int
    capabilities: list[Capability]


class Page(ApiModel):
    items: list[dict[str, object]]
    next_cursor: str | None
    snapshot_revision: int
    schema_version: int


CommandType = Literal[
    "CREATE_EXPERIMENT",
    "START_REPLAY",
    "RESUME_REPLAY",
    "RECONCILE_REPLAY",
    "REQUEST_REPORT",
    "REQUEST_EXPORT",
    "HALT_SIMULATION",
]
TargetType = Literal["DATASET", "EXPERIMENT", "REPLAY_RUN", "SYSTEM"]
CommandState = Literal["RECEIVED", "CLAIMED", "COMPLETED", "REJECTED", "EXPIRED"]


class CommandRequest(ApiModel):
    command_type: CommandType
    target_type: TargetType
    target_id: UUID
    payload: dict[str, object] = Field(default_factory=dict)


class CommandReceiptResponse(ApiModel):
    command_id: UUID
    state: CommandState
    duplicate: bool
    completion_implied: Literal[False] = False
    correlation_id: UUID


PageSize = Annotated[int, Field(ge=1, le=100)]
