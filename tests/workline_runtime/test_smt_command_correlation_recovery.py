"""SMT source-pick command correlation 恢复矩阵。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffSourceItemStatus
from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService
from src.utils.timezone import timezone


def _source_item() -> SimpleNamespace:
    return SimpleNamespace(
        id=12,
        handoff_demand_id=11,
        sorting_session_id=21,
        claim_attempt_no=3,
        source_pick_inbox_id=31,
        source_pick_command_id=None,
        source_pick_command_code=None,
        source_pick_dispatch_key=None,
        status=SmtInboundHandoffSourceItemStatus.PICK_REQUESTED,
        failure_code=None,
        failure_message=None,
        next_attempt_at=None,
    )


def _session() -> SimpleNamespace:
    request = {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 12,
        "claim_attempt_no": 3,
        "event_id": "source-pick-event-31",
    }
    return SimpleNamespace(
        id=21,
        workline_id=7,
        plugin_key="smt_sorting_inbound",
        contract_version="smt_sorting_inbound.v1",
        awaiting_device_command_code="SC-SOURCE-PICK-31",
        context_json={"sorting": {"context_schema_version": 1, "source_pick_request": request}},
    )


def _inbox() -> SimpleNamespace:
    return SimpleNamespace(
        id=31,
        status="PROCESSED",
        workline_id=7,
        workline_session_id=21,
        execution_session_id=61,
        correlation_id="workline-session:SMT-21",
        event_id="source-pick-event-31",
    )


def _command(
    *,
    command_id: int = 41,
    status: str = "PENDING",
    result: str | None = None,
    **param_overrides: object,
) -> SimpleNamespace:
    params = {
        "handoff_demand_id": 11,
        "handoff_source_item_id": 12,
        "claim_attempt_no": 3,
        "source_pick_inbox_id": 31,
        "source_pick_request_event_id": "source-pick-event-31",
        **param_overrides,
    }
    return SimpleNamespace(
        id=command_id,
        command_code="SC-SOURCE-PICK-31",
        correlation_id="workline-session:SMT-21",
        workline_id=7,
        plugin_key="smt_sorting_inbound",
        contract_version="smt_sorting_inbound.v1",
        task_type="SORTING_SOURCE_PICK",
        params=params,
        status=status,
        result=result,
    )


class _CommandRepository:
    def __init__(self, candidates: list[object]) -> None:
        self.candidates = candidates
        self.calls: list[dict[str, object]] = []

    async def list_by_runtime_correlation(self, _db: object, **kwargs: object) -> list[object]:
        self.calls.append(dict(kwargs))
        return list(self.candidates)


class _Repository:
    async def get_source_item_for_update(self, _db: object, _source_item_id: int) -> object:
        raise AssertionError("恢复 helper 已持有 source item，不应重复查询")


class _RecoveryService(SmtInboundHandoffService):
    def __init__(self, candidates: list[object]) -> None:
        super().__init__(repository=_Repository())  # type: ignore[arg-type]
        self.command_repository = _CommandRepository(candidates)
        self.correlations: list[dict[str, object]] = []
        self.successes: list[dict[str, object]] = []
        self.manual_holds: list[dict[str, object]] = []

    async def record_source_pick_command_correlation(self, _db: object, **kwargs: object) -> object:
        self.correlations.append(dict(kwargs))
        item = kwargs.pop("_item", None)
        _ = item
        return SimpleNamespace()

    async def record_source_pick_success(self, _db: object, **kwargs: object) -> object:
        self.successes.append(dict(kwargs))
        return SimpleNamespace(outcome="advanced", advanced=True)

    async def _manual_hold_source_pick_recovery(self, _db: object, **kwargs: object) -> None:
        self.manual_holds.append(dict(kwargs))
        item = kwargs["item"]
        item.status = SmtInboundHandoffSourceItemStatus.MANUAL_HOLD
        item.failure_code = kwargs["failure_code"]
        item.failure_message = kwargs["message"]


async def _recover(
    service: _RecoveryService,
    *,
    item: SimpleNamespace | None = None,
    session: SimpleNamespace | None = None,
) -> tuple[str | None, SimpleNamespace]:
    source_item = item or _source_item()
    recover = getattr(service, "_recover_processed_source_pick_command", None)
    assert recover is not None, "PROCESSED inbox 必须支持严格 command correlation 恢复"
    outcome = await recover(
        object(),
        demand=SimpleNamespace(id=11),
        item=source_item,
        inbox=_inbox(),
        session=session or _session(),
        now=timezone.now_for_db(),
    )
    return outcome, source_item


@pytest.mark.asyncio
async def test_unique_matching_candidate_recovers_command_correlation() -> None:
    command = _command()
    service = _RecoveryService([command])

    outcome, item = await _recover(service)

    assert outcome == "advanced"
    assert service.command_repository.calls == [
        {
            "correlation_id": "workline-session:SMT-21",
            "awaiting_command_code": "SC-SOURCE-PICK-31",
            "workline_id": 7,
            "task_type": "SORTING_SOURCE_PICK",
            "handoff_demand_id": 11,
            "handoff_source_item_id": 12,
            "claim_attempt_no": 3,
            "source_pick_inbox_id": 31,
            "source_pick_request_event_id": "source-pick-event-31",
            "limit": 2,
        }
    ]
    assert service.correlations == [
        {
            "handoff_demand_id": 11,
            "source_item_id": 12,
            "claim_attempt_no": 3,
            "source_pick_inbox_id": 31,
            "command_id": 41,
            "command_code": "SC-SOURCE-PICK-31",
            "dispatch_key": "device-command:SC-SOURCE-PICK-31",
            "session_id": 21,
            "workline_id": 7,
            "execution_session_id": 61,
            "correlation_id": "workline-session:SMT-21",
            "plugin_key": "smt_sorting_inbound",
            "contract_version": "smt_sorting_inbound.v1",
            "trace_id": None,
        }
    ]
    assert service.successes == []
    assert service.manual_holds == []
    assert item.status == SmtInboundHandoffSourceItemStatus.PICK_REQUESTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidates", "message_fragment"),
    [
        ([], "0"),
        ([_command(command_id=41), _command(command_id=42)], "2"),
        ([_command(handoff_source_item_id=999)], "evidence"),
    ],
    ids=["zero", "multiple", "payload-mismatch"],
)
async def test_ambiguous_or_mismatched_candidates_enter_manual_hold(
    candidates: list[object],
    message_fragment: str,
) -> None:
    service = _RecoveryService(candidates)

    outcome, item = await _recover(service)

    assert outcome == "manual_hold"
    assert service.correlations == []
    assert service.successes == []
    assert len(service.manual_holds) == 1
    assert message_fragment in str(service.manual_holds[0]["message"])
    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD


@pytest.mark.asyncio
async def test_unique_success_candidate_recovers_correlation_and_advances_to_picked_in_same_scan() -> None:
    service = _RecoveryService([_command(status="COMPLETED", result="SUCCESS")])

    outcome, _item = await _recover(service)

    assert outcome == "advanced"
    assert len(service.correlations) == 1
    assert service.successes == [
        {
            "handoff_demand_id": 11,
            "source_item_id": 12,
            "claim_attempt_no": 3,
            "source_pick_inbox_id": 31,
            "command_id": 41,
        }
    ]
    assert service.manual_holds == []


@pytest.mark.asyncio
async def test_unique_failed_candidate_records_correlation_then_enters_manual_hold() -> None:
    service = _RecoveryService([_command(status="FAILED", result="FAILED")])

    outcome, item = await _recover(service)

    assert outcome == "manual_hold"
    assert len(service.correlations) == 1
    assert service.successes == []
    assert len(service.manual_holds) == 1
    assert "失败终态" in str(service.manual_holds[0]["message"])
    assert item.status == SmtInboundHandoffSourceItemStatus.MANUAL_HOLD


@pytest.mark.asyncio
async def test_repeated_success_scan_does_not_create_a_second_correlation() -> None:
    command = _command(status="COMPLETED", result="SUCCESS")
    service = _RecoveryService([command])
    item = _source_item()

    first, _ = await _recover(service, item=item)
    # 模拟现有 correlation/success API 的持久化结果，再次扫描同一行。
    item.source_pick_command_id = command.id
    item.source_pick_command_code = command.command_code
    item.source_pick_dispatch_key = f"device-command:{command.command_code}"
    item.status = SmtInboundHandoffSourceItemStatus.PICKED
    second, _ = await _recover(service, item=item)

    assert first == "advanced"
    assert second is None
    assert len(service.correlations) == 1
    assert len(service.successes) == 1


@pytest.mark.asyncio
async def test_command_repository_uses_correlation_index_entry_with_two_row_budget() -> None:
    from src.app.device.repositories.command_repository import DeviceCommandRepository

    repository = DeviceCommandRepository()
    build_statement = getattr(repository, "build_runtime_correlation_statement", None)
    assert build_statement is not None, "command recovery 必须复用 correlation_id 索引入口"

    statement = build_statement(
        correlation_id="workline-session:SMT-21",
        awaiting_command_code="SC-SOURCE-PICK-31",
        workline_id=7,
        task_type="SORTING_SOURCE_PICK",
        handoff_demand_id=11,
        handoff_source_item_id=12,
        claim_attempt_no=3,
        source_pick_inbox_id=31,
        source_pick_request_event_id="source-pick-event-31",
        limit=2,
    )
    compiled = str(statement)

    assert "correlation_id" in compiled
    assert getattr(statement, "_limit_clause", None) is not None
    assert int(statement._limit_clause.value) == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_command_repository_filters_evidence_before_two_row_budget(db_session: Any) -> None:
    from src.app.device.models.command import CommandStatus, DeviceCommand
    from src.app.device.repositories.command_repository import DeviceCommandRepository

    unrelated = DeviceCommand(
        device_id=1,
        task_type="SORTING_SOURCE_PICK",
        command_code="SC-UNRELATED-FIRST",
        correlation_id="workline-session:SMT-21",
        workline_id=7,
        params={
            "handoff_demand_id": 999,
            "handoff_source_item_id": 999,
            "claim_attempt_no": 999,
            "source_pick_inbox_id": 999,
            "source_pick_request_event_id": "unrelated-event",
        },
        status=CommandStatus.PENDING,
    )
    valid = DeviceCommand(
        device_id=1,
        task_type="SORTING_SOURCE_PICK",
        command_code="SC-SOURCE-PICK-31",
        correlation_id="workline-session:SMT-21",
        workline_id=7,
        params={
            "handoff_demand_id": 11,
            "handoff_source_item_id": 12,
            "claim_attempt_no": 3,
            "source_pick_inbox_id": 31,
            "source_pick_request_event_id": "source-pick-event-31",
        },
        status=CommandStatus.PENDING,
    )
    db_session.add_all([unrelated, valid])
    await db_session.flush()

    candidates = await DeviceCommandRepository().list_by_runtime_correlation(
        db_session,
        correlation_id="workline-session:SMT-21",
        awaiting_command_code="SC-SOURCE-PICK-31",
        workline_id=7,
        task_type="SORTING_SOURCE_PICK",
        handoff_demand_id=11,
        handoff_source_item_id=12,
        claim_attempt_no=3,
        source_pick_inbox_id=31,
        source_pick_request_event_id="source-pick-event-31",
        limit=2,
    )

    assert [candidate.command_code for candidate in candidates] == ["SC-SOURCE-PICK-31"]


class _RecoveryScanRepository:
    async def list_due_recovery_demands(self, _db: object, **_kwargs: object) -> list[object]:
        return []

    async def list_stuck_source_items_for_recovery(self, _db: object, **_kwargs: object) -> list[object]:
        return [SimpleNamespace(id=1), SimpleNamespace(id=2)]


class _NestedTransaction:
    def __init__(self, db: _SavepointDB) -> None:
        self.db = db

    async def __aenter__(self) -> None:
        self.db.entered += 1

    async def __aexit__(self, exc_type: object, _exc: object, _tb: object) -> bool:
        if exc_type is not None:
            self.db.rolled_back += 1
        return False


class _SavepointDB:
    def __init__(self) -> None:
        self.entered = 0
        self.rolled_back = 0

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction(self)


class _SavepointRecoveryService(SmtInboundHandoffService):
    def __init__(self) -> None:
        super().__init__(repository=_RecoveryScanRepository())  # type: ignore[arg-type]
        self.recovered_ids: list[int] = []

    async def _recover_stuck_source_item(self, _db: object, item: object, *, now: object) -> str:
        _ = now
        item_id = int(item.id)  # type: ignore[attr-defined]
        self.recovered_ids.append(item_id)
        if item_id == 1:
            raise RuntimeError("first recovery failed")
        return "advanced"


@pytest.mark.asyncio
async def test_recovery_scan_uses_savepoint_per_item_and_continues_after_failure() -> None:
    service = _SavepointRecoveryService()
    db = _SavepointDB()

    summary = await service.scan_smt_inbound_handoff_demands_batch(
        db,  # type: ignore[arg-type]
        scan_limit=0,
        recovery_limit=2,
        claim_limit=0,
    )

    assert service.recovered_ids == [1, 2]
    assert db.entered == 2
    assert db.rolled_back == 1
    assert summary["recovery_errors"] == 1
    assert summary["advanced"] == 1
