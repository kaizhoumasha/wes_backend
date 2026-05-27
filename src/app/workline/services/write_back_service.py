from typing import Any

from src.utils.timezone import timezone
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.trace_context import TraceContext


def _session_context(session: Any) -> dict[str, Any]:
    context = getattr(session, "context_json", None)
    return dict(context) if isinstance(context, dict) else {}


class OrchestratorWriteBackService:
    async def write_back(
        self,
        db: Any,
        *,
        session: Any,
        workline: Any,
        inbox: Any,
        devices_by_role: dict[str, list[Any]],
        source_device: Any | None,
        orch_result: OrchestratorResult,
    ) -> None:
        """应用 OrchestratorResult 到 Session / Command / Outbox / Timeline。"""
        trace = TraceContext.from_runtime(
            session=session,
            workline=workline,
            inbox=inbox,
            trace_id=getattr(inbox, "trace_id", None) or getattr(session, "trace_id", None),
        )
        ctx: Any = {
            "db": db,
            "session": session,
            "workline": workline,
            "inbox": inbox,
            "devices_by_role": devices_by_role,
            "source_device": source_device,
            "orch_result": orch_result,
            "current_status": getattr(session, "status", None),
            "trace_id": trace.trace_id,
            "trace": trace,
            "session_ctx": _session_context(session),
            "now": timezone.now_for_db(),
            "awaiting_command_id": None,
            "awaiting_command_code": None,
            "next_timeline_seq_no": None,
        }

        from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier

        await RuntimeIntentEffectApplier().apply(ctx, orch_result.intents or [])


orchestrator_write_back_service = OrchestratorWriteBackService()
