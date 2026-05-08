"""运行监控与 Trace 查询响应模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel, Field


class TraceQueryRequest(BaseModel):
    """Trace 列表查询请求。"""

    workline_id: int | None = None
    device_id: int | None = None
    status: str | None = None
    step_code: str | None = None
    keyword: str | None = None
    only_active: bool = False
    only_failed: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class RuntimeTraceListItem(BaseModel):
    """Trace 列表项。"""

    session_id: int
    session_code: str
    trace_id: str | None = None
    request_id: str | None = None
    business_key: str | None = None
    barcode: str | None = None
    workline_id: int
    workline_name: str | None = None
    workline_code: str | None = None
    device_id: int | None = None
    device_name: str | None = None
    device_code: str | None = None
    command_code: str | None = None
    status: str
    step_code: str | None = None
    current_wait_type: str | None = None
    failure_domain: str | None = None
    failure_code: str | None = None
    latest_timeline_action: str | None = None
    latest_timeline_status: str | None = None
    latest_timeline_message: str | None = None
    started_at: datetime | None = None
    last_ingress_at: datetime | None = None
    deadline_at: datetime | None = None
    is_timed_out: bool = False


class RuntimeTraceListResponse(BaseModel):
    """Trace 列表响应。"""

    total: int
    items: list[RuntimeTraceListItem]


class TraceOverviewSummary(BaseModel):
    """Trace 详情页顶部摘要。"""

    callback_logs: int = 0
    inboxes: int = 0
    commands: int = 0
    outboxes: int = 0
    timelines: int = 0
    diagnostics: int = 0
    session_status: str | None = None
    step_code: str | None = None
    current_wait_type: str | None = None
    latest_timeline_action: str | None = None
    latest_timeline_status: str | None = None
    latest_timeline_message: str | None = None


class TraceContextResponse(BaseModel):
    request_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    causation_id: str | None = None
    workline_id: int | None = None
    session_id: int | None = None
    inbox_id: int | None = None
    device_id: int | None = None
    device_code: str | None = None
    command_id: int | None = None
    command_code: str | None = None
    outbox_id: int | None = None
    dispatch_key: str | None = None
    canonical_event_type: str | None = None
    transition: str | None = None
    plugin_key: str | None = None
    contract_version: str | None = None


class TraceCallbackLogItem(BaseModel):
    id: int
    callback_type: str
    device_id: str
    request_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    causation_id: str | None = None
    response_status: int
    response_time_ms: int
    error_message: str | None = None
    ingress_outcome: str | None = None
    failure_stage: str | None = None
    request_body: dict[str, Any]
    created_at: datetime
    updated_at: datetime | None = None


class TraceInboxItem(BaseModel):
    id: int
    kind: str
    source_system: str
    source_message_id: str | None = None
    trace_id: str | None = None
    event_id: str | None = None
    causation_id: str | None = None
    workline_id: int | None = None
    device_id: int | None = None
    command_id: int | None = None
    session_id: int | None = None
    status: str
    received_at: datetime
    processed_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 0
    next_retry_at: datetime | None = None
    error_message: str | None = None
    payload_json: dict[str, Any]


class TraceSessionItem(BaseModel):
    id: int
    session_code: str
    workline_id: int
    plugin_key: str
    run_mode: str
    business_key: str | None = None
    barcode: str | None = None
    status: str
    step_code: str | None = None
    trace_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    current_wait_type: str | None = None
    current_wait_token: str | None = None
    waiting_since: datetime | None = None
    deadline_at: datetime | None = None
    awaiting_command_id: int | None = None
    failure_domain: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    ingress_count: int = 0
    last_request_id: str | None = None
    last_ingress_at: datetime | None = None
    last_inbox_id: int | None = None
    context_json: dict[str, Any]


class TraceCommandItem(BaseModel):
    id: int
    device_id: int
    command_code: str
    trace_id: str | None = None
    workline_id: int | None = None
    session_id: str | None = None
    task_type: str
    status: str
    result: str | None = None
    retry_count: int = 0
    sent_at: datetime | None = None
    ack_received_at: datetime | None = None
    completed_at: datetime | None = None
    ack_code: int | None = None
    ack_message: str | None = None
    ack_trace_id: str | None = None
    step_code: str | None = None
    params: dict[str, Any]
    result_data: dict[str, Any] | None = None
    error_detail: dict[str, Any] | None = None
    duration_ms: int | None = None


class TraceOutboxItem(BaseModel):
    id: int
    session_id: int | None = None
    workline_id: int
    dispatch_type: str
    dispatch_key: str
    target_type: str
    target_code: str
    status: str
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    sent_at: datetime | None = None
    finished_at: datetime | None = None
    payload_json: dict[str, Any]


class TraceDispatchAttemptItem(BaseModel):
    id: int
    outbox_id: int
    dispatch_key: str
    attempt_no: int
    lease_token: str
    status: str
    target_type: str | None = None
    target_code: str | None = None
    started_at: datetime
    finalized_at: datetime | None = None
    error_message: str | None = None
    response_json: dict[str, Any] = Field(default_factory=dict)
    trace_json: dict[str, Any] = Field(default_factory=dict)


class TraceTimelineItem(BaseModel):
    id: int
    session_id: int
    workline_id: int
    trace_id: str | None = None
    seq_no: int
    occurred_at: datetime
    stage: str
    action_type: str
    actor_type: str
    actor_code: str | None = None
    from_status: str | None = None
    to_status: str | None = None
    status: str
    failure_domain: str | None = None
    message: str | None = None
    payload_json: dict[str, Any] | None = None
    related_inbox_id: int | None = None
    related_command_id: int | None = None


class TraceDiagnosticContextItem(BaseModel):
    request_id: str | None = None
    trace_id: str | None = None
    session_id: int | None = None
    inbox_id: int | None = None
    outbox_id: int | None = None
    command_code: str | None = None
    device_code: str | None = None
    workline_id: int | None = None
    workline_code: str | None = None
    plugin_key: str | None = None
    canonical_event_type: str | None = None
    transition: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TraceDiagnosticItem(TraceDiagnosticContextItem):
    pass


class TraceDetailResponse(BaseModel):
    trace: TraceContextResponse
    summary: TraceOverviewSummary
    session: TraceSessionItem | None = None
    sessions: list[TraceSessionItem] = Field(default_factory=list)
    callback_logs: list[TraceCallbackLogItem] = Field(default_factory=list)
    inboxes: list[TraceInboxItem] = Field(default_factory=list)
    commands: list[TraceCommandItem] = Field(default_factory=list)
    outboxes: list[TraceOutboxItem] = Field(default_factory=list)
    dispatch_attempts: list[TraceDispatchAttemptItem] = Field(default_factory=list)
    timelines: list[TraceTimelineItem] = Field(default_factory=list)
    diagnostics: list[TraceDiagnosticItem] = Field(default_factory=list)


class DiagnosticCardResponse(BaseModel):
    title: str
    summary: str
    error_code: str
    error_domain: str
    severity: str
    recoverability: str
    problem_class: str
    user_message: str
    operator_action: str | None = None
    technical_summary: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    context: TraceDiagnosticContextItem


class TraceBlockingPointResponse(BaseModel):
    trace_id: str
    request_id: str | None = None
    blocking_point: str
    owner: str
    recoverability: str
    operator_action: str
    diagnostic_card: DiagnosticCardResponse
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_steps: list[str] = Field(default_factory=list)


class RuntimeStatCard(BaseModel):
    key: str
    label: str
    value: int
    status: str = "info"


class RuntimeDeviceHealthSummary(BaseModel):
    total: int = 0
    abnormal: int = 0
    maintenance: int = 0
    loaded: int = 0
    healthy: int = 0


class RuntimeOverviewResponse(BaseModel):
    stats: list[RuntimeStatCard]
    recent_failed_traces: list[RuntimeTraceListItem] = Field(default_factory=list)
    hot_worklines: list[RuntimeWorklineSummary] = Field(default_factory=list)
    abnormal_devices: list[RuntimeDeviceSummary] = Field(default_factory=list)
    device_health: RuntimeDeviceHealthSummary = Field(default_factory=RuntimeDeviceHealthSummary)


class RuntimeWorklineSummary(BaseModel):
    id: int
    line_code: str
    line_name: str
    line_type: str
    zone_name: str | None = None
    plugin_key: str | None = None
    contract_version: str | None = None
    is_active: bool
    device_count: int = 0
    active_session_count: int = 0
    waiting_session_count: int = 0
    failed_session_count: int = 0
    error_device_count: int = 0
    offline_device_count: int = 0
    maintenance_device_count: int = 0
    run_mode: str = "AUTO"
    runtime_status: str = "READY"
    active_safety_incident_id: int | None = None
    stopped_at: datetime | None = None
    stopped_reason: str | None = None
    resumed_at: datetime | None = None
    last_activity_at: datetime | None = None


class RuntimeWorklineDeviceItem(BaseModel):
    id: int
    device_code: str
    device_name: str
    device_role: str
    role_index: int
    upstream_device_id: int | None = None
    device_status: str
    maintenance_mode: bool = False
    current_command_id: int | None = None
    last_heartbeat_at: datetime | None = None
    error_code: str | None = None


class RuntimeWorklineDetailResponse(BaseModel):
    summary: RuntimeWorklineSummary
    devices: list[RuntimeWorklineDeviceItem] = Field(default_factory=list)
    active_sessions: list[RuntimeTraceListItem] = Field(default_factory=list)
    recent_failed_traces: list[RuntimeTraceListItem] = Field(default_factory=list)
    recent_completed_traces: list[RuntimeTraceListItem] = Field(default_factory=list)


class RuntimeTraceDeviceAction(BaseModel):
    kind: str
    label: str
    status: str | None = None
    timestamp: datetime | None = None
    message: str | None = None


class RuntimeTraceDevicePathNode(BaseModel):
    device_id: int
    device_code: str | None = None
    device_name: str | None = None
    device_role: str | None = None
    is_current: bool = False
    actions: list[RuntimeTraceDeviceAction] = Field(default_factory=list)


class RuntimeTraceTimelineGroup(BaseModel):
    group_key: str
    group_type: str
    display_name: str
    device_id: int | None = None
    device_code: str | None = None
    is_current: bool = False
    is_blocked: bool = False
    events: list[TraceTimelineItem] = Field(default_factory=list)


class RuntimeBlockingReason(BaseModel):
    device_id: int | None = None
    reason: str
    detail: str | None = None


class RuntimeTracePathResponse(BaseModel):
    workline_id: int | None = None
    session_id: int | None = None
    trace_id: str | None = None
    devices: list[RuntimeTraceDevicePathNode] = Field(default_factory=list)
    timeline_groups: list[RuntimeTraceTimelineGroup] = Field(default_factory=list)
    current_blocking_device_id: int | None = None
    blocking_reason: RuntimeBlockingReason | None = None
    evidence: TraceDetailResponse | None = None


class RuntimeDeviceSummary(BaseModel):
    id: int
    device_code: str
    device_name: str
    device_role: str
    role_index: int
    workline_id: int | None = None
    workline_name: str | None = None
    workline_code: str | None = None
    device_status: str
    maintenance_mode: bool = False
    current_command_id: int | None = None
    pending_command_count: int = 0
    last_heartbeat_at: datetime | None = None
    recent_callback_at: datetime | None = None
    error_code: str | None = None


class RuntimeDeviceDetailResponse(BaseModel):
    summary: RuntimeDeviceSummary
    recent_commands: list[TraceCommandItem] = Field(default_factory=list)
    recent_callbacks: list[TraceCallbackLogItem] = Field(default_factory=list)
    active_sessions: list[RuntimeTraceListItem] = Field(default_factory=list)


_ = RuntimeOverviewResponse.model_rebuild()
