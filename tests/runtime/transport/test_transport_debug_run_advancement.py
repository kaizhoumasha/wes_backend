from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.transport.contracts import RackPosition, TransportCaller, TransportContractError, TransportHandle
from src.app.transport.debug_run_evidence import Scan12EvidenceDisposition, Scan12EvidenceEvaluation
from src.app.transport.debug_run_service import TransportDebugRunService
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportMember, TransportTask
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

NOW = datetime(2026, 9, 2, 12, 0, 0)
NOT_BEFORE_MS = 1_725_000_000_000
CLIENT_IDS = (
    "01990f0d-1800-7000-8000-000000000001",
    "01990f0d-1800-7000-8000-000000000002",
    "01990f0d-1800-7000-8000-000000000003",
)


class _Context(AbstractAsyncContextManager[object]):
    def __init__(self, db: object) -> None:
        self.db = db

    async def __aenter__(self) -> object:
        return self.db

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class _Sessions:
    def __init__(self) -> None:
        self.db = object()

    def begin(self) -> _Context:
        return _Context(self.db)

    def __call__(self) -> _Context:
        return _Context(self.db)


class _Transport:
    def __init__(self, repository: _Repository) -> None:
        self.repository = repository
        self.calls: list[str] = []
        self.position_checks: list[tuple[str, object, str]] = []
        self.error: Exception | None = None
        self.position_error: Exception | None = None

    async def assert_debug_rack_position_in_session(
        self,
        db: object,
        rack_id: str,
        expected_position: object,
        expected_face: str,
    ) -> None:
        del db
        self.position_checks.append((rack_id, expected_position, expected_face))
        if self.position_error is not None:
            raise self.position_error

    async def create_debug_task_in_session(self, db: object, request: object) -> TransportHandle:
        del db
        if self.error is not None:
            raise self.error
        client_request_id = request.client_request_id  # type: ignore[attr-defined]
        self.calls.append(client_request_id)
        task_id = f"transport-{len(self.calls)}"
        self.repository.tasks[task_id] = _task(task_id, client_request_id, request.kind.value)  # type: ignore[attr-defined]
        return TransportHandle(task_id, client_request_id)

    async def is_unsent_debug_task_finalizable_in_session(self, db: object, task_id: str) -> bool:
        del db, task_id
        return False


class _Repository:
    def __init__(self, run: TransportDebugRun, step: TransportDebugRunStep) -> None:
        self.run = run
        self.steps = [step]
        self.tasks: dict[str, TransportTask] = {}
        self.members: dict[str, list[TransportMember]] = {}
        self.evidences: list[InboundEvidence] = []
        self.conflicting_evidence_ids: set[int] = set()
        self.transport_conflicting_task_ids: set[str] = set()
        self.pending_transport_evidence = False
        self.claimed = False

    async def claim_run(
        self,
        db: object,
        *,
        run_id: str,
        token: str,
        now: datetime,
        claim_until: datetime,
    ) -> bool:
        del db
        if self.run.run_id != run_id or self.run.active_scope != "GLOBAL":
            return False
        if self.run.claim_until is not None and self.run.claim_until >= now:
            return False
        self.run.claim_token = token
        self.run.claim_until = claim_until
        return True

    async def claim_active_runs(
        self,
        db: object,
        *,
        token: str,
        now: datetime,
        claim_until: datetime,
        limit: int,
    ) -> list[tuple[str, str]]:
        del limit
        claimed = await self.claim_run(
            db,
            run_id=self.run.run_id,
            token=token,
            now=now,
            claim_until=claim_until,
        )
        return [(self.run.run_id, token)] if claimed else []

    async def get_claimed_run(
        self,
        db: object,
        *,
        run_id: str,
        token: str,
        now: datetime,
    ) -> TransportDebugRun | None:
        del db
        if (
            self.run.run_id == run_id
            and self.run.claim_token == token
            and self.run.claim_until is not None
            and self.run.claim_until > now
        ):
            return self.run
        return None

    async def get_run(self, db: object, run_id: str, *, for_update: bool = False) -> TransportDebugRun | None:
        del db, for_update
        return self.run if self.run.run_id == run_id else None

    async def get_current_step(
        self,
        db: object,
        run: TransportDebugRun,
        *,
        for_update: bool = False,
    ) -> TransportDebugRunStep:
        del db, run, for_update
        return self.steps[self.run.current_step_ordinal]

    async def add_step(self, db: object, step: TransportDebugRunStep) -> None:
        del db
        self.steps.append(step)

    async def list_steps(self, db: object, run_id: str) -> list[TransportDebugRunStep]:
        del db, run_id
        return self.steps

    async def get_transport_task(self, db: object, transport_task_id: str) -> TransportTask | None:
        del db
        return self.tasks.get(transport_task_id)

    async def list_transport_tasks(
        self,
        db: object,
        transport_task_ids: list[str],
    ) -> dict[str, TransportTask]:
        del db
        return {task_id: self.tasks[task_id] for task_id in transport_task_ids if task_id in self.tasks}

    async def list_transport_members(self, db: object, transport_task_id: str) -> list[TransportMember]:
        del db
        return self.members.get(transport_task_id, [])

    async def max_device_evidence_id(self, db: object) -> int:
        del db
        return max((evidence.id or 0 for evidence in self.evidences), default=100)

    async def list_device_evidences_since(
        self,
        db: object,
        *,
        received_at: datetime,
        evidence_high_watermark: int,
        after_received_at: datetime | None,
        after_id: int | None,
        limit: int,
    ) -> list[InboundEvidence]:
        del db
        items = [
            item
            for item in self.evidences
            if item.id is not None
            and item.id > evidence_high_watermark
            and item.received_at >= received_at
            and (
                after_received_at is None
                or item.received_at > after_received_at
                or (item.received_at == after_received_at and after_id is not None and item.id > after_id)
            )
        ]
        return sorted(items, key=lambda item: (item.received_at, item.id or 0))[:limit]

    async def has_evidence_conflicts(self, db: object, evidence_ids: list[int]) -> bool:
        del db
        return bool(self.conflicting_evidence_ids.intersection(evidence_ids))

    async def has_transport_evidence_conflict(self, db: object, run_id: str) -> bool:
        del db
        return run_id == self.run.run_id and bool(self.transport_conflicting_task_ids)

    async def has_pending_transport_evidence(self, db: object, run_id: str) -> bool:
        del db
        return run_id == self.run.run_id and self.pending_transport_evidence

    async def has_run_observed_evidence_conflict(self, db: object, run_id: str) -> bool:
        del db
        return run_id == self.run.run_id and any(
            evidence_id in self.conflicting_evidence_ids
            for step in self.steps
            for item in step.observed_bins_json
            if isinstance(item, dict)
            for evidence_id in [item.get("evidence_id")]
            if isinstance(evidence_id, int)
        )


class _Publisher:
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool:
        del channel, event_type, payload
        return True


def _harness(
    *,
    phase: str = "RACK_TO_STATION",
    status: str = "PENDING",
    task_id: str | None = None,
) -> tuple[TransportDebugRunService, _Repository, _Transport]:
    run = TransportDebugRun(
        run_id="debug-run-1",
        status="RUNNING",
        active_scope="GLOBAL",
        rack_id="510056",
        configuration_json={
            "workline_code": "DEBUG-LINE",
            "workline_id": 1,
            "rack_id": "510056",
            "face_groups": [
                {
                    "face": "90",
                    "bins": [
                        {"bin_code": "A000001922", "slot_id": "SLOT-01"},
                        {"bin_code": "A000002653", "slot_id": "SLOT-02"},
                    ],
                }
            ],
            "return_queues": {"0": ["A000001922", "A000002653"]},
            "return_batches": {
                "0": {
                    "moves": [
                        {"bin_code": "A000001922", "rack_id": "510056", "rack_face": "90", "slot_id": "SLOT-01"},
                        {"bin_code": "A000002653", "rack_id": "510056", "rack_face": "90", "slot_id": "SLOT-02"},
                    ]
                }
            },
            "storage_zone": "WH05",
            "workstation": "KT16",
            "infeed_position": "CNV0301",
            "outfeed_position": "CNV0302",
            "rack_out_template": "CTU01",
            "rack_rotate_template": "CTU02",
            "rack_return_template": "CTU03",
            "rack_return_face": "90",
        },
        current_group_index=0,
        current_phase=phase,
        current_step_ordinal=0,
        version=1,
        created_by_user_id=7,
        created_at=NOW,
        updated_at=NOW,
    )
    step = TransportDebugRunStep(
        run_id=run.run_id,
        ordinal=0,
        group_index=0,
        phase=phase,
        status=status,
        client_request_id=None if phase == "WAIT_SCAN12" else CLIENT_IDS[0],
        transport_task_id=task_id,
        evidence_high_watermark=100 if phase == "WAIT_SCAN12" else None,
        evidence_not_before_ms=NOT_BEFORE_MS if phase == "WAIT_SCAN12" else None,
        reason_code="TRANSPORT_DELIVERY_UNKNOWN" if status == "NEEDS_ATTENTION" else None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = _Repository(run, step)
    transport = _Transport(repository)
    service = TransportDebugRunService(
        _Sessions(),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        transport,  # type: ignore[arg-type]
        clock=lambda: NOW,
        event_publisher=_Publisher(),
    )
    # 通用推进用例隔离货架事实；下方退箱事实用例直接验证真实校验器。
    service._assert_return_rack_fact = AsyncMock()
    if phase == "BINS_TO_RACK":
        payload = {
            "operation": "outbound.bin.return_batch@v1",
            "operation_id": CLIENT_IDS[1],
            "timestamp": NOT_BEFORE_MS,
            "data": {
                "workline_code": "DEBUG-LINE",
                "rack_id": "510056",
                "rack_face": "90",
                "return_candidates": [
                    {
                        "sequence_no": index,
                        "bin_code": code,
                        "source": {"type": "HANDOFF_POSITION", "location_code": "CNV0302"},
                    }
                    for index, code in enumerate(run.configuration_json["return_queues"]["0"], 1)
                ],
            },
        }
        batch = run.configuration_json["return_batches"]["0"]
        batch.update(
            operation_id=CLIENT_IDS[1], workline_id=1, step_client_request_id=CLIENT_IDS[0], response_evidence_id=11
        )
        run.configuration_json["return_requests"] = {CLIENT_IDS[1]: payload}
        _install_return_response(
            service,
            repository,
            payload,
            {
                "operation_id": CLIENT_IDS[1],
                "code": "DECIDED",
                "timestamp": NOT_BEFORE_MS,
                "data": {
                    "result": "READY",
                    "moves": [
                        {
                            "sequence_no": index,
                            "bin_code": move["bin_code"],
                            "target": {
                                "type": "RACK_BIN_SLOT",
                                **{key: move[key] for key in ("rack_id", "rack_face", "slot_id")},
                            },
                        }
                        for index, move in enumerate(batch["moves"], 1)
                    ],
                },
            },
        )
    return service, repository, transport


async def test_pending_intent_reuses_persisted_client_id_across_scans() -> None:
    service, repository, transport = _harness()

    assert await service.advance_active_runs(100) == 1
    assert await service.advance_active_runs(100) == 0

    assert transport.calls == [CLIENT_IDS[0]]
    assert repository.steps[0].client_request_id == CLIENT_IDS[0]
    assert repository.steps[0].transport_task_id == "transport-1"
    assert repository.steps[0].status == "WAITING"


async def test_bin_move_uses_frozen_operator_input_without_resource_mounts() -> None:
    service, repository, transport = _harness(phase="BINS_TO_INFEED")

    assert await service.advance_run("debug-run-1") is True

    assert transport.calls == [CLIENT_IDS[0]]
    assert transport.position_checks == [("510056", RackPosition("KT16"), "90")]
    assert repository.steps[0].status == "WAITING"
    assert repository.run.status == "RUNNING"


async def test_rotation_rechecks_the_previous_face_before_moving_to_the_next_group() -> None:
    service, repository, transport = _harness(phase="ROTATE_TO_NEXT_FACE")
    repository.run.configuration_json["face_groups"].append(
        {"face": "270", "bins": [{"bin_code": "A000003001", "slot_id": "SLOT-03"}]}
    )
    repository.run.current_group_index = 1
    repository.steps[0].group_index = 1

    assert await service.advance_run("debug-run-1") is True

    assert transport.position_checks == [("510056", RackPosition("KT16"), "90")]


async def test_rack_return_rechecks_the_last_selected_face() -> None:
    service, repository, transport = _harness(phase="RACK_TO_STORAGE")
    repository.run.configuration_json["face_groups"].append(
        {"face": "270", "bins": [{"bin_code": "A000003001", "slot_id": "SLOT-03"}]}
    )
    repository.run.current_group_index = 1
    repository.steps[0].group_index = 1

    assert await service.advance_run("debug-run-1") is True

    assert transport.position_checks == [("510056", RackPosition("KT16"), "270")]


async def test_transport_contract_rejection_becomes_attention_without_retry_loop() -> None:
    service, repository, transport = _harness()
    transport.error = TransportContractError("rack current position is not confirmed")

    assert await service.advance_run("debug-run-1") is True

    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "TRANSPORT_CONTRACT_REJECTED"
    assert repository.run.claim_token is None


@pytest.mark.parametrize("reason", ["foreign key", "not null", "integrity failure"])
async def test_transport_integrity_error_propagates_without_resource_attention(reason: str) -> None:
    service, repository, transport = _harness()
    error = IntegrityError("INSERT", {}, RuntimeError(reason))
    transport.error = error

    with pytest.raises(IntegrityError) as raised:
        await service.advance_run("debug-run-1")

    assert raised.value is error
    assert repository.run.status == "RUNNING"
    assert repository.run.attention_code is None
    assert repository.steps[0].status == "PENDING"
    assert repository.steps[0].transport_task_id is None


async def test_transport_step_without_task_identity_fails_closed() -> None:
    service, repository, _ = _harness(status="WAITING")

    changed = await service._advance_transport_step(
        object(),  # type: ignore[arg-type]
        repository.run,
        repository.steps[0],
        NOW,
    )

    assert changed is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "TRANSPORT_TASK_MISSING"


async def test_succeeded_transport_creates_one_next_intent_with_evidence_boundary(monkeypatch) -> None:
    from unittest.mock import Mock

    from src.app.transport import debug_run_service as module

    wake = Mock()
    monkeypatch.setattr(module, "defer_wakeup", wake)
    service, repository, _ = _harness(task_id="transport-1", status="WAITING")
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "RACK_MOVE", status="SUCCEEDED")
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]

    service._task_queue = Mock()
    assert await service.advance_run("debug-run-1") is True
    wake.assert_called_once_with(service._sessions.db, service._task_queue.enqueue_transport_debug)

    assert repository.steps[0].status == "SUCCEEDED"
    assert len(repository.steps) == 2
    next_step = repository.steps[1]
    assert (next_step.ordinal, next_step.phase, next_step.status) == (1, "BINS_TO_INFEED", "PENDING")
    assert next_step.evidence_high_watermark == 100
    assert next_step.evidence_not_before_ms is not None
    assert next_step.client_request_id is not None and next_step.client_request_id != CLIENT_IDS[0]
    assert repository.run.current_step_ordinal == 1


async def test_evidence_boundary_rounds_up_to_exclude_an_earlier_record_in_the_same_millisecond() -> None:
    service, repository, _ = _harness(task_id="transport-1", status="WAITING")
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "RACK_MOVE", status="SUCCEEDED")
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]
    submillisecond_start = datetime(2026, 9, 2, 12, 0, 0, 900)
    service._clock = lambda: submillisecond_start

    assert await service.advance_run("debug-run-1") is True
    assert repository.steps[1].evidence_not_before_ms == (
        int(timezone.to_utc(submillisecond_start).timestamp()) * 1000 + 1
    )


async def test_conflicting_transport_callback_blocks_the_next_physical_step() -> None:
    service, repository, _ = _harness(task_id="transport-1", status="WAITING")
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "RACK_MOVE", status="SUCCEEDED")
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]
    repository.transport_conflicting_task_ids.add("transport-1")

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "TRANSPORT_EVIDENCE_CONFLICT"
    assert len(repository.steps) == 1


async def test_pending_transport_callback_waits_before_creating_the_next_step() -> None:
    service, repository, _ = _harness(task_id="transport-1", status="WAITING")
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "RACK_MOVE", status="SUCCEEDED")
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]
    repository.pending_transport_evidence = True

    assert await service.advance_run("debug-run-1") is False
    assert repository.run.status == "RUNNING"
    assert len(repository.steps) == 1


async def test_bin_return_reports_only_exactly_confirmed_members_before_aggregate_success() -> None:
    service, repository, transport = _harness(phase="BINS_TO_RACK", status="WAITING", task_id="transport-1")
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "BIN_MOVE", status="ACCEPTED")
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="BIN",
            object_id="A000001922",
            source={"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
            target={"kind": "RACK_BIN_SLOT", "rack_id": "510056", "rack_face": "90", "slot_id": "SLOT-01"},
            face=None,
        ),
        _member(
            "transport-1",
            object_type="BIN",
            object_id="A000002653",
            source={"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
            target={"kind": "RACK_BIN_SLOT", "rack_id": "510056", "rack_face": "90", "slot_id": "SLOT-02"},
            face=None,
        ),
    ]
    repository.members["transport-1"][1].status = "PENDING"
    repository.members["transport-1"][1].final_position_json = None

    assert await service.advance_run("debug-run-1") is True
    partial = await service.get_run("debug-run-1")
    assert partial.current_phase == "BINS_TO_RACK"
    assert partial.observed_bin_codes == ("A000001922",)
    assert partial.current_step is not None and partial.current_step.status == "WAITING"
    assert len(repository.steps) == 1
    assert transport.calls == []

    repository.members["transport-1"][1].status = "SUCCEEDED"
    repository.members["transport-1"][1].final_position_json = repository.members["transport-1"][1].target_json
    assert await service.advance_run("debug-run-1") is True
    assert (await service.get_run("debug-run-1")).observed_bin_codes == ("A000001922", "A000002653")
    assert len(repository.steps) == 1

    repository.tasks["transport-1"].status = "SUCCEEDED"
    assert await service.advance_run("debug-run-1") is True
    assert repository.run.current_phase == "RACK_TO_STORAGE"


async def test_bin_return_does_not_count_wrong_target_or_unknown_position_as_confirmed() -> None:
    service, repository, _ = _harness(phase="BINS_TO_RACK", status="WAITING", task_id="transport-1")
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "BIN_MOVE", status="ACCEPTED")
    member = _member(
        "transport-1",
        object_type="BIN",
        object_id="A000001922",
        source={"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
        target={"kind": "RACK_BIN_SLOT", "rack_id": "510056", "rack_face": "90", "slot_id": "SLOT-01"},
        face=None,
    )
    member.final_position_json = {"kind": "RACK_BIN_SLOT", "rack_id": "510056", "rack_face": "90", "slot_id": "WRONG"}
    repository.members["transport-1"] = [member]

    assert await service.advance_run("debug-run-1") is False
    assert (await service.get_run("debug-run-1")).observed_bin_codes == ()
    assert repository.run.current_phase == "BINS_TO_RACK"
    assert len(repository.steps) == 1

    member.final_position_json = None
    member.position_unknown = True
    repository.tasks["transport-1"].status = "RECONCILING"
    repository.tasks["transport-1"].reason_code = "TRANSPORT_POSITION_UNKNOWN"
    assert await service.advance_run("debug-run-1") is True
    unknown = await service.get_run("debug-run-1")
    assert unknown.observed_bin_codes == ()
    assert unknown.current_phase == "BINS_TO_RACK"
    assert unknown.status == "NEEDS_ATTENTION"
    assert unknown.attention_code == "TRANSPORT_POSITION_UNKNOWN"
    assert len(repository.steps) == 1


async def test_scan12_freezes_available_fifo_without_waiting_for_unscanned_bins() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.run.configuration_json["return_queues"] = {}
    repository.evidences = [
        _scan(101, "EVENT-A", "A000001922"),
        _scan(102, "EVENT-A-DUP", "A000001922"),
        _scan(103, "EVENT-C", "OTHER-BIN"),
    ]
    assert await service.advance_run("debug-run-1")
    assert repository.run.current_phase == "BINS_TO_RACK"
    assert repository.run.configuration_json["return_queues"]["0"] == ["A000001922"]
    assert repository.steps[0].observed_bins_json[0]["evidence_id"] == 101


async def test_partial_return_resumes_scan_with_original_boundary_and_does_not_return_twice() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.run.configuration_json["return_queues"] = {}
    repository.evidences = [_scan(101, "EVENT-A", "A000001922")]
    assert await service.advance_run("debug-run-1")
    assert repository.run.current_phase == "BINS_TO_RACK"
    returned_step = repository.steps[-1]
    repository.run.configuration_json["returned_bins"] = [{"bin_code": "A000001922"}]
    await service._append_next_step(service._sessions.db, repository.run, returned_step, NOW)
    assert repository.run.current_phase == "WAIT_SCAN12"
    assert repository.steps[-1].evidence_high_watermark == 100
    assert repository.steps[-1].evidence_not_before_ms == NOT_BEFORE_MS
    await service.advance_run("debug-run-1")
    assert repository.run.current_phase == "WAIT_SCAN12"
    repository.evidences.append(_scan(102, "EVENT-B", "A000002653"))
    assert await service.advance_run("debug-run-1")
    assert repository.run.current_phase == "BINS_TO_RACK"
    assert repository.run.configuration_json["return_queues"]["0"] == ["A000001922", "A000002653"]


async def test_pending_scan_allows_confirmed_fifo_prefix_but_not_later_bins() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.run.configuration_json["return_queues"] = {}
    repository.evidences = [
        _scan(101, "EVENT-A", "A000001922"),
        _scan(102, "EVENT-B", "A000002653", apply_status="PENDING"),
    ]
    assert await service.advance_run("debug-run-1")
    assert repository.run.current_phase == "BINS_TO_RACK"
    assert repository.run.configuration_json["return_queues"]["0"] == ["A000001922"]


async def test_scan12_pending_selected_evidence_waits_without_advancing_cursor() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    pending = _scan(101, "EVENT-A", "A000001922")
    pending.apply_status = InboundEvidenceApplyStatus.PENDING
    repository.evidences = [pending, _scan(102, "EVENT-B", "A000002653")]

    assert await service.advance_run("debug-run-1") is False
    assert repository.steps[0].evidence_high_watermark == 100
    assert repository.steps[0].observed_bins_json == []

    pending.apply_status = InboundEvidenceApplyStatus.APPLIED
    assert await service.advance_run("debug-run-1") is True
    assert repository.steps[0].evidence_high_watermark == 100
    assert {item["bin_code"] for item in repository.steps[0].observed_bins_json} == {
        "A000001922",
        "A000002653",
    }


async def test_scan12_pages_past_full_irrelevant_page_without_moving_boundary() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [
        _scan(evidence_id, f"IRRELEVANT-{evidence_id}", "OTHER-BIN") for evidence_id in range(101, 1101)
    ]
    repository.evidences.append(_scan(1101, "EVENT-A", "A000001922"))

    assert await service.advance_run("debug-run-1") is True
    assert repository.steps[0].evidence_high_watermark == 100
    assert repository.steps[0].observed_bins_json == [
        {
            "bin_code": "A000001922",
            "evidence_id": 1101,
            "source_event_id": "EVENT-A",
        }
    ]


async def test_scan12_does_not_permanently_skip_a_late_committing_lower_id() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [_scan(102, "IRRELEVANT-102", "OTHER-BIN")]

    assert await service.advance_run("debug-run-1") is False
    assert repository.steps[0].evidence_high_watermark == 100

    repository.evidences.extend(
        [
            _scan(101, "EVENT-A", "A000001922"),
            _scan(103, "EVENT-B", "A000002653"),
        ]
    )
    assert await service.advance_run("debug-run-1") is True
    assert repository.run.current_phase == "BINS_TO_RACK"


async def test_scan12_rejects_preexisting_watermark_ids_at_the_exact_time_boundary() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    boundary = timezone.to_db_datetime(NOT_BEFORE_MS / 1000)
    assert boundary is not None
    repository.evidences = [
        _scan(99, "OLD-A", "A000001922", received_at=boundary),
        _scan(100, "OLD-B", "A000002653", received_at=boundary),
        _scan(101, "OLD-SAME-MS", "A000001922", received_at=boundary - timedelta(microseconds=400)),
    ]

    assert await service.advance_run("debug-run-1") is False
    assert repository.run.current_phase == "WAIT_SCAN12"
    assert repository.steps[0].observed_bins_json == []


async def test_scan12_rejects_watermark_ids_even_when_received_after_the_time_boundary() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    boundary = timezone.to_db_datetime(NOT_BEFORE_MS / 1000)
    assert boundary is not None
    repository.evidences = [
        _scan(99, "OLD-A", "A000001922", received_at=boundary + timedelta(milliseconds=1)),
        _scan(100, "OLD-B", "A000002653", received_at=boundary + timedelta(milliseconds=1)),
    ]

    assert await service.advance_run("debug-run-1") is False
    assert repository.run.current_phase == "WAIT_SCAN12"
    assert repository.steps[0].observed_bins_json == []


async def test_scan12_conflict_freezes_before_bin_return_step_is_created() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [
        _scan(101, "EVENT-A", "A000001922"),
        _scan(102, "EVENT-B", "A000002653"),
    ]
    repository.conflicting_evidence_ids.add(101)

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
    assert len(repository.steps) == 1


async def test_late_scan12_conflict_is_rechecked_before_creating_bin_return_transport() -> None:
    service, repository, transport = _harness(phase="BINS_TO_RACK", status="PENDING")
    repository.steps[0].observed_bins_json = [
        {"bin_code": "A000001922", "evidence_id": 101, "source_event_id": "EVENT-A"},
        {"bin_code": "A000002653", "evidence_id": 102, "source_event_id": "EVENT-B"},
    ]
    repository.conflicting_evidence_ids.add(101)

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
    assert transport.calls == []


async def test_rack_workstation_drift_blocks_the_next_physical_task() -> None:
    service, repository, transport = _harness(phase="BINS_TO_INFEED")
    transport.position_error = TransportContractError("rack current exact position does not match debug workstation")

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "TRANSPORT_CONTRACT_REJECTED"
    assert transport.calls == []


async def test_late_scan12_conflict_is_rechecked_after_bin_return_transport_is_bound() -> None:
    service, repository, _ = _harness(phase="BINS_TO_RACK", status="WAITING", task_id="transport-1")
    repository.steps[0].observed_bins_json = [
        {"bin_code": "A000001922", "evidence_id": 101, "source_event_id": "EVENT-A"},
        {"bin_code": "A000002653", "evidence_id": 102, "source_event_id": "EVENT-B"},
    ]
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "BIN_MOVE")
    repository.conflicting_evidence_ids.add(101)

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
    assert len(repository.steps) == 1


@pytest.mark.parametrize("resolved_status", [InboundEvidenceApplyStatus.APPLIED, InboundEvidenceApplyStatus.IGNORED])
async def test_reconciled_scan12_reuses_original_run_and_step_after_processing(resolved_status) -> None:
    service, repository, transport = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [
        _scan(101, "EVENT-A", "A000001922", apply_status="RECONCILING"),
        _scan(102, "EVENT-B", "A000002653"),
    ]
    original_step = repository.steps[0]
    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_RECONCILING"
    assert await service.advance_run("debug-run-1") is False
    assert repository.steps == [original_step]
    assert transport.calls == []

    repository.evidences[0].apply_status = resolved_status
    assert await service.advance_run("debug-run-1") is True
    assert repository.run.run_id == "debug-run-1"
    assert repository.run.status == "RUNNING"
    assert repository.run.current_phase == "BINS_TO_RACK"
    assert repository.steps[0] is original_step
    assert original_step.status == "SUCCEEDED"
    assert [item["evidence_id"] for item in original_step.observed_bins_json] == [101, 102]
    assert len(repository.steps) == 2
    assert repository.run.active_scope == "GLOBAL"
    assert transport.calls == []


async def test_reconciled_scan12_does_not_bypass_new_evidence_conflict() -> None:
    service, repository, transport = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [
        _scan(101, "EVENT-A", "A000001922", apply_status="RECONCILING"),
        _scan(102, "EVENT-B", "A000002653"),
    ]
    assert await service.advance_run("debug-run-1")
    repository.evidences[0].apply_status = InboundEvidenceApplyStatus.IGNORED
    repository.conflicting_evidence_ids.add(101)
    assert await service.advance_run("debug-run-1")
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
    assert len(repository.steps) == 1
    assert not await service.advance_run("debug-run-1")
    assert transport.calls == []


async def test_scan12_payload_and_evidence_device_identity_conflict_needs_attention() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    evidence = _scan(101, "EVENT-A", "A000001922")
    evidence.device_code = "SCAN13"
    repository.evidences = [evidence]

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "DEVICE_IDENTITY_CONFLICT"


async def test_scan12_malformed_match_evaluation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [_scan(101, "EVENT-A", "A000001922")]
    monkeypatch.setattr(
        "src.app.transport.debug_run_service.evaluate_scan12_evidence",
        lambda *args, **kwargs: Scan12EvidenceEvaluation(
            disposition=Scan12EvidenceDisposition.MATCH,
            evidence_id=101,
        ),
    )

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_MATCH_INVALID"


async def test_transport_delivery_unknown_recovers_only_on_same_task_success() -> None:
    service, repository, _ = _harness(task_id="transport-1", status="NEEDS_ATTENTION")
    repository.run.status = "NEEDS_ATTENTION"
    repository.run.attention_code = "TRANSPORT_DELIVERY_UNKNOWN"
    repository.tasks["transport-1"] = _task(
        "transport-1",
        CLIENT_IDS[0],
        "RACK_MOVE",
        status="RECONCILING",
        reason_code="TRANSPORT_DELIVERY_UNKNOWN",
    )

    assert await service.advance_run("debug-run-1") is False
    assert repository.run.status == "NEEDS_ATTENTION"

    repository.tasks["transport-1"].status = "SUCCEEDED"
    repository.tasks["transport-1"].reason_code = None
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "RUNNING"
    assert repository.run.current_phase == "BINS_TO_INFEED"


async def test_transport_result_timeout_recovers_only_on_same_task_success() -> None:
    service, repository, _ = _harness(task_id="transport-1", status="NEEDS_ATTENTION")
    repository.run.status = "NEEDS_ATTENTION"
    repository.run.attention_code = "TRANSPORT_RESULT_TIMEOUT"
    repository.steps[0].reason_code = "TRANSPORT_RESULT_TIMEOUT"
    repository.tasks["transport-1"] = _task(
        "transport-1",
        CLIENT_IDS[0],
        "RACK_MOVE",
        status="RECONCILING",
        reason_code="TRANSPORT_RESULT_TIMEOUT",
    )

    assert await service.advance_run("debug-run-1") is False
    assert repository.run.status == "NEEDS_ATTENTION"

    repository.tasks["transport-1"].status = "SUCCEEDED"
    repository.tasks["transport-1"].reason_code = None
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "RUNNING"
    assert repository.run.current_phase == "BINS_TO_INFEED"


async def test_transport_position_unknown_recovers_only_on_same_task_success() -> None:
    service, repository, _ = _harness(task_id="transport-1", status="NEEDS_ATTENTION")
    repository.run.status = "NEEDS_ATTENTION"
    repository.run.attention_code = "TRANSPORT_POSITION_UNKNOWN"
    repository.steps[0].reason_code = "TRANSPORT_POSITION_UNKNOWN"
    repository.tasks["transport-1"] = _task(
        "transport-1",
        CLIENT_IDS[0],
        "RACK_MOVE",
        status="RECONCILING",
        reason_code="TRANSPORT_POSITION_UNKNOWN",
    )

    assert await service.advance_run("debug-run-1") is False

    repository.tasks["transport-1"].status = "SUCCEEDED"
    repository.tasks["transport-1"].reason_code = None
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "KT16"},
            face="90",
        )
    ]

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "RUNNING"
    assert repository.run.current_phase == "BINS_TO_INFEED"


def _task(
    task_id: str,
    client_id: str,
    kind: str,
    *,
    status: str = "PENDING",
    reason_code: str | None = None,
) -> TransportTask:
    return TransportTask(
        transport_task_id=task_id,
        client_request_id=client_id,
        request_digest="0" * 64,
        kind=kind,
        caller_json={"workline_id": "TRANSPORT_DEBUG"},
        request_json={},
        submit_operation_id=CLIENT_IDS[1],
        submit_timestamp_ms=1,
        submit_request_body="{}",
        submit_request_body_digest="1" * 64,
        status=status,
        reason_code=reason_code,
        created_at=NOW,
        updated_at=NOW,
    )


def _member(
    task_id: str,
    *,
    object_type: str,
    object_id: str,
    source: dict[str, str],
    target: dict[str, str],
    face: str | None,
) -> TransportMember:
    return TransportMember(
        transport_task_id=task_id,
        ordinal=0,
        object_type=object_type,
        object_id=object_id,
        source_json=source,
        target_json=target,
        status="SUCCEEDED",
        final_position_json=target,
        position_unknown=False,
        arrival_face=face,
        updated_at=NOW,
    )


def _scan(
    evidence_id: int,
    source_event_id: str,
    barcode: str,
    *,
    apply_status: str = "APPLIED",
    received_at: datetime = NOW,
) -> InboundEvidence:
    return InboundEvidence(
        id=evidence_id,
        kind=InboundEvidenceKind.DEVICE_EVENT,
        source_identity=source_event_id,
        payload_digest="a" * 64,
        normalized_payload={
            "device_code": "SCAN12",
            "contract_key": "device.event",
            "contract_version": "1.0",
            "event_type": "SCAN_COMPLETED",
            "timestamp": NOT_BEFORE_MS,
            "source_event_id": source_event_id,
            "is_debug": True,
            "data": {"barcode": barcode},
        },
        received_at=received_at,
        device_code="SCAN12",
        contract_key="device.event",
        contract_version="1.0",
        apply_status=InboundEvidenceApplyStatus(apply_status),
    )


async def test_wms_return_request_persists_fifo_identity_without_transport_and_waits_on_restart(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    service, repository, transport = _harness(phase="BINS_TO_RACK")
    from unittest.mock import Mock

    wake = Mock()
    service._task_queue = Mock()
    monkeypatch.setattr("src.app.transport.debug_run_service.defer_wakeup", wake)
    repository.run.configuration_json["return_batches"] = {}
    repository.run.configuration_json["return_queues"] = {"0": ["A000002653", "A000001922"]}
    service._wms = SimpleNamespace(create_or_get=AsyncMock())
    service._confirmations = SimpleNamespace(
        get_by_identity_for_update=AsyncMock(return_value=SimpleNamespace(status="PENDING"))
    )
    assert await service.advance_run("debug-run-1")
    assert any(call.args[1] == service._task_queue.enqueue_wms_confirmations for call in wake.call_args_list)
    call = service._wms.create_or_get.call_args.kwargs
    assert call["workline_id"] == 1
    assert call["operation"] == "outbound.bin.return_batch@v1"
    assert [item["bin_code"] for item in call["request_payload"]["data"]["return_candidates"]] == [
        "A000002653",
        "A000001922",
    ]
    assert call["request_payload"]["data"]["return_candidates"][0]["source"]["location_code"] == "CNV0302"
    assert not await service.advance_run("debug-run-1")
    assert service._wms.create_or_get.await_count == 1
    assert transport.calls == []
    assert repository.run.configuration_json["return_batches"]["0"]["operation_id"] == call["operation_id"]


@pytest.mark.parametrize("invalid_source", [None, "missing", "group", "pending", "rack", "face", "slot", "member"])
async def test_wms_no_batch_waits_without_original_slot_fallback(invalid_source: str | None) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    service, repository, transport = _harness(phase="BINS_TO_RACK")
    repository.run.configuration_json["return_batches"] = {}
    repository.run.configuration_json["return_queues"] = {"0": ["A000002653", "A000001922"]}
    source_step = TransportDebugRunStep(
        run_id=repository.run.run_id,
        ordinal=1,
        group_index=1 if invalid_source == "group" else 0,
        phase="BINS_TO_INFEED",
        status="WAITING" if invalid_source == "pending" else "SUCCEEDED",
        transport_task_id="outbound-source",
        created_at=NOW,
        updated_at=NOW,
    )
    if invalid_source != "missing":
        repository.steps.append(source_step)
    repository.members["outbound-source"] = [
        _member(
            "outbound-source",
            object_type="BIN",
            object_id=code,
            source={
                "kind": "RACK_BIN_SLOT",
                "rack_id": "wrong" if invalid_source == "rack" else "510056",
                "rack_face": "270" if invalid_source == "face" else "90",
                "slot_id": "" if invalid_source == "slot" else slot,
            },
            target={"kind": "CONVEYOR_POSITION", "location_code": "CNV0301"},
            face=None,
        )
        for code, slot in [("A000001922", "CONFIRMED-01"), ("A000002653", "CONFIRMED-02")]
    ]
    if invalid_source == "member":
        repository.members["outbound-source"][0].status = "UNKNOWN"
    service._wms = SimpleNamespace(create_or_get=AsyncMock())
    await service.advance_run("debug-run-1")
    first = service._wms.create_or_get.call_args.kwargs
    response = {
        "operation_id": first["operation_id"],
        "code": "DECIDED",
        "timestamp": NOT_BEFORE_MS,
        "data": {"result": "NO_BATCH", "retry_after_ms": 1000},
    }
    _install_return_response(service, repository, first["request_payload"], response)
    assert await service.advance_run("debug-run-1")
    batch = repository.run.configuration_json["return_batches"]["0"]
    assert batch["operation_id"] == first["operation_id"]
    assert service._wms.create_or_get.await_count == 1
    assert repository.steps[0].client_request_id == CLIENT_IDS[0]
    assert repository.run.status == "RUNNING"
    assert repository.steps[0].status == "PENDING"
    assert repository.steps[0].reason_code is None
    assert "moves" not in batch
    assert batch["retry_at"] == (NOW + timedelta(seconds=1)).isoformat()
    assert transport.calls == []
    assert not await service.advance_run("debug-run-1")
    assert service._wms.create_or_get.await_count == 1


def _install_return_response(service, repository, payload, response, *, evidence_id=11):
    confirmation = SimpleNamespace(
        operation=payload["operation"],
        operation_id=payload["operation_id"],
        status="COMPLETED",
        workline_id=repository.run.configuration_json["workline_id"],
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        response_evidence_id=evidence_id,
        response_result=response["data"]["result"],
    )
    evidence = InboundEvidence(
        id=evidence_id,
        kind=InboundEvidenceKind.WMS_RESULT,
        source_identity=f"{payload['operation']}:{payload['operation_id']}",
        operation=payload["operation"],
        operation_id=payload["operation_id"],
        workline_id=confirmation.workline_id,
        payload_digest=canonical_json_digest(response),
        normalized_payload=response,
        apply_status=InboundEvidenceApplyStatus.APPLIED,
        received_at=NOW,
    )
    read = AsyncMock(return_value=evidence)
    service._confirmations = SimpleNamespace(get_by_identity_for_update=AsyncMock(return_value=confirmation))
    service._wms_evidence = SimpleNamespace(get_by_id_without_lock=read, get_by_id_for_update=read)
    return confirmation


async def _no_batch_harness():
    service, repository, transport = _harness(phase="BINS_TO_RACK")
    repository.run.configuration_json["return_batches"] = {}
    service._wms = SimpleNamespace(create_or_get=AsyncMock())
    assert await service.advance_run("debug-run-1")
    first = service._wms.create_or_get.call_args.kwargs
    confirmation = _install_return_response(
        service,
        repository,
        first["request_payload"],
        {
            "operation_id": first["operation_id"],
            "code": "DECIDED",
            "timestamp": NOT_BEFORE_MS,
            "data": {"result": "NO_BATCH", "retry_after_ms": 1000},
        },
    )
    assert await service.advance_run("debug-run-1")
    return service, repository, transport, confirmation, first


async def test_no_batch_deadline_survives_restart_then_new_identity_ready_moves_once() -> None:
    service, repository, transport, confirmation, first = await _no_batch_harness()
    old_payload = dict(confirmation.request_payload)
    for milliseconds in (0, 999):
        restarted = TransportDebugRunService(
            service._sessions,
            repository,
            transport,
            clock=lambda delay=milliseconds: NOW + timedelta(milliseconds=delay),
            event_publisher=_Publisher(),
        )
        restarted._wms = service._wms
        restarted._confirmations = service._confirmations
        restarted._wms_evidence = service._wms_evidence
        restarted._assert_return_rack_fact = service._assert_return_rack_fact
        assert not await restarted.advance_run("debug-run-1")
        assert transport.calls == []
        assert service._wms.create_or_get.await_count == 1
    restarted._clock = lambda: NOW + timedelta(seconds=1)
    assert await restarted.advance_run("debug-run-1")
    second = service._wms.create_or_get.call_args.kwargs
    assert service._wms.create_or_get.await_count == 2
    assert second["operation_id"] != first["operation_id"]
    assert second["request_payload"]["timestamp"] > first["request_payload"]["timestamp"]
    assert second["request_payload"]["data"] == first["request_payload"]["data"]
    assert confirmation.status == "COMPLETED"
    assert confirmation.request_payload == old_payload
    assert repository.run.configuration_json["return_requests"][first["operation_id"]] == old_payload
    restarted._confirmations.get_by_identity_for_update.return_value = SimpleNamespace(status="PENDING")
    assert not await restarted.advance_run("debug-run-1")
    _install_return_response(
        restarted,
        repository,
        second["request_payload"],
        {
            "operation_id": second["operation_id"],
            "code": "DECIDED",
            "timestamp": NOT_BEFORE_MS,
            "data": {
                "result": "READY",
                "moves": [
                    {
                        "sequence_no": 1,
                        "bin_code": "A000001922",
                        "target": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": "510056",
                            "rack_face": "90",
                            "slot_id": "WMS-NEW",
                        },
                    }
                ],
            },
        },
        evidence_id=12,
    )
    assert await restarted.advance_run("debug-run-1")
    assert await restarted.advance_run("debug-run-1")
    assert not await restarted.advance_run("debug-run-1")
    assert transport.calls == [CLIENT_IDS[0]]
    assert service._wms.create_or_get.await_count == 2


@pytest.mark.parametrize(
    "invalidation",
    ["returned", "queue", "group", "step_run", "step_phase", "rack", "source", "owner", "owner_id", "client_id"],
)
async def test_no_batch_invalid_candidate_does_not_request_or_move(invalidation) -> None:
    service, repository, transport, confirmation, first = await _no_batch_harness()
    configuration = repository.run.configuration_json
    if invalidation == "returned":
        configuration["returned_bins"] = [{"bin_code": "A000001922"}]
    elif invalidation == "queue":
        configuration["return_queues"]["0"].reverse()
    elif invalidation == "group":
        repository.steps[0].group_index = 1
    elif invalidation == "step_run":
        repository.steps[0].run_id = "other-run"
    elif invalidation == "step_phase":
        repository.run.current_phase = "WAIT_SCAN12"
    elif invalidation == "rack":
        repository.run.rack_id = "other-rack"
    elif invalidation == "source":
        configuration["outfeed_position"] = "other-position"
    elif invalidation == "owner_id":
        configuration["workline_id"] = 2
    elif invalidation == "client_id":
        repository.steps[0].client_request_id = CLIENT_IDS[2]
    else:
        configuration["workline_code"] = "other-line"
    service._clock = lambda: NOW + timedelta(seconds=1)
    assert await service.advance_run("debug-run-1")
    assert repository.run.status == "NEEDS_ATTENTION"
    assert service._wms.create_or_get.await_count == 1
    assert confirmation.status == "COMPLETED"
    assert repository.run.configuration_json["return_batches"]["0"]["operation_id"] == first["operation_id"]
    assert transport.calls == []
    assert not await service.advance_run("debug-run-1")


async def test_no_batch_wait_does_not_block_independent_transport(outcome_service) -> None:
    service, repository, transport, _, _ = await _no_batch_harness()
    assert not await service.advance_run("debug-run-1")
    handle = await outcome_service.move_rack(
        CLIENT_IDS[2], TransportCaller("independent-workline"), "other-rack", RackPosition("A"), RackPosition("B"), "90"
    )
    assert await outcome_service.submit_pending_tasks(1) == 1
    assert handle.transport_task_id
    assert repository.run.status == "RUNNING"
    assert repository.run.active_scope == "GLOBAL"
    assert transport.calls == []


@pytest.mark.parametrize("confirmation_state", [None, "RECONCILING", "PENDING", "PAYLOAD_CONFLICT"])
async def test_no_batch_due_never_bypasses_original_confirmation_conflict(confirmation_state) -> None:
    service, repository, transport, confirmation, _ = await _no_batch_harness()
    if confirmation_state is None:
        service._confirmations.get_by_identity_for_update.return_value = None
    elif confirmation_state == "PAYLOAD_CONFLICT":
        confirmation.request_payload = {**confirmation.request_payload, "timestamp": 1}
    else:
        confirmation.status = confirmation_state
    service._clock = lambda: NOW + timedelta(seconds=1)
    assert await service.advance_run("debug-run-1")
    assert repository.run.status == "NEEDS_ATTENTION"
    assert service._wms.create_or_get.await_count == 1
    assert transport.calls == []


async def _ready_harness():
    service, repository, transport = _harness(phase="BINS_TO_RACK")
    service._wms = SimpleNamespace(create_or_get=AsyncMock())
    repository.run.configuration_json["return_batches"]["0"].pop("moves")
    assert await service.advance_run("debug-run-1")
    assert repository.run.configuration_json["return_batches"]["0"]["moves"]
    assert transport.calls == []
    return service, repository, transport, service._confirmations.get_by_identity_for_update.return_value


@pytest.mark.parametrize(
    "drift",
    [
        "client_id",
        "workline_id",
        "workline_code",
        "face",
        "rack",
        "source",
        "fifo",
        "returned",
        "batch_client",
        "batch_owner",
        "moves",
    ],
)
async def test_cached_ready_revalidates_current_context_before_transport(drift) -> None:
    service, repository, transport, _ = await _ready_harness()
    configuration = repository.run.configuration_json
    batch = configuration["return_batches"]["0"]
    if drift == "client_id":
        repository.steps[0].client_request_id = CLIENT_IDS[2]
    elif drift == "workline_id":
        configuration["workline_id"] = 2
    elif drift == "workline_code":
        configuration["workline_code"] = "OTHER-LINE"
    elif drift == "face":
        configuration["face_groups"][0]["face"] = "270"
        for move in batch["moves"]:
            move["rack_face"] = "270"
    elif drift == "rack":
        repository.run.rack_id = configuration["rack_id"] = "OTHER-RACK"
        for move in batch["moves"]:
            move["rack_id"] = "OTHER-RACK"
    elif drift == "source":
        configuration["outfeed_position"] = "OTHER-SOURCE"
    elif drift == "fifo":
        configuration["return_queues"]["0"].reverse()
    elif drift == "returned":
        configuration["returned_bins"] = [{"bin_code": "A000001922"}]
    elif drift == "batch_client":
        batch["step_client_request_id"] = CLIENT_IDS[2]
    elif drift == "batch_owner":
        batch["workline_id"] = 2
    else:
        batch["moves"][0]["slot_id"] = "UNAUTHORIZED-SLOT"
    assert await service.advance_run("debug-run-1")
    assert repository.run.status == "NEEDS_ATTENTION"
    assert transport.calls == []
    service._wms.create_or_get.assert_not_awaited()
    assert not await service.advance_run("debug-run-1")


@pytest.mark.parametrize("decision", ["NO_BATCH", "READY"])
@pytest.mark.parametrize(
    "invalid",
    [
        "response_id",
        "evidence_missing",
        "evidence_id",
        "response_result",
        "confirmation_operation",
        "confirmation_identity",
        "confirmation_request",
        "request_digest",
        "evidence_operation",
        "evidence_identity",
        "response_identity",
        "evidence_conflict",
        "evidence_status",
        "evidence_kind",
        "evidence_source",
        "evidence_owner",
        "evidence_result",
        "evidence_digest",
    ],
)
async def test_cached_decision_requires_original_matching_response_evidence(decision, invalid) -> None:
    if decision == "NO_BATCH":
        service, repository, transport, confirmation, _ = await _no_batch_harness()
        service._clock = lambda: NOW + timedelta(seconds=1)
    else:
        service, repository, transport, confirmation = await _ready_harness()
    evidence = service._wms_evidence.get_by_id_for_update.return_value
    if invalid == "response_id":
        confirmation.response_evidence_id = None
    elif invalid == "evidence_missing":
        service._wms_evidence.get_by_id_for_update.return_value = None
    elif invalid == "evidence_id":
        evidence.id = 12
    elif invalid == "response_result":
        confirmation.response_result = "READY" if decision == "NO_BATCH" else "NO_BATCH"
    elif invalid == "confirmation_operation":
        confirmation.operation = "outbound.bin.inbound_batch@v1"
    elif invalid == "confirmation_identity":
        confirmation.operation_id = CLIENT_IDS[2]
    elif invalid == "confirmation_request":
        confirmation.request_payload = {**confirmation.request_payload, "timestamp": 1}
    elif invalid == "request_digest":
        confirmation.request_digest = "0" * 64
    elif invalid == "evidence_operation":
        evidence.operation = "outbound.bin.inbound_batch@v1"
    elif invalid == "evidence_identity":
        evidence.operation_id = CLIENT_IDS[2]
    elif invalid == "response_identity":
        evidence.normalized_payload["operation_id"] = CLIENT_IDS[2]
        evidence.payload_digest = canonical_json_digest(evidence.normalized_payload)
    elif invalid == "evidence_conflict":
        repository.conflicting_evidence_ids.add(evidence.id)
    elif invalid == "evidence_status":
        evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
    elif invalid == "evidence_kind":
        evidence.kind = InboundEvidenceKind.WMS_EVENT
    elif invalid == "evidence_source":
        evidence.source_identity = "OTHER-SOURCE"
    elif invalid == "evidence_owner":
        evidence.workline_id = 2
    elif invalid == "evidence_result":
        evidence.normalized_payload["data"] = (
            {
                "result": "READY",
                "moves": [
                    {
                        "sequence_no": 1,
                        "bin_code": "A000001922",
                        "target": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": "510056",
                            "rack_face": "90",
                            "slot_id": "OTHER-SLOT",
                        },
                    }
                ],
            }
            if decision == "NO_BATCH"
            else {"result": "NO_BATCH", "retry_after_ms": 1000}
        )
        confirmation.response_result = evidence.normalized_payload["data"]["result"]
        evidence.payload_digest = canonical_json_digest(evidence.normalized_payload)
    else:
        evidence.payload_digest = "0" * 64
    original_count = service._wms.create_or_get.await_count
    assert await service.advance_run("debug-run-1")
    assert repository.run.status == "NEEDS_ATTENTION"
    assert service._wms.create_or_get.await_count == original_count
    assert transport.calls == []


@pytest.mark.parametrize(
    "invalid_fact", [None, "missing", "group", "run", "pending", "task", "face", "position", "member_task"]
)
@pytest.mark.parametrize("stage", ["request", "ready"])
async def test_return_decision_uses_only_exact_same_group_rack_fact(invalid_fact, stage) -> None:
    service, repository, transport = _harness(phase="BINS_TO_RACK")
    del service._assert_return_rack_fact
    step = repository.steps[0]
    step.ordinal = 3
    repository.run.current_step_ordinal = 3
    repository.get_current_step = AsyncMock(return_value=step)
    rack_step = TransportDebugRunStep(
        run_id="other-run" if invalid_fact == "run" else repository.run.run_id,
        ordinal=0,
        group_index=1 if invalid_fact == "group" else 0,
        phase="RACK_TO_STATION",
        status="PENDING" if invalid_fact == "pending" else "SUCCEEDED",
        client_request_id=CLIENT_IDS[1],
        transport_task_id="rack-task",
        created_at=NOW,
        updated_at=NOW,
    )
    repository.steps.append(rack_step)
    if invalid_fact != "missing":
        repository.tasks["rack-task"] = _task(
            "rack-task", CLIENT_IDS[2] if invalid_fact == "task" else CLIENT_IDS[1], "RACK_MOVE", status="SUCCEEDED"
        )
    repository.members["rack-task"] = [
        _member(
            "other-task" if invalid_fact == "member_task" else "rack-task",
            object_type="RACK",
            object_id="510056",
            source={"kind": "RACK", "location_code": "510056"},
            target={"kind": "RACK_POSITION", "location_code": "wrong" if invalid_fact == "position" else "KT16"},
            face="270" if invalid_fact == "face" else "90",
        )
    ]
    batch = repository.run.configuration_json["return_batches"]["0"]
    repository.run.configuration_json["return_batches"] = {"3": batch} if stage == "ready" else {}
    service._wms = SimpleNamespace(create_or_get=AsyncMock())
    transport.position_error = TransportContractError("aggregate is unknown due to another task")
    assert await service.advance_run("debug-run-1")
    assert transport.position_checks == []
    assert service._wms.create_or_get.await_count == (1 if not invalid_fact and stage == "request" else 0)
    assert transport.calls == ([CLIENT_IDS[0]] if not invalid_fact and stage == "ready" else [])
    assert repository.run.status == ("NEEDS_ATTENTION" if invalid_fact else "RUNNING")


async def test_partial_wms_ready_freezes_target_and_remaining_fifo_waits_for_physical_success() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    service, repository, transport = _harness(phase="BINS_TO_RACK")
    repository.run.configuration_json["return_batches"] = {}
    service._wms = SimpleNamespace(create_or_get=AsyncMock())
    await service.advance_run("debug-run-1")
    first = service._wms.create_or_get.call_args.kwargs
    response = {
        "operation_id": first["operation_id"],
        "code": "DECIDED",
        "timestamp": NOT_BEFORE_MS,
        "data": {
            "result": "READY",
            "moves": [
                {
                    "sequence_no": 1,
                    "bin_code": "A000001922",
                    "target": {
                        "type": "RACK_BIN_SLOT",
                        "rack_id": "510056",
                        "rack_face": "90",
                        "slot_id": "ALLOCATED-NEW",
                    },
                }
            ],
        },
    }
    _install_return_response(service, repository, first["request_payload"], response)
    assert await service.advance_run("debug-run-1")
    assert transport.calls == []
    assert await service.advance_run("debug-run-1")
    assert len(repository.steps) == 1
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "BIN_MOVE", status="SUCCEEDED")
    repository.members["transport-1"] = [
        _member(
            "transport-1",
            object_type="BIN",
            object_id="A000001922",
            source={"kind": "HANDOFF_POSITION", "location_code": "CNV0302"},
            target={"kind": "RACK_BIN_SLOT", "rack_id": "510056", "rack_face": "90", "slot_id": "ALLOCATED-NEW"},
            face=None,
        )
    ]
    assert await service.advance_run("debug-run-1")
    snapshot = await service.get_run("debug-run-1")
    assert snapshot.current_phase == "BINS_TO_RACK"
    assert len(snapshot.steps) == 2
    assert snapshot.returned_bins == (
        {"bin_code": "A000001922", "rack_id": "510056", "rack_face": "90", "slot_id": "ALLOCATED-NEW"},
    )
    assert await service.advance_run("debug-run-1")
    candidates = service._wms.create_or_get.call_args.kwargs["request_payload"]["data"]["return_candidates"]
    assert [(item["sequence_no"], item["bin_code"]) for item in candidates] == [(1, "A000002653")]


async def test_unknown_wms_allocation_retains_scope_and_prevents_physical_abort() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.app.transport.debug_run_service import TransportDebugRunConflict

    service, repository, _ = _harness(phase="BINS_TO_RACK")
    repository.run.status = "NEEDS_ATTENTION"
    repository.run.configuration_json["return_batches"] = {"0": {"operation_id": CLIENT_IDS[1]}}
    service._confirmations = SimpleNamespace(
        get_by_identity_for_update=AsyncMock(return_value=SimpleNamespace(status="RECONCILING"))
    )
    assert not (await service.get_run("debug-run-1")).can_abort
    with pytest.raises(TransportDebugRunConflict, match="unresolved WMS"):
        await service.abort_run("debug-run-1", assertion="PHYSICAL_STATE_VERIFIED", reason="checked", actor_id=7)
    assert repository.run.active_scope == "GLOBAL"


@pytest.mark.parametrize("task_id", [None, "transport-original"])
async def test_legacy_run_freezes_for_upgrade_without_recreating_return_task(task_id: str | None) -> None:
    service, repository, transport = _harness(phase="BINS_TO_RACK", task_id=task_id)
    if task_id is None:
        repository.run.configuration_json.pop("workline_code")
    repository.run.configuration_json.pop("return_batches")
    original_id = repository.steps[0].client_request_id
    assert await service.advance_run("debug-run-1")
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "DEBUG_RUN_CONFIGURATION_UPGRADE_REQUIRED"
    assert repository.run.active_scope == "GLOBAL"
    assert repository.steps[0].client_request_id == original_id
    assert repository.steps[0].transport_task_id == task_id
    assert not await service.advance_run("debug-run-1")
    assert transport.calls == []


async def test_invalid_wms_owner_recovers_before_any_obligation_without_replacing_transport_identity() -> None:
    from unittest.mock import AsyncMock

    service, repository, transport = _harness(phase="BINS_TO_RACK")
    repository.run.configuration_json["return_batches"] = {}
    service._wms = SimpleNamespace(create_or_get=AsyncMock(side_effect=ValueError("WorkLine owner 不匹配或已关闭")))
    original_step = repository.steps[0]
    original_client_id = original_step.client_request_id
    assert await service.advance_run("debug-run-1")
    assert repository.run.attention_code == "WMS_RETURN_OWNER_INVALID"
    assert not await service.advance_run("debug-run-1")
    assert transport.calls == []
    assert repository.run.configuration_json["return_batches"] == {}
    assert repository.steps == [original_step]

    service._wms.create_or_get.side_effect = None
    service._confirmations = SimpleNamespace(
        get_by_identity_for_update=AsyncMock(return_value=SimpleNamespace(status="PENDING"))
    )
    assert await service.advance_run("debug-run-1")
    assert original_step.client_request_id == original_client_id
    assert repository.steps == [original_step]
    assert repository.run.run_id == "debug-run-1"
    call = service._wms.create_or_get.call_args.kwargs
    assert repository.run.configuration_json["return_batches"]["0"]["operation_id"] == call["operation_id"]
    assert repository.run.status == "RUNNING"
    assert repository.run.attention_code is None
    assert original_step.status == "PENDING"
    successful_create_count = service._wms.create_or_get.await_count
    assert not await service.advance_run("debug-run-1")
    assert service._wms.create_or_get.await_count == successful_create_count
    assert transport.calls == []


async def test_unknown_wms_obligation_does_not_use_owner_admission_recovery() -> None:
    service, repository, transport = _harness(phase="BINS_TO_RACK", status="NEEDS_ATTENTION")
    repository.run.status = "NEEDS_ATTENTION"
    repository.run.attention_code = "WMS_RETURN_RECONCILING"
    repository.run.configuration_json["return_batches"] = {"0": {"operation_id": CLIENT_IDS[1]}}
    assert not await service.advance_run("debug-run-1")
    assert repository.run.active_scope == "GLOBAL"
    assert repository.run.configuration_json["return_batches"]["0"]["operation_id"] == CLIENT_IDS[1]
    assert transport.calls == []
