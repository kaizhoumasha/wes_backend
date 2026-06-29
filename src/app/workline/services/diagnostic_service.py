"""工作线诊断 Service。"""

from __future__ import annotations

from typing import Any

from src.app.runtime.orchestration.consumers.diagnostics_bridge import (
    DiagnosticEvent,
    ErrorCode,
    build_diagnostic_card,
    build_diagnostic_context,
    build_diagnostic_event,
    get_diagnostic_code_definition,
)
from src.app.runtime.orchestration.resource_wait_evidence_bridge import ResourceWaitEvidence
from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.app.workline.repositories.diagnostic_repository import (
    WorklineDiagnosticRepository,
    workline_diagnostic_repository,
)
from src.core.base_service import BaseService

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


def _coerce_diagnostic_event(event: Any) -> DiagnosticEvent:
    """将同形状的诊断镜像模型归一为 WLR DiagnosticEvent。"""

    if isinstance(event, DiagnosticEvent):
        return event
    if hasattr(event, "model_dump"):
        return DiagnosticEvent.model_validate(event.model_dump(mode="json"))
    return DiagnosticEvent.model_validate(event)


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
        event: Any,
        evidence: dict[str, Any] | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        diagnostic_key_override: str | None = None,
        auto_commit: bool = True,
    ) -> WorklineDiagnostic:
        """按诊断事件创建或复用诊断记录。

        callback 域在 Phase 2 launch PR 中拥有本地诊断镜像模型;进入
        workline 持久化边界时统一转换为 WLR 模型,避免 Pydantic 嵌套模型类型不匹配。
        """

        event = _coerce_diagnostic_event(event)
        diagnostic_key = diagnostic_key_override or self.build_diagnostic_key(event)
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

        created = await self.repo.create_idempotent_by_diagnostic_key(db, data)
        if auto_commit:
            await self._commit_mutation(db)
        return created

    async def get_active_by_trace_id(self, db: Any, trace_id: str) -> list[WorklineDiagnostic]:
        """查询指定 trace 的活跃诊断。"""

        return await self.repo.get_active_by_trace_id(db, trace_id)

    async def record_resource_wait(
        self,
        db: Any,
        *,
        evidence: ResourceWaitEvidence,
        inbox: Any,
        session: Any,
        workline: Any,
        auto_commit: bool = True,
    ) -> WorklineDiagnostic:
        """幂等记录当前 Inbox 的 RESOURCE_WAIT 诊断。"""

        _ = await self.repo.resolve_other_active_resource_waits_for_inbox(
            db,
            inbox_id=evidence.inbox_id,
            keep_diagnostic_key=evidence.diagnostic_key,
        )
        existing = await self.repo.get_by_diagnostic_key(db, evidence.diagnostic_key)
        existing_evidence = existing.evidence_json if existing is not None else None
        merged_evidence = (
            ResourceWaitEvidence.build(
                inbox_id=evidence.inbox_id,
                subject_type=evidence.subject_type,
                subject_key=evidence.subject_key,
                projection_type=evidence.projection_type,
                reason_code=evidence.reason_code,
                message=evidence.message,
                occurred_at=evidence.last_seen_at,
                session_id=evidence.session_id,
                workline_id=evidence.workline_id,
                trace_id=evidence.trace_id,
                details=evidence.details,
                existing=existing_evidence if isinstance(existing_evidence, dict) else None,
            )
            if existing is not None
            else evidence
        )
        diagnostic_evidence = merged_evidence.to_diagnostic_evidence()
        if existing is not None:
            updated = await self.repo.update_resource_wait_by_key(
                db,
                diagnostic_key=merged_evidence.diagnostic_key,
                message=merged_evidence.message,
                evidence_json=_redact(diagnostic_evidence),
            )
            if auto_commit:
                await self._commit_mutation(db)
            return updated or existing

        context = build_diagnostic_context(
            trace_id=merged_evidence.trace_id,
            session=session,
            inbox=inbox,
            workline=workline,
            extra={
                "subject_type": merged_evidence.subject_type,
                "subject_key": merged_evidence.subject_key,
                "projection_type": merged_evidence.projection_type,
                "reason_code": merged_evidence.reason_code,
            },
        )
        event = build_diagnostic_event(
            error_code=ErrorCode.RESOURCE_WAIT,
            context=context,
            message=merged_evidence.message,
            operator_action="等待资源释放后自动重试",
        )
        return await self.record_event(
            db,
            event=event,
            evidence=diagnostic_evidence,
            diagnostic_key_override=merged_evidence.diagnostic_key,
            auto_commit=auto_commit,
        )

    async def resolve_resource_wait_diagnostics(
        self,
        db: Any,
        *,
        inbox_id: int,
        subject_type: str,
        subject_key: str,
        projection_type: str,
        auto_commit: bool = True,
    ) -> int:
        """成功推进后关闭当前 Inbox + subject 的 ACTIVE RESOURCE_WAIT 诊断。"""

        resolved = await self.repo.resolve_resource_wait_by_key(
            db,
            diagnostic_key=ResourceWaitEvidence(
                inbox_id=inbox_id,
                subject_type=subject_type,
                subject_key=subject_key,
                projection_type=projection_type,
                reason_code="RESOURCE_WAIT_RESOLVED",
                message="RESOURCE_WAIT resolved",
                first_seen_at="",
                last_seen_at="",
                wait_count=1,
            ).diagnostic_key,
        )
        if auto_commit:
            await self._commit_mutation(db)
        return resolved


workline_diagnostic_service = WorklineDiagnosticService()


__all__ = ["WorklineDiagnosticService", "workline_diagnostic_service"]
