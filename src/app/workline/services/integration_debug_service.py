"""非生产集成调试案件定位服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from src.app.runtime.orchestration.consumers.diagnostics_bridge import ErrorCode, get_diagnostic_code_definition
from src.app.workline.models.integration_debug import (
    IntegrationDebugCaseListResponse,
    IntegrationDebugCaseResponse,
    IntegrationDebugEvidenceLink,
    IntegrationDebugNextAction,
    IntegrationDebugStageCheck,
)
from src.app.workline.models.runtime import TraceQueryRequest
from src.app.workline.services.runtime_query_service import RuntimeQueryService, runtime_query_service
from src.app.workline.services.trace_query_service import TraceQueryResult, trace_query_service
from src.app.workline.services.trace_response_builder import build_trace_response
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import coerce_optional_str, optional_enum_str

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class _TraceQueryProtocol(Protocol):
    async def by_trace_id(self, db: Any, trace_id: str) -> TraceQueryResult: ...

    async def by_request_id(self, db: Any, request_id: str) -> TraceQueryResult: ...

    async def by_session_id(self, db: Any, session_id: int) -> TraceQueryResult: ...

    async def by_command_code(self, db: Any, command_code: str) -> TraceQueryResult: ...

    async def by_dispatch_key(self, db: Any, dispatch_key: str) -> TraceQueryResult: ...


class IntegrationDebugService:
    """把 Trace 证据归纳成现场可读的集成调试案件。"""

    def __init__(
        self,
        *,
        trace_query: _TraceQueryProtocol | None = None,
        runtime_query: RuntimeQueryService | None = None,
    ) -> None:
        self._trace_query = trace_query or trace_query_service
        self._runtime_query = runtime_query or runtime_query_service

    async def latest_cases(
        self,
        db: AsyncSession,
        *,
        limit: int = 10,
        workline_id: int | None = None,
        device_id: int | None = None,
        status: str | None = None,
    ) -> IntegrationDebugCaseListResponse:
        trace_list = await self._runtime_query.get_trace_list(
            db,
            TraceQueryRequest(
                workline_id=workline_id,
                device_id=device_id,
                status=status,
                limit=limit,
                offset=0,
            ),
        )
        items: list[IntegrationDebugCaseResponse] = []
        for item in trace_list.items:
            result = await self._trace_query.by_session_id(db, item.session_id)
            items.append(self.build_case(result, include_raw=False))

        return IntegrationDebugCaseListResponse(total=trace_list.total, items=items[:limit])

    async def lookup_case(
        self,
        db: AsyncSession,
        *,
        anchor_type: str,
        anchor: str,
        include_raw: bool = False,
    ) -> IntegrationDebugCaseResponse | None:
        result = await self._lookup_trace_result(db, anchor_type=anchor_type, anchor=anchor)
        if result is None or not self._has_case_evidence(result):
            return None
        return self.build_case(result, include_raw=include_raw)

    def build_case(self, result: TraceQueryResult, *, include_raw: bool) -> IntegrationDebugCaseResponse:
        session = result.session
        trace = result.trace
        # session 可能为 None (query miss), 走占位 session 状态。
        status = optional_enum_str(getattr(session, "status", None)) or "UNKNOWN"
        detected_wms_block = _wms_timeout_block(result)
        wms_block = detected_wms_block if status == "MANUAL_HOLD" else None
        detected_resource_block = _resource_reconciliation_block(result)
        resource_block = detected_resource_block if status == "MANUAL_HOLD" else None
        failed_outbox = next(
            (item for item in result.outboxes if optional_enum_str(getattr(item, "status", None)) == "FAILED"), None
        )
        failed_command = next(
            (item for item in result.commands if optional_enum_str(getattr(item, "status", None)) == "FAILED"), None
        )

        if wms_block is not None:
            phase = "external_wms"
            verdict = "blocked"
            blocking_domain = "INTEGRATION"
            blocking_code = "WMS_TIMEOUT"
            owner = "integration"
            severity = "error"
            definition = get_diagnostic_code_definition(ErrorCode.WMS_TIMEOUT)
            recoverability = definition.recoverability.value
            summary = "设备链路已完成，当前阻塞在 WMS 库存同步超时"
        elif resource_block is not None:
            _, resource_payload = resource_block
            phase = "resource_reconciliation"
            verdict = "blocked"
            blocking_domain = "RESOURCE_RECONCILIATION"
            blocking_code = coerce_optional_str(resource_payload.get("reason_code")) or coerce_optional_str(
                getattr(session, "failure_code", None)
            )
            owner = "runtime"
            severity = "error"
            recoverability = "manual_retryable"
            summary = "资源投影进入调和状态，需处理货架/料箱/物料占用冲突"
        elif failed_outbox is not None:
            phase = "command_dispatched"
            verdict = "failed"
            blocking_domain = "INTEGRATION"
            blocking_code = ErrorCode.OUTBOX_DISPATCH_FAILED.value
            owner = "integration"
            severity = "error"
            definition = get_diagnostic_code_definition(ErrorCode.OUTBOX_DISPATCH_FAILED)
            recoverability = definition.recoverability.value
            summary = "命令未成功派发，当前阻塞在 Outbox 派发链路"
        elif failed_command is not None:
            phase = "command_result"
            verdict = "failed"
            blocking_domain = "DEVICE"
            blocking_code = ErrorCode.DEVICE_TIMEOUT.value
            owner = "device"
            severity = "error"
            definition = get_diagnostic_code_definition(ErrorCode.DEVICE_TIMEOUT)
            recoverability = definition.recoverability.value
            summary = "设备命令失败或超时，需检查设备执行状态"
        elif status == "COMPLETED":
            phase = "terminal_state"
            verdict = "ok"
            blocking_domain = None
            blocking_code = None
            owner = "runtime"
            severity = "info"
            recoverability = "auto_retryable"
            summary = "集成链路已完成，未发现阻塞点"
        else:
            phase = "terminal_state"
            verdict = (
                "waiting" if status in {"NEW", "RUNNING", "WAITING_DEVICE_RESULT", "WAITING_EXTERNAL"} else "unknown"
            )
            blocking_domain = coerce_optional_str(getattr(session, "failure_domain", None))
            blocking_code = coerce_optional_str(getattr(session, "failure_code", None))
            owner = "runtime"
            severity = "warning"
            recoverability = "manual_retryable"
            summary = "当前证据不足，需继续查看 Trace 明细"

        return IntegrationDebugCaseResponse(
            case_id=_case_id(result),
            session_id=getattr(session, "id", None),
            session_code=coerce_optional_str(getattr(session, "session_code", None)),
            trace_id=trace.trace_id,
            request_id=trace.request_id,
            command_code=_latest_command_code(result),
            status=status,
            phase=phase,
            verdict=verdict,
            blocking_domain=blocking_domain,
            blocking_code=blocking_code,
            owner=owner,
            severity=severity,
            recoverability=recoverability,
            summary=summary,
            facts=_facts(result, wms_block=wms_block, resource_block=resource_block),
            stage_checks=_stage_checks(
                result,
                phase=phase,
                verdict=verdict,
                wms_block=wms_block,
                resource_block=resource_block,
            ),
            evidence_links=_evidence_links(result),
            next_actions=_next_actions(result, phase=phase, wms_block=wms_block, resource_block=resource_block),
            trace_detail=build_trace_response(result) if include_raw else None,
        )

    async def _lookup_trace_result(
        self,
        db: AsyncSession,
        *,
        anchor_type: str,
        anchor: str,
    ) -> TraceQueryResult | None:
        normalized_type = anchor_type.strip().lower()
        normalized_anchor = anchor.strip()
        if not normalized_anchor:
            return None
        if normalized_type == "session_id":
            if not normalized_anchor.isdigit():
                return None
            return await self._trace_query.by_session_id(db, int(normalized_anchor))
        trace_lookup_method_names = {
            "trace_id": "by_trace_id",
            "request_id": "by_request_id",
            "command_code": "by_command_code",
            "dispatch_key": "by_dispatch_key",
        }
        lookup_method_name = trace_lookup_method_names.get(normalized_type)
        if lookup_method_name is not None:
            lookup_method = getattr(self._trace_query, lookup_method_name)
            return await lookup_method(db, normalized_anchor)
        if normalized_type in {"session_code", "barcode", "business_key"}:
            return await self._lookup_by_trace_list(db, anchor_type=normalized_type, anchor=normalized_anchor)
        return None

    async def _lookup_by_trace_list(
        self,
        db: AsyncSession,
        *,
        anchor_type: str,
        anchor: str,
    ) -> TraceQueryResult | None:
        page_size = 20
        offset = 0
        while True:
            trace_list = await self._runtime_query.get_trace_list(
                db,
                TraceQueryRequest(keyword=anchor, limit=page_size, offset=offset),
            )
            for item in trace_list.items:
                if anchor_type == "session_code":
                    candidate = cast("str | None", getattr(item, "session_code", None))
                elif anchor_type == "barcode":
                    candidate = cast("str | None", getattr(item, "barcode", None))
                else:
                    candidate = cast("str | None", getattr(item, "business_key", None))
                if candidate == anchor:
                    return await self._trace_query.by_session_id(db, item.session_id)

            item_count = len(trace_list.items)
            if item_count == 0 or offset + item_count >= trace_list.total:
                return None
            offset += item_count

    @staticmethod
    def _has_case_evidence(result: TraceQueryResult) -> bool:
        if any(
            [
                result.session is not None,
                result.callback_logs,
                result.inboxes,
                result.commands,
                result.outboxes,
                result.timelines,
            ]
        ):
            return True
        # TraceQueryService 会为完全空的查询补一个兜底 DiagnosticContext；
        # lookup 语义只应把真实持久化诊断当作案件证据。
        return any(diagnostic.extra.get("source") == "workline_diagnostic" for diagnostic in result.diagnostics)


def _case_id(result: TraceQueryResult) -> str:
    session_id = getattr(result.session, "id", None)
    if session_id is not None:
        return f"session:{session_id}"
    if result.trace.trace_id:
        return f"trace:{result.trace.trace_id}"
    return "trace:unknown"


def _latest_command_code(result: TraceQueryResult) -> str | None:
    if not result.commands:
        return result.trace.command_code
    return coerce_optional_str(getattr(result.commands[-1], "command_code", None)) or result.trace.command_code


def _wms_timeout_block(result: TraceQueryResult) -> tuple[Any, dict[str, Any]] | None:
    session = result.session
    session_failure_code = optional_enum_str(getattr(session, "failure_code", None)) if session is not None else None
    session_status = optional_enum_str(getattr(session, "status", None)) if session is not None else None
    if session_failure_code != "WMS_TIMEOUT" and session_status != "MANUAL_HOLD":
        return None
    for timeline in reversed(result.timelines):
        payload = payload_dict(getattr(timeline, "payload_json", None))
        reason_code = coerce_optional_str(payload.get("reason_code"))
        if reason_code == "WMS_TIMEOUT":
            return timeline, payload
    if session_failure_code == "WMS_TIMEOUT":
        return None, {"reason_code": "WMS_TIMEOUT", "target_code": "WMS_INVENTORY"}
    return None


def _resource_reconciliation_block(result: TraceQueryResult) -> tuple[Any, dict[str, Any]] | None:
    session = result.session
    session_failure_domain = (
        coerce_optional_str(getattr(session, "failure_domain", None)) if session is not None else None
    )
    session_failure_code = coerce_optional_str(getattr(session, "failure_code", None)) if session is not None else None
    if session_failure_domain != "RESOURCE_RECONCILIATION":
        return None
    for timeline in reversed(result.timelines):
        payload = payload_dict(getattr(timeline, "payload_json", None))
        reason_code = coerce_optional_str(payload.get("reason_code"))
        if reason_code == session_failure_code:
            return timeline, payload
    return None, {"reason_code": session_failure_code}


def _facts(
    result: TraceQueryResult,
    *,
    wms_block: tuple[Any, dict[str, Any]] | None,
    resource_block: tuple[Any, dict[str, Any]] | None,
) -> dict[str, Any]:
    session = result.session
    _, wms_payload = wms_block or (None, {})
    _, resource_payload = resource_block or (None, {})
    completed_commands = [
        getattr(command, "command_code", None)
        for command in result.commands
        if optional_enum_str(getattr(command, "status", None)) == "COMPLETED"
    ]
    acked_commands = [
        getattr(command, "command_code", None)
        for command in result.commands
        if getattr(command, "ack_received_at", None) is not None
        or optional_enum_str(getattr(command, "status", None)) in {"ACK_RECEIVED", "COMPLETED"}
    ]
    return {
        "session_id": getattr(session, "id", None),
        "session_code": getattr(session, "session_code", None),
        "workline_id": getattr(session, "workline_id", None) or result.trace.workline_id,
        "business_key": getattr(session, "business_key", None),
        "barcode": getattr(session, "barcode", None),
        "trace_id": result.trace.trace_id,
        "request_id": result.trace.request_id,
        "command_code": _latest_command_code(result),
        "command_acked": bool(acked_commands),
        "command_completed": bool(completed_commands),
        "wms_reason_code": wms_payload.get("reason_code"),
        "wms_target_code": wms_payload.get("target_code"),
        "wms_block_scope": wms_payload.get("block_scope"),
        "resource_reason_code": resource_payload.get("reason_code"),
        "resource_block_scope": resource_payload.get("block_scope"),
    }


def _stage_checks(
    result: TraceQueryResult,
    *,
    phase: str,
    verdict: str,
    wms_block: tuple[Any, dict[str, Any]] | None,
    resource_block: tuple[Any, dict[str, Any]] | None,
) -> list[IntegrationDebugStageCheck]:
    has_completed_command = any(
        optional_enum_str(getattr(command, "status", None)) == "COMPLETED" for command in result.commands
    )
    has_acked_command = any(
        getattr(command, "ack_received_at", None) is not None
        or optional_enum_str(getattr(command, "status", None)) in {"ACK_RECEIVED", "COMPLETED"}
        for command in result.commands
    )
    has_failed_outbox = any(
        optional_enum_str(getattr(outbox, "status", None)) == "FAILED" for outbox in result.outboxes
    )
    has_failed_command = any(
        optional_enum_str(getattr(command, "status", None)) == "FAILED" for command in result.commands
    )
    return [
        _stage(
            "callback_event",
            "回调入口",
            "ok" if result.callback_logs or result.inboxes else "unknown",
            len(result.callback_logs),
        ),
        _stage(
            "session_created",
            "Session 创建",
            "ok" if result.session is not None else "unknown",
            1 if result.session else 0,
        ),
        _stage(
            "command_dispatched",
            "命令派发",
            "failed" if has_failed_outbox else "ok" if result.outboxes or result.commands else "not_started",
            len(result.outboxes),
        ),
        _stage(
            "device_ack",
            "设备 ACK",
            "ok"
            if has_acked_command
            else "failed"
            if has_failed_outbox
            else "waiting"
            if result.commands
            else "not_started",
            len(result.commands),
        ),
        _stage(
            "command_result",
            "执行结果",
            "failed"
            if has_failed_command
            else "ok"
            if has_completed_command
            else "waiting"
            if has_acked_command
            else "not_started",
            len(result.commands),
        ),
        _stage("plugin_decision", "插件决策", "ok" if result.timelines else "unknown", len(result.timelines)),
        _stage(
            "external_wms",
            "WMS 同步",
            "blocked" if wms_block is not None else "unknown" if has_completed_command else "not_started",
            1 if wms_block is not None else 0,
        ),
        _stage(
            "resource_reconciliation",
            "资源调和",
            "blocked" if resource_block is not None else "not_started",
            1 if resource_block is not None else 0,
        ),
        _stage(
            "terminal_state",
            "终态",
            "waiting" if phase in {"external_wms", "resource_reconciliation"} else verdict,
            1 if result.session is not None else 0,
        ),
    ]


def _stage(key: str, label: str, state: str, evidence_count: int) -> IntegrationDebugStageCheck:
    return IntegrationDebugStageCheck(key=key, label=label, state=state, evidence_count=evidence_count)


def _evidence_links(result: TraceQueryResult) -> list[IntegrationDebugEvidenceLink]:
    links: list[IntegrationDebugEvidenceLink] = []
    if result.trace.trace_id:
        links.append(
            IntegrationDebugEvidenceLink(
                kind="trace",
                label="Trace 详情",
                api_path=f"/api/v1/workline/trace/trace/{result.trace.trace_id}",
                route_name="RuntimeTraces",
                route_query={"type": "trace", "value": result.trace.trace_id},
            )
        )
    session_id = getattr(result.session, "id", None)
    if session_id is not None:
        links.append(
            IntegrationDebugEvidenceLink(
                kind="session_path",
                label="Session 路径",
                api_path=f"/api/v1/workline/runtime/sessions/{session_id}/path",
            )
        )
    command_code = _latest_command_code(result)
    if command_code:
        links.append(
            IntegrationDebugEvidenceLink(
                kind="command",
                label="命令证据",
                api_path=f"/api/v1/workline/trace/command/{command_code}",
                route_name="RuntimeTraces",
                route_query={"type": "command", "value": command_code},
            )
        )
    return links


def _next_actions(
    result: TraceQueryResult,
    *,
    phase: str,
    wms_block: tuple[Any, dict[str, Any]] | None,
    resource_block: tuple[Any, dict[str, Any]] | None,
) -> list[IntegrationDebugNextAction]:
    if phase == "external_wms":
        _, payload = wms_block or (None, {})
        return [
            IntegrationDebugNextAction(
                kind="inspect_wms_inventory",
                label="检查 WMS 库存同步",
                description=coerce_optional_str(payload.get("suggested_action"))
                or "检查 WMS_INVENTORY 请求、WMS 服务和网络链路。",
            ),
            IntegrationDebugNextAction(
                kind="open_trace",
                label="打开 Trace 证据",
                description="查看 callback、command、timeline 的完整证据链。",
                route_name="RuntimeTraces",
                route_query={"type": "trace", "value": result.trace.trace_id},
            ),
        ]
    if phase == "resource_reconciliation":
        _, payload = resource_block or (None, {})
        reason_code = coerce_optional_str(payload.get("reason_code")) or "RESOURCE_RECONCILIATION"
        return [
            IntegrationDebugNextAction(
                kind="inspect_resource_hold",
                label="检查资源调和 Hold",
                description=f"处理 {reason_code} 对应的货架、料箱或物料 active 投影冲突。",
            ),
            IntegrationDebugNextAction(
                kind="open_trace",
                label="打开 Trace 证据",
                description="查看资源事实、runtime hold 和 session 状态的完整证据链。",
                route_name="RuntimeTraces",
                route_query={"type": "trace", "value": result.trace.trace_id},
            ),
        ]
    return [
        IntegrationDebugNextAction(
            kind="open_trace",
            label="打开 Trace 证据",
            description="继续查看 Trace 明细定位阻塞阶段。",
            route_name="RuntimeTraces",
            route_query={"type": "trace", "value": result.trace.trace_id},
        )
    ]


integration_debug_service = IntegrationDebugService()


__all__ = ["IntegrationDebugService", "integration_debug_service"]
