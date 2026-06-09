"""IntegrationDebugService 测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from src.app.workline.services.integration_debug_service import IntegrationDebugService
from src.app.workline.services.trace_query_service import TraceQueryResult
from src.workline_runtime.diagnostics import build_diagnostic_context
from src.workline_runtime.trace_context import TraceContext


class _EmptyTraceQuery:
    def __init__(self, *, include_fallback_diagnostic: bool = False) -> None:
        self.include_fallback_diagnostic = include_fallback_diagnostic

    async def by_trace_id(self, _db: Any, trace_id: str) -> TraceQueryResult:
        return self._empty_result(trace_id)

    async def by_request_id(self, _db: Any, request_id: str) -> TraceQueryResult:
        return self._empty_result(request_id)

    async def by_session_id(self, _db: Any, session_id: int) -> TraceQueryResult:
        return self._empty_result(str(session_id))

    async def by_command_code(self, _db: Any, command_code: str) -> TraceQueryResult:
        return self._empty_result(command_code)

    async def by_dispatch_key(self, _db: Any, dispatch_key: str) -> TraceQueryResult:
        return self._empty_result(dispatch_key)

    def _empty_result(self, trace_id: str) -> TraceQueryResult:
        trace = TraceContext.from_request(trace_id=trace_id)
        diagnostics = [build_diagnostic_context(trace=trace)] if self.include_fallback_diagnostic else []
        return TraceQueryResult(trace=trace, diagnostics=diagnostics)


class _PagedRuntimeQuery:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    async def get_trace_list(self, _db: Any, request: Any) -> Any:
        self.offsets.append(cast("int", request.offset))
        if request.offset == 0:
            items = [
                SimpleNamespace(
                    session_id=index,
                    session_code=f"SES-FUZZY-{index}",
                    barcode=f"MAT-{index}",
                    business_key=f"BIZ-{index}",
                )
                for index in range(20)
            ]
            return SimpleNamespace(total=21, items=items)
        return SimpleNamespace(
            total=21,
            items=[
                SimpleNamespace(
                    session_id=99,
                    session_code="SES-EXACT",
                    barcode="MAT-EXACT",
                    business_key="BIZ-EXACT",
                )
            ],
        )


class _RecordingTraceQuery(_EmptyTraceQuery):
    def __init__(self) -> None:
        super().__init__()
        self.session_ids: list[int] = []

    async def by_session_id(self, _db: Any, session_id: int) -> TraceQueryResult:
        self.session_ids.append(session_id)
        return TraceQueryResult(
            trace=TraceContext.from_request(trace_id=f"trace-{session_id}"),
            session=cast(
                "Any",
                SimpleNamespace(
                    id=session_id,
                    session_code="SES-EXACT",
                    trace_id=f"trace-{session_id}",
                    workline_id=22,
                    status="RUNNING",
                    failure_domain=None,
                    failure_code=None,
                    failure_message=None,
                    business_key="BIZ-EXACT",
                    barcode="MAT-EXACT",
                ),
            ),
        )


class _LatestCasesRuntimeQuery:
    async def get_trace_list(self, _db: Any, _request: Any) -> Any:
        return SimpleNamespace(total=1, items=[SimpleNamespace(session_id=41)])


class _FullLatestCasesRuntimeQuery:
    async def get_trace_list(self, _db: Any, request: Any) -> Any:
        return SimpleNamespace(
            total=2,
            items=[SimpleNamespace(session_id=41), SimpleNamespace(session_id=42)][: request.limit],
        )


class _LatestCasesTraceQuery(_EmptyTraceQuery):
    async def by_session_id(self, _db: Any, session_id: int) -> TraceQueryResult:
        return TraceQueryResult(
            trace=TraceContext.from_request(trace_id="trace-active"),
            session=cast(
                "Any",
                SimpleNamespace(
                    id=session_id,
                    session_code="SES-ACTIVE",
                    trace_id="trace-active",
                    workline_id=1,
                    status="WAITING_DEVICE_RESULT",
                    failure_domain=None,
                    failure_code=None,
                    failure_message=None,
                    business_key="BUSY",
                    barcode=None,
                ),
            ),
        )


def test_build_case_identifies_wms_timeout_after_device_command_completed() -> None:
    service = IntegrationDebugService()
    result = TraceQueryResult(
        trace=TraceContext.from_request(request_id="req-1", trace_id="trace-1"),
        session=cast(
            "Any",
            SimpleNamespace(
                id=11,
                session_code="SES-1",
                trace_id="trace-1",
                workline_id=22,
                status="MANUAL_HOLD",
                failure_domain="INTEGRATION",
                failure_code="WMS_TIMEOUT",
                failure_message="WMS 同步调用超时",
                business_key="biz-1",
                barcode="MAT-1",
            ),
        ),
        commands=[
            cast(
                "Any",
                SimpleNamespace(
                    id=33,
                    command_code="CMD-1",
                    status="COMPLETED",
                    ack_received_at=1,
                    completed_at=2,
                    device_id=77,
                ),
            )
        ],
        timelines=[
            cast(
                "Any",
                SimpleNamespace(
                    id=66,
                    session_id=11,
                    workline_id=22,
                    seq_no=8,
                    action_type="MANUAL_HOLD_CREATED",
                    status="BLOCKED",
                    message="WMS 同步调用超时",
                    payload_json={
                        "reason_code": "WMS_TIMEOUT",
                        "target_code": "WMS_INVENTORY",
                        "block_scope": "MATERIAL",
                        "suggested_action": "人工检查粗分机当前物料与依赖状态",
                    },
                ),
            )
        ],
    )

    case = service.build_case(result, include_raw=False)

    assert case.case_id == "session:11"
    assert case.phase == "external_wms"
    assert case.verdict == "blocked"
    assert case.blocking_code == "WMS_TIMEOUT"
    assert case.summary == "设备链路已完成，当前阻塞在 WMS 库存同步超时"
    assert case.facts["command_completed"] is True
    assert [stage.key for stage in case.stage_checks if stage.state == "blocked"] == ["external_wms"]
    assert case.next_actions[0].kind == "inspect_wms_inventory"


def test_build_case_does_not_treat_non_timeout_wms_inventory_hold_as_timeout() -> None:
    service = IntegrationDebugService()
    result = TraceQueryResult(
        trace=TraceContext.from_request(request_id="req-1", trace_id="trace-1"),
        session=cast(
            "Any",
            SimpleNamespace(
                id=11,
                session_code="SES-1",
                trace_id="trace-1",
                workline_id=22,
                status="MANUAL_HOLD",
                failure_domain="INTEGRATION",
                failure_code="WMS_UNAVAILABLE",
                failure_message="WMS 依赖不可用",
                business_key="biz-1",
                barcode="MAT-1",
            ),
        ),
        commands=[
            cast(
                "Any",
                SimpleNamespace(
                    id=33,
                    command_code="CMD-1",
                    status="COMPLETED",
                    ack_received_at=1,
                    completed_at=2,
                    device_id=77,
                ),
            )
        ],
        timelines=[
            cast(
                "Any",
                SimpleNamespace(
                    id=66,
                    session_id=11,
                    workline_id=22,
                    seq_no=8,
                    action_type="MANUAL_HOLD_CREATED",
                    status="BLOCKED",
                    message="WMS 依赖不可用",
                    payload_json={
                        "reason_code": "WMS_UNAVAILABLE",
                        "target_code": "WMS_INVENTORY",
                        "block_scope": "MATERIAL",
                    },
                ),
            )
        ],
    )

    case = service.build_case(result, include_raw=False)

    assert case.phase != "external_wms"
    assert case.blocking_code == "WMS_UNAVAILABLE"


def test_build_case_identifies_resource_reconciliation_hold() -> None:
    service = IntegrationDebugService()
    result = TraceQueryResult(
        trace=TraceContext.from_request(request_id="req-1", trace_id="trace-resource"),
        session=cast(
            "Any",
            SimpleNamespace(
                id=42,
                session_code="SES-RESOURCE",
                trace_id="trace-resource",
                workline_id=22,
                status="MANUAL_HOLD",
                failure_domain="RESOURCE_RECONCILIATION",
                failure_code="RACK_BIN_MOUNT_CONFLICT",
                failure_message="货架槽位或料箱已有 active 挂载",
                business_key="biz-1",
                barcode="PKG-1",
            ),
        ),
        commands=[
            cast(
                "Any",
                SimpleNamespace(
                    id=33,
                    command_code="CMD-1",
                    status="COMPLETED",
                    ack_received_at=1,
                    completed_at=2,
                    device_id=77,
                ),
            )
        ],
        timelines=[
            cast(
                "Any",
                SimpleNamespace(
                    id=66,
                    session_id=42,
                    workline_id=22,
                    seq_no=8,
                    action_type="MANUAL_HOLD",
                    status="PENDING",
                    message="货架槽位或料箱已有 active 挂载",
                    payload_json={
                        "reason_code": "RACK_BIN_MOUNT_CONFLICT",
                        "block_scope": "RESOURCE_RECONCILIATION",
                    },
                ),
            )
        ],
    )

    case = service.build_case(result, include_raw=False)

    assert case.phase == "resource_reconciliation"
    assert case.verdict == "blocked"
    assert case.blocking_domain == "RESOURCE_RECONCILIATION"
    assert case.blocking_code == "RACK_BIN_MOUNT_CONFLICT"
    assert case.summary == "资源投影进入调和状态，需处理货架/料箱/物料占用冲突"
    assert [stage.key for stage in case.stage_checks if stage.state == "blocked"] == ["resource_reconciliation"]
    assert case.next_actions[0].kind == "inspect_resource_hold"


def test_build_case_does_not_treat_completed_session_history_as_wms_block() -> None:
    service = IntegrationDebugService()
    result = TraceQueryResult(
        trace=TraceContext.from_request(request_id="req-1", trace_id="trace-1"),
        session=cast(
            "Any",
            SimpleNamespace(
                id=11,
                session_code="SES-1",
                trace_id="trace-1",
                workline_id=22,
                status="COMPLETED",
                failure_domain="INTEGRATION",
                failure_code="WMS_TIMEOUT",
                failure_message="历史 WMS 同步调用超时",
                business_key="biz-1",
                barcode="MAT-1",
            ),
        ),
        commands=[
            cast(
                "Any",
                SimpleNamespace(
                    id=33,
                    command_code="CMD-1",
                    status="COMPLETED",
                    ack_received_at=1,
                    completed_at=2,
                    device_id=77,
                ),
            )
        ],
        timelines=[
            cast(
                "Any",
                SimpleNamespace(
                    id=66,
                    session_id=11,
                    workline_id=22,
                    seq_no=8,
                    action_type="MANUAL_HOLD_CREATED",
                    status="BLOCKED",
                    message="历史 WMS 同步调用超时",
                    payload_json={
                        "reason_code": "WMS_TIMEOUT",
                        "target_code": "WMS_INVENTORY",
                        "block_scope": "MATERIAL",
                    },
                ),
            )
        ],
    )

    case = service.build_case(result, include_raw=False)

    assert case.phase == "terminal_state"
    assert case.verdict == "ok"
    assert case.blocking_code is None
    assert case.facts["wms_reason_code"] is None
    assert [stage.key for stage in case.stage_checks if stage.state == "blocked"] == []


async def test_lookup_case_returns_none_when_anchor_has_no_evidence() -> None:
    service = IntegrationDebugService(trace_query=cast("Any", _EmptyTraceQuery()))
    db: Any = object()

    case = await service.lookup_case(db, anchor_type="trace_id", anchor="missing")

    assert case is None


async def test_lookup_case_finds_exact_anchor_after_first_trace_list_page() -> None:
    runtime_query = _PagedRuntimeQuery()
    trace_query = _RecordingTraceQuery()
    service = IntegrationDebugService(trace_query=cast("Any", trace_query), runtime_query=cast("Any", runtime_query))
    db: Any = object()

    case = await service.lookup_case(db, anchor_type="session_code", anchor="SES-EXACT")

    assert case is not None
    assert case.session_id == 99
    assert runtime_query.offsets == [0, 20]
    assert trace_query.session_ids == [99]


async def test_lookup_case_ignores_empty_fallback_diagnostic() -> None:
    service = IntegrationDebugService(trace_query=cast("Any", _EmptyTraceQuery(include_fallback_diagnostic=True)))
    db: Any = object()

    case = await service.lookup_case(db, anchor_type="trace_id", anchor="missing")

    assert case is None


async def test_latest_cases_only_uses_real_trace_backlog() -> None:
    service = IntegrationDebugService(
        trace_query=cast("Any", _LatestCasesTraceQuery()),
        runtime_query=cast("Any", _LatestCasesRuntimeQuery()),
    )

    result = await service.latest_cases(
        cast("Any", object()),
        workline_id=1,
        limit=10,
    )

    assert result.total == 1
    assert [item.case_id for item in result.items] == ["session:41"]
    legacy_code = "WORKLINE_ENTRY_ADMISSION_BLOCKED"
    assert all(item.blocking_code != legacy_code for item in result.items)


async def test_latest_cases_does_not_replace_full_trace_page_with_synthetic_backlog() -> None:
    service = IntegrationDebugService(
        trace_query=cast("Any", _LatestCasesTraceQuery()),
        runtime_query=cast("Any", _FullLatestCasesRuntimeQuery()),
    )

    result = await service.latest_cases(cast("Any", object()), workline_id=1, limit=2)

    assert result.total == 2
    assert [item.case_id for item in result.items] == ["session:41", "session:42"]
    legacy_code = "WORKLINE_ENTRY_ADMISSION_BLOCKED"
    assert all(item.blocking_code != legacy_code for item in result.items)
