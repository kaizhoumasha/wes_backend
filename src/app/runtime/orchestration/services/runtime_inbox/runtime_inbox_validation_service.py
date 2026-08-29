"""RuntimeInboxValidationService (Task 5 三阶段 Processor 拆分).

SCAN_COMPLETED barcode 校验、ESTOP 阻断与 ESTOP 专用路由前置 gate。

输入: RuntimeInbox 实体 + payload.
输出: ValidationOutcome 携带 decision + 关联实体 + 必要终止态信息.

不写终态, 不调 orchestrator, 不做 write-back。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from src.app.runtime.orchestration.diagnostics import ErrorCode
from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import (
    RuntimeInboxRepository,
    runtime_inbox_repository,
)
from src.app.workline.diagnostic_support import _record_diagnostic
from src.app.workline.utils import payload_dict
from src.utils.value_normalization import string_value

if TYPE_CHECKING:
    from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox


_SCAN_BARCODE_FIELDS = (
    "HHPN",
    "MfrPN",
    "Qty",
    "DateCode",
    "LotCode",
    "PkgID",
    "ProductNo",
    "PONumber",
    "barcode",
)


def _scan_completed_has_any_barcode_payload(payload: dict[str, Any]) -> bool:
    """SCAN_COMPLETED 最小通用 gate。

    白皮书已禁止拍平 payload, 只接受嵌套 data 结构.
    """
    data_field = payload.get("data")
    data = cast("dict[str, Any]", data_field) if isinstance(data_field, dict) else {}
    return any(isinstance(data.get(field), str) and data.get(field) for field in _SCAN_BARCODE_FIELDS)


def _entry_event_types_for_workline(workline: Any | None) -> frozenset[str]:
    """核心只识别通用扫码入口；具体业务入口由外部插件包拥有。"""

    _ = workline
    return frozenset({"SCAN_COMPLETED"})


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Validation 阶段输出.

    Attributes:
        proceed_to_orchestrator: True 走 orchestrator; False 终止于本阶段.
        terminal_disposition: 若 proceed_to_orchestrator=False, 携带终态.
        error_message: 失败原因 (用于 mark_failed / mark_dead_letter).
        error_code: ErrorCode (CALLBACK_SCHEMA_INVALID / SESSION_CONTEXT_MISSING).
        estop_event: ESTOP 专用 routing context (handler 在 Orchestrator 阶段调用).
    """

    proceed_to_orchestrator: bool
    terminal_disposition: WriteBackDisposition | None = None
    error_message: str | None = None
    error_code: ErrorCode | None = None
    estop_event: bool = False
    needs_session_resolution: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def continue_orchestrator(*, needs_session_resolution: bool = False) -> ValidationOutcome:
        return ValidationOutcome(
            proceed_to_orchestrator=True,
            needs_session_resolution=needs_session_resolution,
        )

    @staticmethod
    def fail(
        *,
        error_message: str,
        error_code: ErrorCode,
    ) -> ValidationOutcome:
        return ValidationOutcome(
            proceed_to_orchestrator=False,
            error_message=error_message,
            error_code=error_code,
        )

    @staticmethod
    def estop_routing() -> ValidationOutcome:
        return ValidationOutcome(
            proceed_to_orchestrator=False,
            terminal_disposition=WriteBackDisposition.PROCESSED,
            estop_event=True,
        )


class RuntimeInboxValidationService:
    """RuntimeInbox 前置 gate 服务.

    单一职责: 在调用 orchestrator 前做出 SCAN/ESTOP 决策。
    不写终态, 不调 orchestrator, 不做 write-back.
    """

    def __init__(self, *, inbox_repository: RuntimeInboxRepository | None = None) -> None:
        self._inbox_repository = inbox_repository or runtime_inbox_repository

    async def is_payload_invalid_entry_replay(self, db: Any, *, inbox: Any, session: Any) -> bool:
        """仅凭当前 hold 的持久化因果证据授权入口事件重放。"""

        if getattr(inbox, "is_manual_replay", False) is not True:
            return False
        immediate_source_id = getattr(inbox, "replay_immediate_source_inbox_id", None)
        root_source_id = getattr(inbox, "replay_root_source_inbox_id", None)
        source_ids = {value for value in (immediate_source_id, root_source_id) if isinstance(value, int)}
        session_id = getattr(session, "id", None)
        if not isinstance(session_id, int) or not source_ids:
            return False
        status = getattr(getattr(session, "status", None), "value", getattr(session, "status", None))
        if status != "MANUAL_HOLD" or string_value(getattr(session, "failure_code", None)) != "PAYLOAD_INVALID":
            return False
        if getattr(session, "awaiting_device_command_code", None) is not None:
            return False
        if string_value(getattr(session, "current_wait_type", None)):
            return False

        evidence = await self._inbox_repository.get_latest_manual_hold_evidence(db, session_id=session_id)
        if evidence is None:
            return False
        return (
            evidence.session_id == session_id
            and evidence.action_type == "MANUAL_HOLD"
            and evidence.timeline_status == "PENDING"
            and evidence.reason_code == "PAYLOAD_INVALID"
            and evidence.related_inbox_id in source_ids
            and evidence.source_session_id == session_id
            and evidence.source_status == "DEAD_LETTER"
        )

    async def pre_gate(
        self,
        db: Any,
        *,
        inbox: RuntimeInbox,
        resolved_event_type: str,
        workline: Any | None,
    ) -> ValidationOutcome:
        """执行 SCAN_COMPLETED 最小 gate.

        Args:
            db: 数据库会话 (用于 _record_diagnostic).
            inbox: 已 claim 的 RuntimeInbox 实体.
            resolved_event_type: canonical_event_type(payload) 解析结果.
            workline: 关联 workline 实体 (可为 None).

        Returns:
            ValidationOutcome 决策.
        """
        _ = workline  # 当前 SCAN gate 不依赖 workline, 保留参数为对称 API.

        if resolved_event_type == "SCAN_COMPLETED":
            payload = payload_dict(getattr(inbox, "payload_json", None))
            if not _scan_completed_has_any_barcode_payload(payload):
                error_msg = "SCAN_COMPLETED 缺少条码信息（HHPN/MfrPN/Qty/DateCode/LotCode/PkgID 或 barcode）"
                logger.warning(f"Inbox {inbox.id} {error_msg}")
                try:
                    await _record_diagnostic(
                        db,
                        inbox=inbox,
                        error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                        message=error_msg,
                    )
                except Exception as exc:
                    logger.warning(f"记录 SCAN gate 诊断失败: {exc}")
                return ValidationOutcome.fail(
                    error_message=error_msg,
                    error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
                )

        # SCAN gate 全部通过 -> 继续走 orchestrator.
        return ValidationOutcome.continue_orchestrator()


__all__ = [
    "RuntimeInboxValidationService",
    "ValidationOutcome",
    "_entry_event_types_for_workline",
    "_scan_completed_has_any_barcode_payload",
]
