"""Trace 查询响应构建器。"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit

from src.app.workline.models.runtime import (
    TraceCallbackLogItem,
    TraceCommandItem,
    TraceContextResponse,
    TraceDetailResponse,
    TraceDiagnosticItem,
    TraceDispatchAttemptItem,
    TraceInboxItem,
    TraceOutboxItem,
    TraceOverviewSummary,
    TraceResourceEvidenceResponse,
    TraceSessionItem,
    TraceTimelineItem,
)
from src.app.workline.models.runtime_hold_api import FailedCommandEvidence
from src.utils.timezone import timezone
from src.utils.value_normalization import enum_value, optional_enum_str

_RESOURCE_WAIT_DETAIL_KEYS = {
    "device_code",
    "status_url",
    "observed_mode",
    "observed_status",
    "observed_current_command_id",
    "http_status",
    "error_kind",
    "error_message",
    "last_probe_result",
    "ttl_seconds",
    "max_check_count",
    "escalated_at",
    "diagnostic_key",
    "waited_seconds",
}
_STATUS_URL_QUERY_ALLOWLIST = {"device_code"}


def _status_str(value: Any) -> str:
    return optional_enum_str(value) or "UNKNOWN"


def _resource_evidence_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        payload = item.model_dump(mode="json")
    elif hasattr(item, "__dict__"):
        payload = vars(item)
    else:
        return {}
    return {key: enum_value(value) for key, value in payload.items() if not key.startswith("_")}


def _resource_wait_detail_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary = {key: value[key] for key in _RESOURCE_WAIT_DETAIL_KEYS if key in value}
    if isinstance(summary.get("status_url"), str):
        parsed = urlsplit(summary["status_url"])
        query = urlencode(
            [(key, value) for key, value in parse_qsl(parsed.query) if key in _STATUS_URL_QUERY_ALLOWLIST]
        )
        summary["status_url"] = f"{parsed.path}?{query}" if query else parsed.path
    return summary


def _blocked_wait_seconds(blocked_at: Any) -> int | None:
    if blocked_at is None:
        return None
    return max(int((timezone.now_for_db() - blocked_at).total_seconds()), 0)


def _build_trace_summary(result: Any) -> TraceOverviewSummary:
    session = result.session
    latest_timeline = result.timelines[-1] if result.timelines else None
    return TraceOverviewSummary(
        callback_logs=len(result.callback_logs),
        inboxes=len(result.inboxes),
        commands=len(result.commands),
        outboxes=len(result.outboxes),
        timelines=len(result.timelines),
        diagnostics=len(result.diagnostics),
        session_status=optional_enum_str(session.status) if session is not None else None,
        current_wait_type=session.current_wait_type if session is not None else None,
        latest_timeline_action=optional_enum_str(latest_timeline.action_type) if latest_timeline else None,
        latest_timeline_status=optional_enum_str(latest_timeline.status) if latest_timeline else None,
        latest_timeline_message=latest_timeline.message if latest_timeline else None,
    )


def _build_session_item(session: Any) -> TraceSessionItem | None:
    if session is None:
        return None

    reconciliation_state = optional_enum_str(getattr(session, "reconciliation_state", None))

    return TraceSessionItem(
        id=session.id,
        session_code=session.session_code,
        workline_id=session.workline_id,
        plugin_key=session.plugin_key,
        run_mode=_status_str(session.run_mode),
        business_key=session.business_key,
        barcode=session.barcode,
        status=_status_str(session.status),
        trace_id=session.trace_id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        current_wait_type=session.current_wait_type,
        current_wait_timeout_seconds=getattr(session, "current_wait_timeout_seconds", None),
        waiting_since=session.waiting_since,
        deadline_at=session.deadline_at,
        awaiting_command_id=session.awaiting_command_id,
        reconciliation_state=reconciliation_state,
        reconciliation_reason=optional_enum_str(getattr(session, "reconciliation_reason", None)),
        reconciliation_source_kind=optional_enum_str(getattr(session, "reconciliation_source_kind", None)),
        reconciliation_source_inbox_id=getattr(session, "reconciliation_source_inbox_id", None),
        reconciliation_source_outbox_id=getattr(session, "reconciliation_source_outbox_id", None),
        reconciliation_command_id=getattr(session, "reconciliation_command_id", None),
        reconciliation_device_id=getattr(session, "reconciliation_device_id", None),
        reconciliation_wait_token=getattr(session, "reconciliation_wait_token", None),
        reconciliation_ack_received_at=getattr(session, "reconciliation_ack_received_at", None),
        reconciliation_deadline_at=getattr(session, "reconciliation_deadline_at", None),
        reconciliation_occurred_at=getattr(session, "reconciliation_occurred_at", None),
        reconciliation_late_evidence_received=getattr(session, "reconciliation_late_evidence_received", False),
        reconciliation_resolution=optional_enum_str(getattr(session, "reconciliation_resolution", None)),
        reconciliation_resolved_at=getattr(session, "reconciliation_resolved_at", None),
        required_operator_action=("RESOLVE_RUNTIME_RECONCILIATION" if reconciliation_state == "PENDING" else None),
        failure_domain=session.failure_domain,
        failure_code=session.failure_code,
        failure_message=session.failure_message,
        ingress_count=session.ingress_count,
        last_request_id=session.last_request_id,
        last_ingress_at=session.last_ingress_at,
        last_inbox_id=session.last_inbox_id,
        context_json=session.context_json,
    )


def _build_callback_log_item(item: Any) -> TraceCallbackLogItem:
    return TraceCallbackLogItem(
        id=item.id,
        callback_type=item.callback_type,
        subject_code=item.subject_code,
        request_id=item.request_id,
        trace_id=item.trace_id,
        event_id=item.event_id,
        causation_id=item.causation_id,
        response_status=item.response_status,
        response_time_ms=item.response_time_ms,
        error_message=item.error_message,
        ingress_outcome=item.ingress_outcome,
        failure_stage=item.failure_stage,
        request_body=item.request_body,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _build_inbox_item(item: Any) -> TraceInboxItem:
    return TraceInboxItem(
        id=item.id,
        kind=_status_str(item.kind),
        source_system=_status_str(item.source_system),
        source_message_id=item.source_message_id,
        trace_id=item.trace_id,
        event_id=item.event_id,
        causation_id=item.causation_id,
        workline_id=item.workline_id,
        device_id=item.device_id,
        command_id=item.command_id,
        session_id=item.session_id,
        status=_status_str(item.status),
        received_at=item.received_at,
        processed_at=item.processed_at,
        attempt_count=item.attempt_count,
        max_attempts=item.max_attempts,
        next_retry_at=item.next_retry_at,
        error_message=item.error_message,
        payload_json=item.payload_json,
    )


def _build_command_item(item: Any) -> TraceCommandItem:
    return TraceCommandItem(
        id=item.id,
        device_id=item.device_id,
        command_code=item.command_code,
        trace_id=item.trace_id,
        workline_id=item.workline_id,
        session_id=item.session_id,
        task_type=_status_str(item.task_type),
        status=_status_str(item.status),
        result=optional_enum_str(item.result),
        retry_count=item.retry_count,
        sent_at=item.sent_at,
        ack_received_at=item.ack_received_at,
        completed_at=item.completed_at,
        ack_code=item.ack_code,
        ack_message=item.ack_message,
        ack_trace_id=item.ack_trace_id,
        params=item.params,
        result_data=item.result_data,
        error_detail=item.error_detail,
        duration_ms=item.get_duration_ms(),
    )


def _build_outbox_item(item: Any) -> TraceOutboxItem:
    blocked_at = getattr(item, "blocked_at", None)
    return TraceOutboxItem(
        id=item.id,
        session_id=item.session_id,
        workline_id=item.workline_id,
        dispatch_type=_status_str(item.dispatch_type),
        dispatch_key=item.dispatch_key,
        target_type=_status_str(item.target_type),
        target_code=item.target_code,
        status=_status_str(item.status),
        attempt_count=item.attempt_count,
        next_retry_at=item.next_retry_at,
        last_error=item.last_error,
        blocked_by_runtime_hold_id=getattr(item, "blocked_by_runtime_hold_id", None),
        blocked_by_reconciliation_session_id=getattr(item, "blocked_by_reconciliation_session_id", None),
        blocked_device_id=getattr(item, "blocked_device_id", None),
        blocked_workline_id=getattr(item, "blocked_workline_id", None),
        blocked_reason=getattr(item, "blocked_reason", None),
        blocked_at=blocked_at,
        last_blocked_check_at=getattr(item, "last_blocked_check_at", None),
        blocked_wait_seconds=_blocked_wait_seconds(blocked_at),
        blocked_check_count=getattr(item, "blocked_check_count", None),
        blocked_detail_json=_resource_wait_detail_summary(getattr(item, "blocked_detail_json", None)),
        created_at=item.created_at,
        sent_at=item.sent_at,
        finished_at=item.finished_at,
        payload_json=item.payload_json,
    )


def _build_dispatch_attempt_item(item: Any) -> TraceDispatchAttemptItem:
    return TraceDispatchAttemptItem(
        id=item.id,
        outbox_id=item.outbox_id,
        dispatch_key=item.dispatch_key,
        attempt_no=item.attempt_no,
        lease_token=item.lease_token,
        status=_status_str(item.status),
        target_type=item.target_type,
        target_code=item.target_code,
        started_at=item.started_at,
        finalized_at=item.finalized_at,
        error_message=item.error_message,
        response_json=item.response_json,
        trace_json=item.trace_json,
    )


def _build_timeline_item(item: Any) -> TraceTimelineItem:
    return TraceTimelineItem(
        id=item.id,
        session_id=item.session_id,
        workline_id=item.workline_id,
        trace_id=item.trace_id,
        seq_no=item.seq_no,
        occurred_at=item.occurred_at,
        stage=_status_str(item.stage),
        action_type=_status_str(item.action_type),
        actor_type=_status_str(item.actor_type),
        actor_code=item.actor_code,
        from_status=item.from_status,
        to_status=item.to_status,
        status=_status_str(item.status),
        failure_domain=item.failure_domain,
        message=item.message,
        payload_json=cast("dict[str, Any] | None", item.payload_json),
        related_inbox_id=item.related_inbox_id,
        related_command_id=item.related_command_id,
    )


def build_trace_timeline_item(item: Any) -> TraceTimelineItem:
    return _build_timeline_item(item)


def build_failed_command_evidence(command: Any | None) -> FailedCommandEvidence | None:
    """Build the RuntimeHold failed-command evidence from the trace command projection."""

    if command is None:
        return None
    return FailedCommandEvidence(
        command_id=command.id,
        command_code=command.command_code,
        status=optional_enum_str(command.status),
        result=optional_enum_str(command.result),
        error_detail=cast("dict[str, Any] | None", command.error_detail),
        result_data=cast("dict[str, Any] | None", command.result_data),
    )


def _build_diagnostic_item(item: Any) -> TraceDiagnosticItem:
    return TraceDiagnosticItem(
        request_id=item.request_id,
        trace_id=item.trace_id,
        session_id=item.session_id,
        inbox_id=item.inbox_id,
        outbox_id=item.outbox_id,
        command_code=item.command_code,
        device_code=item.device_code,
        workline_id=item.workline_id,
        workline_code=item.workline_code,
        plugin_key=item.plugin_key,
        canonical_event_type=item.canonical_event_type,
        transition=item.transition,
        extra=item.extra,
    )


def _build_resource_evidence(result: Any) -> TraceResourceEvidenceResponse:
    return TraceResourceEvidenceResponse(
        resource_state_events=[_resource_evidence_dict(item) for item in getattr(result, "resource_state_events", [])],
        rack_releases=[_resource_evidence_dict(item) for item in getattr(result, "rack_releases", [])],
        rack_release_bin_snapshots=[
            _resource_evidence_dict(item) for item in getattr(result, "rack_release_bin_snapshots", [])
        ],
        wms_writeback_evidence=[
            _resource_evidence_dict(item) for item in getattr(result, "wms_writeback_evidence", [])
        ],
        rack_bin_mounts=[_resource_evidence_dict(item) for item in getattr(result, "rack_bin_mounts", [])],
        runtime_holds=[_resource_evidence_dict(item) for item in getattr(result, "runtime_holds", [])],
    )


def build_trace_response(result: Any) -> TraceDetailResponse:
    session = result.session
    sessions = result.sessions
    dispatch_attempts = result.dispatch_attempts

    return TraceDetailResponse(
        trace=TraceContextResponse(**result.trace.as_dict()),
        summary=_build_trace_summary(result),
        session=_build_session_item(session),
        sessions=[item for item in (_build_session_item(session_item) for session_item in sessions) if item],
        callback_logs=[_build_callback_log_item(item) for item in result.callback_logs],
        inboxes=[_build_inbox_item(item) for item in result.inboxes],
        commands=[_build_command_item(item) for item in result.commands],
        outboxes=[_build_outbox_item(item) for item in result.outboxes],
        dispatch_attempts=[_build_dispatch_attempt_item(item) for item in dispatch_attempts],
        timelines=[_build_timeline_item(item) for item in result.timelines],
        diagnostics=[_build_diagnostic_item(item) for item in result.diagnostics],
        resource_evidence=_build_resource_evidence(result),
    )


__all__ = ["build_failed_command_evidence", "build_trace_response", "build_trace_timeline_item"]
