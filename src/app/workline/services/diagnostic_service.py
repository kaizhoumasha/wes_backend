"""工作线诊断 Service。"""

from __future__ import annotations

from typing import Any

from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.app.workline.repositories.diagnostic_repository import (
    WorklineDiagnosticRepository,
    workline_diagnostic_repository,
)
from src.core.base_service import BaseService
from src.workline_runtime.diagnostics import (
    DiagnosticEvent,
    build_diagnostic_card,
    get_diagnostic_code_definition,
)

_REDACT_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            if str(key).lower() in _REDACT_KEYS:
                redacted[key] = "***"
            else:
                redacted[key] = _redact(child)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class WorklineDiagnosticService(BaseService[WorklineDiagnostic, WorklineDiagnosticRepository]):
    """集中生成和持久化工作线诊断。"""

    def __init__(self, repository: WorklineDiagnosticRepository | None = None) -> None:
        super().__init__(repository or workline_diagnostic_repository, enable_cache=False)

    @staticmethod
    def build_diagnostic_key(event: DiagnosticEvent) -> str:
        """生成诊断幂等键。"""

        context = event.context
        trace_id = context.trace_id or context.request_id or "unknown-trace"
        entity = context.command_code or context.outbox_id or context.inbox_id or context.session_id or "unknown-entity"
        request_id = context.request_id or "no-request"
        return f"{event.error_code.value}:{trace_id}:{entity}:{request_id}"

    async def record_event(
        self,
        db: Any,
        *,
        event: DiagnosticEvent,
        evidence: dict[str, Any] | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        auto_commit: bool = True,
    ) -> WorklineDiagnostic:
        """按诊断事件创建或复用诊断记录。"""

        diagnostic_key = self.build_diagnostic_key(event)
        existing = await self.repo.get_by_diagnostic_key(db, diagnostic_key)
        if existing is not None:
            return existing

        card = build_diagnostic_card(event)
        definition = get_diagnostic_code_definition(event.error_code)
        context = event.context
        card_json = card.model_dump(mode="json", exclude_none=True)
        data: dict[str, Any] = {
            "diagnostic_key": diagnostic_key,
            "trace_id": context.trace_id,
            "request_id": context.request_id,
            "event_id": event_id or getattr(context, "event_id", None),
            "causation_id": causation_id or getattr(context, "causation_id", None),
            "session_id": context.session_id,
            "inbox_id": context.inbox_id,
            "outbox_id": context.outbox_id,
            "command_code": context.command_code,
            "device_code": context.device_code,
            "workline_id": context.workline_id,
            "plugin_key": context.plugin_key,
            "diagnostic_code": event.error_code.value,
            "error_domain": event.error_domain.value,
            "severity": event.severity.value,
            "recoverability": event.recoverability.value,
            "problem_class": event.problem_class.value,
            "owner": definition.owner,
            "message": event.message,
            "operator_action": event.operator_action or definition.fix,
            "technical_summary": event.technical_summary,
            "docs_anchor": definition.docs_anchor,
            "next_steps_json": list(event.next_steps),
            "evidence_json": _redact(evidence or {}),
            "card_json": card_json,
        }

        created = await self.repo.create(db, data)
        if created is None:
            # 如果并发事务已经创建了同一 diagnostic_key，优先返回已存在记录。
            existing_after_conflict = await self.repo.get_by_diagnostic_key(db, diagnostic_key)
            if existing_after_conflict is not None:
                return existing_after_conflict
            raise RuntimeError(f"创建工作线诊断失败: {diagnostic_key}")
        if auto_commit:
            await self._commit_mutation(db)
        return created

    async def get_active_by_trace_id(self, db: Any, trace_id: str) -> list[WorklineDiagnostic]:
        """查询指定 trace 的活跃诊断。"""

        return await self.repo.get_active_by_trace_id(db, trace_id)


workline_diagnostic_service = WorklineDiagnosticService()


__all__ = ["WorklineDiagnosticService", "workline_diagnostic_service"]
