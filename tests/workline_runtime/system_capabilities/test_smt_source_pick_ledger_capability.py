"""SMT source-pick ledger capability 的终态与拒绝合同。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import (
    SmtInboundHandoffLedgerResult,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_ledger.contracts import (
    SmtSourcePickLedgerAdmission,
    SmtSourcePickLedgerInput,
    SmtSourcePickLedgerPrecondition,
)
from src.app.runtime.system_capabilities.material_flow.smt_source_pick_ledger.handler import (
    SmtSourcePickLedgerHandler,
)
from src.app.runtime.system_capabilities.outcomes import BusinessReject


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ledger_outcome", "expected_reason"),
    (
        ("manual_hold", "SMT_SOURCE_PICK_MANUAL_HOLD"),
        ("already_terminal", "SMT_SOURCE_PICK_TERMINAL_CONFLICT"),
    ),
)
async def test_late_success_rejects_incompatible_source_pick_terminal(
    monkeypatch: pytest.MonkeyPatch,
    ledger_outcome: str,
    expected_reason: str,
) -> None:
    service_module = import_module("src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service")
    record_success = AsyncMock(
        return_value=SmtInboundHandoffLedgerResult(
            outcome=ledger_outcome,
            advanced=False,
            already_terminal=ledger_outcome == "already_terminal",
            source_item=SimpleNamespace(
                status=ledger_outcome,
                handoff_demand_id=11,
                id=12,
                claim_attempt_no=2,
                source_pick_inbox_id=13,
            ),
        )
    )
    monkeypatch.setattr(
        service_module,
        "smt_inbound_handoff_service",
        SimpleNamespace(record_source_pick_success=record_success),
    )

    outcome = await SmtSourcePickLedgerHandler()(
        SmtSourcePickLedgerInput(operation="RECORD_PICKED", command_code="SC-LATE"),
        execution=SimpleNamespace(
            ctx={
                "db": object(),
                "session": SimpleNamespace(id=31),
                "inbox": SimpleNamespace(command_id=41),
                "trace_id": "trace-late",
            },
            admission=SmtSourcePickLedgerAdmission(
                precondition=SmtSourcePickLedgerPrecondition(expected_status="CLAIMED_BY_SORTING"),
                fact_version="command:0123456789abcdef0123456789abcdef:SUCCESS",
            ),
        ),
    )

    assert isinstance(outcome, BusinessReject)
    assert outcome.reason_code == expected_reason
