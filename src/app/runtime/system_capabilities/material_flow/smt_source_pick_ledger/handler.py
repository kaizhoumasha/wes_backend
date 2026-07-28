"""SMT source-pick ledger capability handler。"""

from __future__ import annotations

from src.app.runtime.system_capabilities.outcomes import BusinessReject, Success

from .contracts import (
    SmtSourcePickLedgerAdmission,
    SmtSourcePickLedgerInput,
    SmtSourcePickLedgerOutput,
)


class SmtSourcePickLedgerHandler:
    async def __call__(self, request: SmtSourcePickLedgerInput, *, execution: object) -> object:
        from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
            smt_inbound_handoff_service,
        )

        ctx = execution.ctx  # type: ignore[attr-defined]
        admission = execution.admission  # type: ignore[attr-defined]
        if not isinstance(admission, SmtSourcePickLedgerAdmission):
            raise TypeError("SMT source-pick ledger effect requires typed admission")
        if admission.precondition.expected_status != "CLAIMED_BY_SORTING":
            raise ValueError("SMT source-pick ledger precondition is invalid")

        command_id = getattr(ctx["inbox"], "command_id", None)
        if not isinstance(command_id, int):
            return BusinessReject(
                reason_code="COMMAND_ID_MISSING",
                message="SMT source-pick ledger requires callback command identity",
            )
        try:
            result = await smt_inbound_handoff_service.record_source_pick_success(
                ctx["db"],
                command_id=command_id,
                command_code=request.command_code,
                session=ctx["session"],
                trace_id=ctx.get("trace_id"),
            )
        except ValueError:
            return BusinessReject(
                reason_code="SMT_SOURCE_PICK_LEDGER_EVIDENCE_MISMATCH",
                message="SMT source-pick ledger evidence mismatch",
            )
        if result.outcome == "manual_hold":
            source_item = result.source_item
            return BusinessReject(
                reason_code="SMT_SOURCE_PICK_MANUAL_HOLD",
                message="SMT source-pick ledger is on manual hold",
                details={
                    "durable_reject_required": True,
                    "handoff_demand_id": source_item.handoff_demand_id,
                    "source_item_id": source_item.id,
                    "claim_attempt_no": source_item.claim_attempt_no,
                    "source_pick_inbox_id": source_item.source_pick_inbox_id,
                    "command_id": command_id,
                    "command_code": request.command_code,
                },
            )
        if result.outcome == "already_terminal":
            return BusinessReject(
                reason_code="SMT_SOURCE_PICK_TERMINAL_CONFLICT",
                message="SMT source-pick ledger is already terminal",
            )
        if result.outcome not in {"advanced", "already_picked"}:
            return BusinessReject(
                reason_code="SMT_SOURCE_PICK_LEDGER_OUTCOME_UNSUPPORTED",
                message="SMT source-pick ledger returned an unsupported outcome",
            )
        return Success(
            payload=SmtSourcePickLedgerOutput(
                status="PICKED",
                advanced=result.advanced,
            )
        )

    async def persist_business_reject(self, outcome: BusinessReject, *, ctx: dict[str, object]) -> bool:
        """在 callback 主事务 rollback 后，以原始命令证据重新加锁并落安全 Hold。"""

        if outcome.reason_code != "SMT_SOURCE_PICK_MANUAL_HOLD":
            return False
        details = outcome.details
        required_ints = (
            "handoff_demand_id",
            "source_item_id",
            "claim_attempt_no",
            "source_pick_inbox_id",
            "command_id",
        )
        if any(not isinstance(details.get(field), int) for field in required_ints):
            raise ValueError("SMT source-pick manual hold compensation evidence is incomplete")
        command_code = details.get("command_code")
        if not isinstance(command_code, str) or not command_code:
            raise ValueError("SMT source-pick manual hold compensation command_code is missing")

        from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
            smt_inbound_handoff_service,
        )

        return await smt_inbound_handoff_service.persist_source_pick_manual_hold_after_reject(
            ctx["db"],  # type: ignore[arg-type]
            handoff_demand_id=details["handoff_demand_id"],
            source_item_id=details["source_item_id"],
            claim_attempt_no=details["claim_attempt_no"],
            source_pick_inbox_id=details["source_pick_inbox_id"],
            command_id=details["command_id"],
            command_code=command_code,
        )


__all__ = ["SmtSourcePickLedgerHandler"]
