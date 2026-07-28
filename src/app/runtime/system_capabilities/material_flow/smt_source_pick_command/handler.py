"""SMT source-pick command 的设备命令与领域账本原子组合。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.device.device_command_write.contracts import DeviceCommandWriteInput
from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success
from src.utils.value_normalization import resolve_entity_id

from .contracts import (
    SmtSourcePickCommandAdmission,
    SmtSourcePickCommandInput,
    SmtSourcePickCommandOutput,
)


class SmtSourcePickCommandHandler:
    async def __call__(self, request: SmtSourcePickCommandInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.device_command_gateway import (
            StaleRuntimeDeviceCommandAdmission,
            prepare_runtime_device_command_effect,
        )
        from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
            smt_inbound_handoff_service,
        )

        ctx = execution.ctx  # type: ignore[attr-defined]
        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, SmtSourcePickCommandAdmission):
            raise TypeError("SMT source-pick command effect requires typed admission")
        try:
            ownership = self._resolve_ownership(ctx, request)
            generic_request = DeviceCommandWriteInput(
                target_device_id=request.target_device_id,
                action=request.action,
                payload={
                    **request.payload.model_dump(mode="json"),
                    "source_pick_inbox_id": ownership["source_pick_inbox_id"],
                },
                priority=request.priority,
                timeout_ms=request.timeout_ms,
                command_code=request.command_code,
                result_policy=request.result_policy,
            )
            command, outbox = await prepare_runtime_device_command_effect(
                ctx,
                generic_request,
                target_device_id=request.target_device_id,
                target_device_code=None,
                expected_workline_id=ownership["workline_id"],
                admission=admission,
                execution=execution,
                intent_log=execution.intent_log,  # type: ignore[attr-defined]
            )
            command_id = resolve_entity_id(command)
            command_code = getattr(command, "command_code", None)
            dispatch_key = getattr(outbox, "dispatch_key", None)
            if (
                not isinstance(command_id, int)
                or not isinstance(command_code, str)
                or not isinstance(dispatch_key, str)
            ):
                raise TypeError("SMT source-pick command/outbox identity is incomplete")
            await smt_inbound_handoff_service.record_source_pick_command_correlation(
                ctx["db"],
                handoff_demand_id=request.payload.handoff_demand_id,
                source_item_id=request.payload.handoff_source_item_id,
                claim_attempt_no=request.payload.claim_attempt_no,
                source_pick_inbox_id=ownership["source_pick_inbox_id"],
                command_id=command_id,
                command_code=command_code,
                dispatch_key=dispatch_key,
                session_id=ownership["session_id"],
                workline_id=ownership["workline_id"],
                execution_session_id=ownership["execution_session_id"],
                correlation_id=ownership["correlation_id"],
                plugin_key=ownership["plugin_key"],
                contract_version=ownership["contract_version"],
                trace_id=ctx.get("trace_id"),
            )
        except StaleRuntimeDeviceCommandAdmission:
            return BusinessReject(reason_code="STALE_PRECONDITION", message="device fact changed")
        except (TypeError, ValueError):
            return BusinessReject(
                reason_code="SMT_SOURCE_PICK_COMMAND_EVIDENCE_MISMATCH",
                message="SMT source-pick command ownership evidence mismatch",
            )
        return Success(
            payload=SmtSourcePickCommandOutput(
                accepted=True,
                command_code=command_code,
                dispatch_key=dispatch_key,
            )
        )

    @staticmethod
    def _resolve_ownership(ctx: dict[str, object], request: SmtSourcePickCommandInput) -> dict[str, object]:
        session = ctx.get("session")
        workline = ctx.get("workline")
        inbox = ctx.get("inbox")
        work_item = ctx.get("work_item")
        session_id = resolve_entity_id(session)
        workline_id = resolve_entity_id(workline)
        source_pick_inbox_id = resolve_entity_id(inbox)
        execution_session_id = getattr(inbox, "execution_session_id", None)
        correlation_id = ctx.get("correlation_id")
        plugin_key = getattr(session, "plugin_key", None)
        contract_version = getattr(session, "contract_version", None)
        expected = (
            session_id,
            workline_id,
            source_pick_inbox_id,
            execution_session_id,
            correlation_id,
            plugin_key,
            contract_version,
        )
        if any(value is None for value in expected):
            raise ValueError("SMT source-pick ownership identity is incomplete")
        if getattr(session, "workline_id", None) != workline_id or getattr(inbox, "workline_id", None) != workline_id:
            raise ValueError("SMT source-pick workline ownership mismatch")
        if work_item is not None:
            if getattr(work_item, "execution_session_id", None) != execution_session_id:
                raise ValueError("SMT source-pick execution ownership mismatch")
            if getattr(work_item, "correlation_id", None) != correlation_id:
                raise ValueError("SMT source-pick correlation ownership mismatch")
        if getattr(inbox, "correlation_id", None) != correlation_id:
            raise ValueError("SMT source-pick inbox correlation mismatch")
        if getattr(inbox, "event_id", None) != request.payload.source_pick_request_event_id:
            raise ValueError("SMT source-pick source event mismatch")
        if plugin_key != "smt_sorting_inbound":
            raise ValueError("SMT source-pick plugin ownership mismatch")
        return {
            "session_id": session_id,
            "workline_id": workline_id,
            "source_pick_inbox_id": source_pick_inbox_id,
            "execution_session_id": execution_session_id,
            "correlation_id": correlation_id,
            "plugin_key": plugin_key,
            "contract_version": contract_version,
        }


__all__ = ["SmtSourcePickCommandHandler"]
