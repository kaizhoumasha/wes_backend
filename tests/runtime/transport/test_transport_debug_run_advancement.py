from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError

from src.app.execution.models import InboundEvidence, InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.transport.contracts import TransportContractError, TransportHandle
from src.app.transport.debug_run_evidence import Scan12EvidenceDisposition, Scan12EvidenceEvaluation
from src.app.transport.debug_run_service import TransportDebugRunService
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportMember, TransportTask
from src.utils.timezone import timezone

if TYPE_CHECKING:
    import pytest

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
        self.error: Exception | None = None
        self.position_error: Exception | None = None

    async def assert_debug_rack_position_in_session(
        self,
        db: object,
        rack_id: str,
        expected_position: object,
    ) -> None:
        del db, rack_id, expected_position
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

    async def has_active_transport_binding(self, db: object, run_id: str) -> bool:
        del db, run_id
        return False

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
            "rack_id": "510056",
            "face_groups": [
                {
                    "face": "90",
                    "bins": [
                        {"bin_id": "A000001922", "slot_id": "SLOT-01"},
                        {"bin_id": "A000002653", "slot_id": "SLOT-02"},
                    ],
                }
            ],
            "storage_zone": "WH01",
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
    assert repository.steps[0].status == "WAITING"
    assert repository.run.status == "RUNNING"


async def test_transport_contract_rejection_becomes_attention_without_retry_loop() -> None:
    service, repository, transport = _harness()
    transport.error = TransportContractError("rack current position is not confirmed")

    assert await service.advance_run("debug-run-1") is True

    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "TRANSPORT_CONTRACT_REJECTED"
    assert repository.run.claim_token is None


async def test_transport_integrity_conflict_becomes_attention_after_rollback() -> None:
    service, repository, transport = _harness()
    transport.error = IntegrityError("INSERT", {}, RuntimeError("resource conflict"))

    assert await service.advance_run("debug-run-1") is True

    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "TRANSPORT_RESOURCE_CONFLICT"
    assert repository.run.claim_token is None


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


async def test_succeeded_transport_creates_one_next_intent_with_evidence_boundary() -> None:
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

    assert await service.advance_run("debug-run-1") is True

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


async def test_scan12_uses_set_semantics_and_advances_only_after_every_selected_bin() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    boundary = timezone.to_db_datetime(NOT_BEFORE_MS / 1000)
    assert boundary is not None
    repository.evidences = [
        _scan(99, "OLD", "A000001922", received_at=boundary),
        _scan(101, "EVENT-A", "A000001922"),
        _scan(102, "EVENT-A-DUP", "A000001922"),
        _scan(103, "EVENT-C", "OTHER-BIN"),
    ]

    assert await service.advance_run("debug-run-1") is True
    snapshot = await service.get_run("debug-run-1")
    assert snapshot.observed_bin_ids == ("A000001922",)
    assert snapshot.current_phase == "WAIT_SCAN12"
    assert repository.steps[0].observed_bins_json[0]["evidence_id"] == 101

    repository.evidences.append(_scan(105, "EVENT-B", "A000002653"))
    assert await service.advance_run("debug-run-1") is True
    snapshot = await service.get_run("debug-run-1")
    assert snapshot.current_phase == "BINS_TO_RACK"
    assert snapshot.observed_bin_ids == ("A000001922", "A000002653")
    assert snapshot.current_step is not None and snapshot.current_step.client_request_id is not None


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
    assert {item["bin_id"] for item in repository.steps[0].observed_bins_json} == {
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
            "bin_id": "A000001922",
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
        {"bin_id": "A000001922", "evidence_id": 101, "source_event_id": "EVENT-A"},
        {"bin_id": "A000002653", "evidence_id": 102, "source_event_id": "EVENT-B"},
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
        {"bin_id": "A000001922", "evidence_id": 101, "source_event_id": "EVENT-A"},
        {"bin_id": "A000002653", "evidence_id": 102, "source_event_id": "EVENT-B"},
    ]
    repository.tasks["transport-1"] = _task("transport-1", CLIENT_IDS[0], "BIN_MOVE")
    repository.conflicting_evidence_ids.add(101)

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_SOURCE_EVENT_CONFLICT"
    assert len(repository.steps) == 1


async def test_ambiguous_evidence_needs_attention_and_does_not_auto_clear() -> None:
    service, repository, _ = _harness(phase="WAIT_SCAN12", status="WAITING")
    repository.evidences = [_scan(101, "EVENT-A", "A000001922", apply_status="RECONCILING")]

    assert await service.advance_run("debug-run-1") is True
    assert repository.run.status == "NEEDS_ATTENTION"
    assert repository.run.attention_code == "EVIDENCE_RECONCILING"

    repository.evidences[0].apply_status = InboundEvidenceApplyStatus.APPLIED
    assert await service.advance_run("debug-run-1") is False
    assert repository.run.status == "NEEDS_ATTENTION"


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
