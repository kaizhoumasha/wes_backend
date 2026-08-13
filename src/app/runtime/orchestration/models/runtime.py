"""运行监控与 Trace 查询响应模型。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TraceQueryRequest(BaseModel):
    """Trace 列表查询请求。"""

    workline_id: int | None = None
    device_id: int | None = None
    status: str | None = None
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
    last_inbox_id: int | None = None
    event_type: str | None = None
    event_payload: dict[str, Any] | None = None
    business_key: str | None = None
    barcode: str | None = None
    workline_id: int
    workline_name: str | None = None
    workline_code: str | None = None
    device_id: int | None = None
    device_name: str | None = None
    device_code: str | None = None
    command_code: str | None = None
    current_device_id: int | None = None
    current_device_name: str | None = None
    current_device_code: str | None = None
    current_action: str | None = None
    current_action_source: str | None = None
    last_device_id: int | None = None
    last_device_name: str | None = None
    last_device_code: str | None = None
    status: str
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
    subject_code: str
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
    run_mode: str
    business_key: str | None = None
    barcode: str | None = None
    status: str
    trace_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    current_wait_type: str | None = None
    current_wait_timeout_seconds: int | None = None
    waiting_since: datetime | None = None
    deadline_at: datetime | None = None
    awaiting_device_command_code: str | None = None
    reconciliation_state: str | None = None
    reconciliation_reason: str | None = None
    reconciliation_source_kind: str | None = None
    reconciliation_source_inbox_id: int | None = None
    reconciliation_source_outbox_id: int | None = None
    reconciliation_command_id: int | None = None
    reconciliation_device_id: int | None = None
    reconciliation_wait_token: str | None = None
    reconciliation_ack_received_at: datetime | None = None
    reconciliation_deadline_at: datetime | None = None
    reconciliation_occurred_at: datetime | None = None
    reconciliation_late_evidence_received: bool = False
    reconciliation_resolution: str | None = None
    reconciliation_resolved_at: datetime | None = None
    required_operator_action: str | None = None
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
    command_code: str
    device_code: str
    trace_id: str | None = None
    line_run_epoch_id: int
    device_binding_id: int
    execution_ref_type: str
    execution_ref_id: str
    contract_key: str
    contract_version: str
    task_type: str
    status: str
    payload_digest: str
    deadline_at: datetime
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    ack_received_at: datetime | None = None
    completed_at: datetime | None = None
    result_evidence_id: int | None = None
    failure_code: str | None = None
    reconciliation_reason: str | None = None
    params: dict[str, Any]


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
    blocked_by_runtime_hold_id: int | None = None
    blocked_by_reconciliation_session_id: int | None = None
    blocked_device_id: int | None = None
    blocked_workline_id: int | None = None
    blocked_reason: str | None = None
    blocked_at: datetime | None = None
    last_blocked_check_at: datetime | None = None
    blocked_wait_seconds: int | None = None
    blocked_check_count: int | None = None
    blocked_detail_json: dict[str, Any] | None = None
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


class TraceResourceEvidenceResponse(BaseModel):
    """Trace 关联的资源域证据链。"""

    resource_state_events: list[dict[str, Any]] = Field(default_factory=list)
    rack_releases: list[dict[str, Any]] = Field(default_factory=list)
    rack_release_bin_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    wms_writeback_evidence: list[dict[str, Any]] = Field(default_factory=list)
    rack_bin_mounts: list[dict[str, Any]] = Field(default_factory=list)
    runtime_holds: list[dict[str, Any]] = Field(default_factory=list)


class DiagnosisEvidenceHealthItemResponse(BaseModel):
    """诊断证据健康明细。"""

    key: str
    label: str
    count: int
    state: str
    hint: str


class DiagnosisEvidenceHealthResponse(BaseModel):
    """诊断证据健康摘要。"""

    level: str
    summary: str
    missing: list[str] = Field(default_factory=list)
    items: list[DiagnosisEvidenceHealthItemResponse] = Field(default_factory=list)


class DiagnosisVerdictResponse(BaseModel):
    """Trace 统一诊断结论。"""

    state: str
    severity: str
    title: str
    summary: str
    requires_operator_action: bool
    primary_action: str | None = None
    blocking_point: str
    owner: str | None = None
    evidence_health: DiagnosisEvidenceHealthResponse


class TraceDetailResponse(BaseModel):
    trace: TraceContextResponse
    summary: TraceOverviewSummary
    diagnosis_verdict: DiagnosisVerdictResponse
    sessions: list[TraceSessionItem] = Field(default_factory=list)
    callback_logs: list[TraceCallbackLogItem] = Field(default_factory=list)
    inboxes: list[TraceInboxItem] = Field(default_factory=list)
    commands: list[TraceCommandItem] = Field(default_factory=list)
    outboxes: list[TraceOutboxItem] = Field(default_factory=list)
    dispatch_attempts: list[TraceDispatchAttemptItem] = Field(default_factory=list)
    timelines: list[TraceTimelineItem] = Field(default_factory=list)
    diagnostics: list[TraceDiagnosticItem] = Field(default_factory=list)
    resource_evidence: TraceResourceEvidenceResponse = Field(default_factory=TraceResourceEvidenceResponse)


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
    diagnosis_verdict: DiagnosisVerdictResponse
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
    runtime_status: str = "STOPPED"
    active_safety_incident_id: int | None = None
    stopped_at: datetime | None = None
    stopped_reason: str | None = None
    resumed_at: datetime | None = None
    start_admission_status: str | None = None
    start_admission_message: str | None = None
    start_admission_failed_device_code: str | None = None
    start_admission_checked_at: datetime | None = None
    last_start_request_id: str | None = None
    last_start_trace_id: str | None = None
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
    open_command_count: int = 0
    pending_command_count: int = 0
    blocked_outbox_count: int = 0
    blocked_reason: str | None = None
    blocked_wait_seconds: int | None = None
    blocked_check_count: int | None = None
    blocked_detail_json: dict[str, Any] | None = None
    open_issue_count: int = 0
    active_runtime_hold_ids: list[int] = Field(default_factory=list)
    last_heartbeat_at: datetime | None = None
    error_code: str | None = None


class RuntimeWorklineReadiness(StrEnum):
    """产线启动准入与运行准备状态。"""

    READY = "READY"  # 满足启动或继续调度的准入条件
    NOT_READY = "NOT_READY"  # 存在阻断条件，不允许启动或继续调度
    UNKNOWN = "UNKNOWN"  # 当前证据不足，无法判断准备状态


class RuntimeStationLease(StrEnum):
    """工站当前占用来源，用于判断是否可继续调度。"""

    IDLE = "IDLE"  # 工站未被运行时任务占用，可参与新调度
    ACTIVE_RACK_BOUND = "ACTIVE_RACK_BOUND"  # 工站已绑定运行中料架，避免重复占用
    ACTIVE_DISPATCH_LEASE = "ACTIVE_DISPATCH_LEASE"  # 工站已有下发租约，等待指令完成或释放
    ACTIVE_SESSION_BOUND = "ACTIVE_SESSION_BOUND"  # 工站已绑定活动 session，继续归属当前流程
    UNKNOWN = "UNKNOWN"  # 缺少占用证据，保持保守展示


class RuntimeSingleLayerRackSnapshot(StrEnum):
    """单层料架快照状态，用于运行时资源视图诊断。"""

    ACTIVE = "ACTIVE"  # WES 存在有效的单层料架快照
    MISSING = "MISSING"  # 未找到单层料架快照
    INVALID = "INVALID"  # 找到快照但结构或状态不可用
    NON_SINGLE_LAYER_EVIDENCE = "NON_SINGLE_LAYER_EVIDENCE"  # 仅有非单层料架证据，不适用于该视图
    UNKNOWN = "UNKNOWN"  # 证据不足，无法判断料架快照状态


class RuntimeRackOperationWait(StrEnum):
    """料架操作等待状态，描述 WMS 回调与超时结果。"""

    WAITING_WMS = "WAITING_WMS"  # 已发起料架操作，正在等待 WMS 回调
    WMS_CALLBACK_RECEIVED = "WMS_CALLBACK_RECEIVED"  # 已收到 WMS 回调，等待进入后续处理
    TIMEOUT = "TIMEOUT"  # 等待 WMS 回调超时
    FAILED = "FAILED"  # WMS 回调或等待流程明确失败
    NONE = "NONE"  # 当前没有料架操作等待
    UNKNOWN = "UNKNOWN"  # 等待状态无法从现有证据判断


class RuntimeResourceEvidenceKind(StrEnum):
    """运行时资源证据来源类型，用于区分快照、回调和 Trace 证据。"""

    WES_ACTIVE_SNAPSHOT = "WES_ACTIVE_SNAPSHOT"  # 来自 WES 当前活动资源快照
    WMS_CALLBACK_EVIDENCE = "WMS_CALLBACK_EVIDENCE"  # 来自 WMS 回调中的资源证据
    TRACE_RESOURCE_EVIDENCE = "TRACE_RESOURCE_EVIDENCE"  # 来自 Trace 关联证据链
    GENERIC_EVIDENCE = "GENERIC_EVIDENCE"  # 兜底资源证据，来源不属于专门分类
    UNKNOWN = "UNKNOWN"  # 无法识别证据来源类型


class RuntimeResourceKind(StrEnum):
    """运行时资源标识类型，用于统一料架、料盒、工位槽等资源编码。"""

    RACK = "RACK"  # 料架资源，用于承载或流转物料
    BIN = "BIN"  # 料盒资源，用于定位料架内的容器
    PKG = "PKG"  # 包装或物料包资源，用于追踪流转单元
    SLOT = "SLOT"  # 工位槽或料架槽位资源，用于表达位置占用
    CELL = "CELL"  # 料盒内单元格资源，用于表达更细粒度库存位置
    MAGAZINE = "MAGAZINE"  # magazine 类资源，用于兼容设备侧料仓/弹夹标识
    PART_SN = "PART_SN"  # 物料序列号资源，用于追踪具体实物件
    UNKNOWN = "UNKNOWN"  # 无法归类的资源标识


class RuntimeResourceEvidenceItem(BaseModel):
    resource_kind: RuntimeResourceKind
    resource_code: str
    display_label: str
    evidence_kind: RuntimeResourceEvidenceKind
    station_code: str | None = None
    position_code: str | None = None
    rack_code: str | None = None
    bin_code: str | None = None
    slot_code: str | None = None
    cell_code: str | None = None
    pkg_code: str | None = None
    part_sn: str | None = None
    material_code: str | None = None
    date_code: str | None = None
    lot_code: str | None = None
    reel_count: int | float | None = None
    reel_code: str | None = None
    position_index: int | float | None = None
    source_session_id: int | None = None
    source_trace_id: str | None = None
    occurred_at: datetime | None = None


class RuntimeWorklineDetailResponse(BaseModel):
    summary: RuntimeWorklineSummary
    workline_readiness: RuntimeWorklineReadiness = RuntimeWorklineReadiness.UNKNOWN
    station_lease: RuntimeStationLease = RuntimeStationLease.UNKNOWN
    single_layer_rack_snapshot: RuntimeSingleLayerRackSnapshot = RuntimeSingleLayerRackSnapshot.UNKNOWN
    rack_operation_wait: RuntimeRackOperationWait = RuntimeRackOperationWait.NONE
    resource_evidence_kind: RuntimeResourceEvidenceKind = RuntimeResourceEvidenceKind.UNKNOWN
    resource_evidence_items: list[RuntimeResourceEvidenceItem] = Field(default_factory=list)
    resource_evidence_total_count: int = 0
    resource_evidence_truncated: bool = False
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


class RuntimeActiveBinRackCellView(BaseModel):
    bin_cell_index: int | str | None = None
    bin_cell_code: str | None = None
    bin_cell_location: str | None = None
    status: str | None = None
    capacity_depth_mm: int | float | None = None
    used_depth_mm: int | float | None = None
    material_identity_key: str | None = None
    pkg_code: str | None = None
    is_reserved: bool | None = None


class RuntimeActiveBinRackBinView(BaseModel):
    rack_slot_code: str | None = None
    rack_slot_location_code: str | None = None
    bin_id: str | int | None = None
    bin_code: str | None = None
    bin_type: str | None = None
    bin_orientation_code: str | None = None
    cells: list[RuntimeActiveBinRackCellView] = Field(default_factory=list)


class RuntimeActiveBinRackView(BaseModel):
    rack_id: str | int | None = None
    rack_code: str | None = None
    rack_kind: str | None = None
    rack_type: str | None = None
    bins: list[RuntimeActiveBinRackBinView] = Field(default_factory=list)


class RuntimeTraceResourceView(BaseModel):
    active_bin_racks: list[RuntimeActiveBinRackView] = Field(default_factory=list)


class RuntimeTracePathResponse(BaseModel):
    workline_id: int | None = None
    session_id: int | None = None
    trace_id: str | None = None
    diagnosis_verdict: DiagnosisVerdictResponse
    sessions: list[TraceSessionItem] = Field(default_factory=list)
    resource_view: RuntimeTraceResourceView = Field(default_factory=RuntimeTraceResourceView)
    devices: list[RuntimeTraceDevicePathNode] = Field(default_factory=list)
    timeline_groups: list[RuntimeTraceTimelineGroup] = Field(default_factory=list)
    current_blocking_device_id: int | None = None
    blocking_reason: RuntimeBlockingReason | None = None


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
    open_command_count: int = 0
    pending_command_count: int = 0
    blocked_outbox_count: int = 0
    blocked_reason: str | None = None
    blocked_wait_seconds: int | None = None
    blocked_check_count: int | None = None
    blocked_detail_json: dict[str, Any] | None = None
    open_issue_count: int = 0
    active_runtime_hold_ids: list[int] = Field(default_factory=list)
    last_heartbeat_at: datetime | None = None
    recent_callback_at: datetime | None = None
    error_code: str | None = None


class RuntimeDeviceDetailResponse(BaseModel):
    summary: RuntimeDeviceSummary
    recent_commands: list[TraceCommandItem] = Field(default_factory=list)
    recent_callbacks: list[TraceCallbackLogItem] = Field(default_factory=list)
    active_sessions: list[RuntimeTraceListItem] = Field(default_factory=list)


class RuntimeMonitorCommandSnapshot(BaseModel):
    """运行监控视图中的设备当前指令快照。

    字段固定，专供 dashboard ECS ACK 链消费；不引入业务流转字段。
    """

    id: int
    command_code: str
    status: str
    sent_at: datetime | None = None
    ack_received_at: datetime | None = None
    ack_code: int | None = None
    ack_message: str | None = None


class RuntimeMonitorDeviceNode(BaseModel):
    id: int
    device_code: str
    device_name: str
    device_role: str
    role_index: int
    upstream_device_id: int | None = None
    device_status: str
    maintenance_mode: bool = False
    current_command_id: int | None = None
    current_command: RuntimeMonitorCommandSnapshot | None = None
    open_command_count: int = 0
    pending_command_count: int = 0
    blocked_outbox_count: int = 0
    blocked_reason: str | None = None
    blocked_wait_seconds: int | None = None
    blocked_check_count: int | None = None
    open_issue_count: int = 0
    active_runtime_hold_ids: list[int] = Field(default_factory=list)
    last_heartbeat_at: datetime | None = None
    error_code: str | None = None


class RuntimeMonitorSessionItem(BaseModel):
    session_id: int
    session_code: str
    trace_id: str | None = None
    request_id: str | None = None
    last_inbox_id: int | None = None
    barcode: str | None = None
    workline_id: int
    device_id: int | None = None
    device_name: str | None = None
    device_code: str | None = None
    status: str
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


class RuntimeMonitorTraceItem(BaseModel):
    session_id: int
    session_code: str
    trace_id: str | None = None
    request_id: str | None = None
    barcode: str | None = None
    workline_id: int
    device_id: int | None = None
    device_name: str | None = None
    device_code: str | None = None
    status: str
    failure_domain: str | None = None
    failure_code: str | None = None
    latest_timeline_action: str | None = None
    latest_timeline_status: str | None = None
    latest_timeline_message: str | None = None
    started_at: datetime | None = None
    last_ingress_at: datetime | None = None
    deadline_at: datetime | None = None
    is_timed_out: bool = False


class RuntimeMonitorSessionSection(BaseModel):
    items: list[RuntimeMonitorSessionItem] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


class RuntimeMonitorTraceSection(BaseModel):
    items: list[RuntimeMonitorTraceItem] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


class RuntimeMonitorEvidenceSection(BaseModel):
    kind: RuntimeResourceEvidenceKind = RuntimeResourceEvidenceKind.UNKNOWN
    items: list[RuntimeResourceEvidenceItem] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


class RuntimeMonitorReconciliationCandidate(BaseModel):
    session_id: int
    session_code: str
    trace_id: str | None = None
    request_id: str | None = None
    reason: str
    source_kind: str
    device_id: int | None = None
    command_id: int | None = None
    wait_token: str | None = None
    occurred_at: datetime
    deadline_at: datetime | None = None
    late_evidence_received: bool = False


class RuntimeMonitorActionCandidates(BaseModel):
    pending_reconciliation: RuntimeMonitorReconciliationCandidate | None = None


class RuntimeWorklineBoundary(BaseModel):
    workline_readiness: RuntimeWorklineReadiness = RuntimeWorklineReadiness.UNKNOWN
    station_lease: RuntimeStationLease = RuntimeStationLease.UNKNOWN
    single_layer_rack_snapshot: RuntimeSingleLayerRackSnapshot = RuntimeSingleLayerRackSnapshot.UNKNOWN
    rack_operation_wait: RuntimeRackOperationWait = RuntimeRackOperationWait.NONE


class RuntimeWorklineMonitorProjectionResponse(BaseModel):
    summary: RuntimeWorklineSummary
    boundary: RuntimeWorklineBoundary
    device_nodes: list[RuntimeMonitorDeviceNode] = Field(default_factory=list)
    active_sessions: RuntimeMonitorSessionSection
    recent_failed_traces: RuntimeMonitorTraceSection
    recent_completed_traces: RuntimeMonitorTraceSection
    resource_evidence: RuntimeMonitorEvidenceSection
    action_candidates: RuntimeMonitorActionCandidates
    generated_at: datetime


_ = RuntimeOverviewResponse.model_rebuild()
_ = RuntimeWorklineMonitorProjectionResponse.model_rebuild()
