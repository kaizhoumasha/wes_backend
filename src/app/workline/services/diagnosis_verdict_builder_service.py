"""Trace 统一诊断结论构建器。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.models.runtime import (
    DiagnosisEvidenceHealthItemResponse,
    DiagnosisEvidenceHealthResponse,
    DiagnosisVerdictResponse,
)
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import coerce_optional_str, optional_enum_str

_FAILED_TIMELINE_STATUSES = {"FAILED", "ERROR"}
_FAILED_TIMELINE_ACTIONS = {"SESSION_FAILED", "COMMAND_FAILED", "EVENT_FAILED", "EXTERNAL_CALL_FAILED"}
_FAILED_INBOX_STATUSES = {"FAILED", "DEAD_LETTER"}
_FAILED_COMMAND_STATUSES = {"FAILED", "TIMEOUT"}
_FAILED_OUTBOX_STATUSES = {"FAILED"}
_RUNNING_SESSION_STATUSES = {"NEW", "RUNNING", "PROCESSING", "WAITING_COMMAND", "WAITING_CALLBACK"}
_WAITING_SESSION_STATUSES = {"WAITING", "WAITING_COMMAND", "WAITING_CALLBACK"}
_ADMISSION_RECOVERING_STATUSES = {"FAILED", "CHECKING", "RECOVERING", "WAITING", "BLOCKED"}


class DiagnosisVerdictBuilder:
    """根据 Trace 聚合事实输出唯一诊断语义。"""

    def build(self, result: Any) -> DiagnosisVerdictResponse:
        evidence_health = self._build_evidence_health(result)

        for candidate in (
            self._resource_wait_verdict(result, evidence_health),
            self._blocked_or_failed_verdict(result, evidence_health),
            self._waiting_verdict(result, evidence_health),
        ):
            if candidate is not None:
                return candidate
        return self._normal_or_unknown_verdict(result, evidence_health)

    def _resource_wait_verdict(
        self,
        result: Any,
        evidence_health: DiagnosisEvidenceHealthResponse,
    ) -> DiagnosisVerdictResponse | None:
        resource_wait = self._resource_wait_outbox(result)
        if resource_wait is None:
            return None
        device_code = self._resource_wait_device_code(resource_wait)
        return DiagnosisVerdictResponse(
            state="waiting",
            severity="warning",
            title="等待设备接纳",
            summary="设备接纳条件暂不满足，系统正在等待下一次资源预检。",
            requires_operator_action=False,
            primary_action=f"观察设备 {device_code} 的 AUTO/IDLE 状态并等待系统自动恢复"
            if device_code
            else "观察设备接纳状态并等待系统自动恢复",
            blocking_point="resource",
            owner="device",
            evidence_health=evidence_health,
        )

    def _blocked_or_failed_verdict(
        self,
        result: Any,
        evidence_health: DiagnosisEvidenceHealthResponse,
    ) -> DiagnosisVerdictResponse | None:
        failed_outbox = self._first_by_status(result.outboxes, _FAILED_OUTBOX_STATUSES)
        if failed_outbox is not None:
            return DiagnosisVerdictResponse(
                state="failed",
                severity="danger",
                title="Outbox 派发失败",
                summary=coerce_optional_str(getattr(failed_outbox, "last_error", None)) or "设备派发消息失败。",
                requires_operator_action=True,
                primary_action="检查 outbox 派发错误和设备接收状态，确认后进行人工对账或重试",
                blocking_point="outbox",
                owner="integration",
                evidence_health=evidence_health,
            )

        failed_inbox = self._first_by_status(result.inboxes, _FAILED_INBOX_STATUSES)
        if failed_inbox is not None:
            return DiagnosisVerdictResponse(
                state="failed",
                severity="danger",
                title="Inbox 处理失败",
                summary=coerce_optional_str(getattr(failed_inbox, "last_error_message", None)) or "入口事件处理失败。",
                requires_operator_action=True,
                primary_action="查看 inbox 错误和诊断证据，修复后重放或重试入口事件",
                blocking_point="inbox",
                owner="integration",
                evidence_health=evidence_health,
            )

        failed_command = self._first_by_status(result.commands, _FAILED_COMMAND_STATUSES)
        if failed_command is not None:
            return DiagnosisVerdictResponse(
                state="failed",
                severity="danger",
                title="设备指令失败",
                summary="设备指令失败或超时。",
                requires_operator_action=True,
                primary_action="现场确认设备动作状态，核对 command_code 后按流程重试或对账",
                blocking_point="command",
                owner="device",
                evidence_health=evidence_health,
            )

        manual_hold = self._manual_hold_block(result)
        if manual_hold is not None:
            _timeline, payload = manual_hold
            reason_code = coerce_optional_str(payload.get("reason_code"))
            is_wms_hold = (
                reason_code == "WMS_TIMEOUT"
                or coerce_optional_str(getattr(result.session, "failure_code", None)) == "WMS_TIMEOUT"
            )
            return DiagnosisVerdictResponse(
                state="blocked",
                severity="danger",
                title="流程已阻塞",
                summary=coerce_optional_str(getattr(result.session, "failure_message", None))
                or coerce_optional_str(payload.get("suggested_action"))
                or "流程进入人工保持，需要处理外部依赖或人工对账。",
                requires_operator_action=True,
                primary_action=coerce_optional_str(payload.get("suggested_action"))
                or "人工检查当前物料与外部系统状态，处理后解除保持",
                blocking_point="external_wms" if is_wms_hold else "session",
                owner="integration" if is_wms_hold else "workflow",
                evidence_health=evidence_health,
            )

        session = getattr(result, "session", None)
        session_status = optional_enum_str(getattr(session, "status", None)) if session is not None else None
        if session is not None and session_status == "FAILED":
            return DiagnosisVerdictResponse(
                state="failed",
                severity="danger",
                title="会话失败",
                summary=coerce_optional_str(getattr(session, "failure_message", None)) or "运行会话已失败。",
                requires_operator_action=True,
                primary_action="查看会话失败域和失败码，按对应恢复流程处理",
                blocking_point="session",
                owner="workflow",
                evidence_health=evidence_health,
            )

        failed_timeline = self._failed_timeline(result)
        if failed_timeline is not None:
            return DiagnosisVerdictResponse(
                state="failed",
                severity="danger",
                title="流程步骤失败",
                summary=coerce_optional_str(getattr(failed_timeline, "message", None)) or "Timeline 记录了失败步骤。",
                requires_operator_action=True,
                primary_action="查看失败 timeline 的上下文和关联证据后处理",
                blocking_point="session",
                owner="workflow",
                evidence_health=evidence_health,
            )
        return None

    def _waiting_verdict(
        self,
        result: Any,
        evidence_health: DiagnosisEvidenceHealthResponse,
    ) -> DiagnosisVerdictResponse | None:
        if self._is_completed_clear(result):
            return None

        admission_wait = self._admission_wait_outbox(result)
        admission_status = coerce_optional_str(getattr(result, "workline_start_admission_status", None))
        if admission_wait is not None or self._has_start_admission_projection(result):
            requires_action = admission_status == "BLOCKED"
            state = "blocked" if requires_action else "waiting"
            return DiagnosisVerdictResponse(
                state=state,
                severity="danger" if requires_action else "warning",
                title="START 准入等待" if not requires_action else "START 准入阻塞",
                summary=coerce_optional_str(getattr(result, "workline_start_admission_message", None))
                or "WorkLine 正在等待 START 准入或恢复链路。",
                requires_operator_action=requires_action,
                primary_action=self._admission_action(result, requires_action=requires_action),
                blocking_point="admission",
                owner="workline",
                evidence_health=evidence_health,
            )
        session = getattr(result, "session", None)
        session_status = optional_enum_str(getattr(session, "status", None)) if session is not None else None
        if self._is_waiting_session(session, session_status):
            return DiagnosisVerdictResponse(
                state="waiting",
                severity="warning",
                title="流程等待中",
                summary="运行会话正在等待外部事件、设备回报或超时窗口。",
                requires_operator_action=False,
                primary_action="观察等待对象和 deadline，超时后再进入人工处理",
                blocking_point="session",
                owner="workflow",
                evidence_health=evidence_health,
            )
        return None

    def _normal_or_unknown_verdict(
        self,
        result: Any,
        evidence_health: DiagnosisEvidenceHealthResponse,
    ) -> DiagnosisVerdictResponse:
        if self._is_completed_clear(result):
            return DiagnosisVerdictResponse(
                state="completed_clear",
                severity="success",
                title="流程已完成",
                summary="当前案件已正常结束，未发现阻塞点。",
                requires_operator_action=False,
                primary_action="无需现场处置",
                blocking_point="none",
                owner=None,
                evidence_health=evidence_health,
            )

        session = getattr(result, "session", None)
        session_status = optional_enum_str(getattr(session, "status", None)) if session is not None else None
        if session is not None and session_status in _RUNNING_SESSION_STATUSES:
            return DiagnosisVerdictResponse(
                state="running",
                severity="info",
                title="流程运行中",
                summary="当前案件正在正常推进，未发现阻塞点。",
                requires_operator_action=False,
                primary_action="继续观察运行进度",
                blocking_point="none",
                owner=None,
                evidence_health=evidence_health,
            )

        return DiagnosisVerdictResponse(
            state="unknown",
            severity="warning",
            title="诊断不足",
            summary=self._unknown_summary(evidence_health),
            requires_operator_action=False,
            primary_action="补齐会话、Timeline、Inbox、Command、Outbox 证据后重新诊断",
            blocking_point="unknown",
            owner=None,
            evidence_health=evidence_health,
        )

    def _build_evidence_health(self, result: Any) -> DiagnosisEvidenceHealthResponse:
        session_count = 1 if getattr(result, "session", None) is not None else 0
        timelines_count = len(getattr(result, "timelines", []))
        callback_count = len(getattr(result, "callback_logs", []))
        inbox_count = len(getattr(result, "inboxes", []))
        command_count = len(getattr(result, "commands", []))
        outbox_count = len(getattr(result, "outboxes", []))
        diagnostics_count = len(getattr(result, "diagnostics", []))
        has_admission = self._has_start_admission_projection(result)
        has_resource_wait = self._resource_wait_outbox(result) is not None
        completed = self._is_completed_clear(result)

        items = [
            self._item(
                "session",
                "Session",
                session_count,
                required=True,
                present_hint="主会话证据已就绪",
                empty_hint="缺少 Session 关键证据",
            ),
            self._item(
                "timeline", "Timeline", timelines_count, required=not completed, empty_hint="当前结论不依赖 Timeline"
            ),
            self._item(
                "callback", "Callback", callback_count, required=False, empty_hint="当前结论不依赖 callback 证据"
            ),
            self._item("inbox", "Inbox", inbox_count, required=False, empty_hint="当前结论无待处理 inbox"),
            self._item("command", "Command", command_count, required=False, empty_hint="当前结论不依赖设备指令"),
            self._item("outbox", "Outbox", outbox_count, required=False, empty_hint="当前结论无待处理 outbox"),
            self._item(
                "diagnostics", "Diagnostics", diagnostics_count, required=False, empty_hint="当前结论不依赖持久化诊断"
            ),
            DiagnosisEvidenceHealthItemResponse(
                key="workline_admission",
                label="WorkLine Admission",
                count=1 if has_admission else 0,
                state="present" if has_admission else "not_required",
                hint="START 准入投影已就绪" if has_admission else "当前结论不依赖 START 准入投影",
            ),
            DiagnosisEvidenceHealthItemResponse(
                key="resource_wait",
                label="Resource Wait",
                count=1 if has_resource_wait else 0,
                state="present" if has_resource_wait else "not_required",
                hint="资源等待证据已就绪" if has_resource_wait else "当前结论不依赖资源等待证据",
            ),
        ]
        missing = [item.key for item in items if item.state == "missing"]
        if missing:
            level = "missing"
            summary = f"缺少关键证据：{', '.join(missing)}"
        elif any(item.state == "empty" for item in items):
            level = "partial"
            summary = "证据部分存在，当前结论已结合业务状态解释空证据。"
        else:
            level = "complete"
            summary = "关键证据完整。"
        return DiagnosisEvidenceHealthResponse(level=level, summary=summary, missing=missing, items=items)

    @staticmethod
    def _item(
        key: str,
        label: str,
        count: int,
        *,
        required: bool,
        present_hint: str | None = None,
        empty_hint: str,
    ) -> DiagnosisEvidenceHealthItemResponse:
        if count > 0:
            return DiagnosisEvidenceHealthItemResponse(
                key=key,
                label=label,
                count=count,
                state="present",
                hint=present_hint or f"{label} 证据已就绪",
            )
        return DiagnosisEvidenceHealthItemResponse(
            key=key,
            label=label,
            count=0,
            state="missing" if required else "not_required",
            hint=f"缺少 {label} 关键证据" if required else empty_hint,
        )

    @staticmethod
    def _first_by_status(items: list[Any], statuses: set[str]) -> Any | None:
        return next((item for item in items if optional_enum_str(getattr(item, "status", None)) in statuses), None)

    @staticmethod
    def _resource_wait_outbox(_result: Any) -> Any | None:
        return None

    @staticmethod
    def _admission_wait_outbox(result: Any) -> Any | None:
        return next(
            (
                item
                for item in getattr(result, "outboxes", [])
                if optional_enum_str(getattr(item, "status", None)) == "RETRY_WAIT"
                and coerce_optional_str(getattr(item, "blocked_reason", None)) == "WORKLINE_STOPPED_WAITING_START"
            ),
            None,
        )

    @staticmethod
    def _resource_wait_device_code(outbox: Any) -> str | None:
        detail = payload_dict(getattr(outbox, "blocked_detail_json", None))
        return coerce_optional_str(detail.get("device_code")) or coerce_optional_str(
            getattr(outbox, "target_code", None)
        )

    @staticmethod
    def _has_start_admission_projection(result: Any) -> bool:
        status = coerce_optional_str(getattr(result, "workline_start_admission_status", None))
        runtime_status = coerce_optional_str(getattr(result, "workline_runtime_status", None))
        return bool(status and (status in _ADMISSION_RECOVERING_STATUSES or runtime_status == "STOPPED"))

    @staticmethod
    def _admission_action(result: Any, *, requires_action: bool) -> str:
        device_code = coerce_optional_str(getattr(result, "workline_start_admission_failed_device_code", None))
        message = coerce_optional_str(getattr(result, "workline_start_admission_message", None))
        if requires_action:
            return message or "处理 START 准入阻塞后重新发起 START"
        if device_code:
            return f"等待设备 {device_code} 满足 START 准入条件，观察恢复链路"
        return message or "等待 START 准入恢复链路完成"

    @staticmethod
    def _manual_hold_block(result: Any) -> tuple[Any, dict[str, Any]] | None:
        session = getattr(result, "session", None)
        if session is None or optional_enum_str(getattr(session, "status", None)) != "MANUAL_HOLD":
            return None
        for timeline in reversed(getattr(result, "timelines", [])):
            payload = payload_dict(getattr(timeline, "payload_json", None))
            if coerce_optional_str(payload.get("reason_code")):
                return timeline, payload
        session_failure_code = coerce_optional_str(getattr(session, "failure_code", None))
        if session_failure_code:
            return None, {"reason_code": session_failure_code}
        return None

    @staticmethod
    def _failed_timeline(result: Any) -> Any | None:
        return next(
            (
                item
                for item in getattr(result, "timelines", [])
                if optional_enum_str(getattr(item, "status", None)) in _FAILED_TIMELINE_STATUSES
                or optional_enum_str(getattr(item, "action_type", None)) in _FAILED_TIMELINE_ACTIONS
            ),
            None,
        )

    def _is_completed_clear(self, result: Any) -> bool:
        session = getattr(result, "session", None)
        if session is None or optional_enum_str(getattr(session, "status", None)) != "COMPLETED":
            return False
        if coerce_optional_str(getattr(session, "failure_domain", None)) or coerce_optional_str(
            getattr(session, "failure_code", None)
        ):
            return False
        return not any(
            (
                self._first_by_status(getattr(result, "inboxes", []), _FAILED_INBOX_STATUSES),
                self._first_by_status(getattr(result, "commands", []), _FAILED_COMMAND_STATUSES),
                self._first_by_status(getattr(result, "outboxes", []), _FAILED_OUTBOX_STATUSES),
                self._manual_hold_block(result),
                self._failed_timeline(result),
            )
        )

    @staticmethod
    def _is_waiting_session(session: Any | None, session_status: str | None) -> bool:
        if session is None:
            return False
        return bool(
            session_status in _WAITING_SESSION_STATUSES
            or coerce_optional_str(getattr(session, "current_wait_type", None))
            or getattr(session, "waiting_since", None)
            or getattr(session, "deadline_at", None)
            or getattr(session, "awaiting_device_command_code", None)
        )

    @staticmethod
    def _unknown_summary(evidence_health: DiagnosisEvidenceHealthResponse) -> str:
        if evidence_health.missing:
            return f"诊断不足，缺少关键证据：{', '.join(evidence_health.missing)}。"
        return "诊断不足，当前证据无法可靠判断流程是否完成、等待、阻塞或失败。"


diagnosis_verdict_builder = DiagnosisVerdictBuilder()


__all__ = ["DiagnosisVerdictBuilder", "diagnosis_verdict_builder"]
