"""非生产集成调试定位响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .runtime import TraceDetailResponse  # noqa: TC001 - FastAPI/Pydantic needs runtime annotation for OpenAPI


class IntegrationDebugStageCheck(BaseModel):
    """集成链路单阶段定位结果。"""

    key: str
    label: str
    state: str
    evidence_count: int = 0
    primary_evidence: str | None = None
    links: list[str] = Field(default_factory=list)


class IntegrationDebugEvidenceLink(BaseModel):
    """调试证据跳转。"""

    kind: str
    label: str
    api_path: str | None = None
    route_name: str | None = None
    route_params: dict[str, Any] = Field(default_factory=dict)
    route_query: dict[str, Any] = Field(default_factory=dict)


class IntegrationDebugNextAction(BaseModel):
    """只读下一步建议。"""

    kind: str
    label: str
    description: str
    route_name: str | None = None
    route_params: dict[str, Any] = Field(default_factory=dict)
    route_query: dict[str, Any] = Field(default_factory=dict)


class IntegrationDebugCaseResponse(BaseModel):
    """集成调试案件定位结果。"""

    case_id: str
    session_id: int | None = None
    session_code: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    command_code: str | None = None
    status: str
    phase: str
    verdict: str
    blocking_domain: str | None = None
    blocking_code: str | None = None
    owner: str
    severity: str
    recoverability: str
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    stage_checks: list[IntegrationDebugStageCheck] = Field(default_factory=list)
    evidence_links: list[IntegrationDebugEvidenceLink] = Field(default_factory=list)
    next_actions: list[IntegrationDebugNextAction] = Field(default_factory=list)
    trace_detail: TraceDetailResponse | None = None


class IntegrationDebugCaseListResponse(BaseModel):
    """最新集成调试案件列表。"""

    total: int
    items: list[IntegrationDebugCaseResponse] = Field(default_factory=list)


__all__ = [
    "IntegrationDebugCaseListResponse",
    "IntegrationDebugCaseResponse",
    "IntegrationDebugEvidenceLink",
    "IntegrationDebugNextAction",
    "IntegrationDebugStageCheck",
]
