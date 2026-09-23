"""入站与退箱批次复用宿主的可靠 WMS 义务。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
import wes_plugin_sdk as sdk

from src.app.wms_integration.outbound_picking.services import bin_batch


class _Confirmations:
    def __init__(self) -> None:
        self.calls = []

    async def create_or_get(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return SimpleNamespace(confirmation=SimpleNamespace(next_attempt_at=None), duplicate=False)


class _ConfirmationReader:
    def __init__(self, confirmation):  # type: ignore[no-untyped-def]
        self.confirmation = confirmation

    async def get_by_identity_for_update(self, _db, _operation, _operation_id):  # type: ignore[no-untyped-def]
        return self.confirmation


@pytest.mark.asyncio
async def test_batch_scheduler_keeps_typed_operation_and_workline_owner() -> None:
    confirmations = _Confirmations()
    scheduler = bin_batch.BinBatchScheduler(confirmations)
    now = datetime(2026, 9, 13, 12)
    inbound = sdk.wms_operations.outbound_bin_inbound_batch(
        operation_id="019f0000-0000-7000-8000-000000000001",
        task_id="PICK-001",
        rack_id="R1",
        rack_face="90",
    )
    returned = sdk.wms_operations.outbound_bin_return_batch(
        operation_id="019f0000-0000-7000-8000-000000000002",
        workline_code="LINE-1",
        rack_id="R1",
        rack_face="90",
        return_candidates=(sdk.BinReturnCandidate(1, "A000000001", "CNV0302"),),
    )

    await scheduler.create_in_session(object(), inbound, workline_id=7, created_at=now)
    await scheduler.create_in_session(object(), returned, workline_id=7, created_at=now)

    assert [call["operation"] for call in confirmations.calls] == [
        "outbound.bin.inbound_batch@v1",
        "outbound.bin.return_batch@v1",
    ]
    assert [call["workline_id"] for call in confirmations.calls] == [7, 7]
    assert confirmations.calls[0]["request_payload"]["data"] == {
        "task_id": "PICK-001",
        "rack_id": "R1",
        "rack_face": "90",
    }
    assert confirmations.calls[1]["request_payload"]["data"]["return_candidates"] == [
        {
            "sequence_no": 1,
            "bin_code": "A000000001",
            "source": {"type": "HANDOFF_POSITION", "location_code": "CNV0302"},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "cancelled_evidence_id", "task_type"),
    [
        ("EXECUTING", None, "MANUAL"),
        ("EXECUTION_COMPLETED", None, "MANUAL"),
        ("EXECUTION_COMPLETED", 91, "MANUAL"),
        ("ARCHIVED", 91, "MANUAL"),
        ("EXECUTING", None, "AUTO"),
    ],
)
async def test_inbound_batch_owner_keeps_existing_face_obligation_after_parent_transition(
    status: str, cancelled_evidence_id: int | None, task_type: str
) -> None:
    class Tasks:
        async def get_by_task_id_for_update(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return SimpleNamespace(id=31, workline_id=7, status=status, task_type=task_type)

    class Plans:
        async def list_bin_source_racks(self, _db, _task_id):  # type: ignore[no-untyped-def]
            return [SimpleNamespace(rack_id="R1", rack_face="90", cancelled_evidence_id=cancelled_evidence_id)]

    owner = bin_batch.BinInboundBatchOwnerService(tasks=Tasks(), plans=Plans())
    payload = {
        "operation_id": "019f0000-0000-7000-8000-000000000001",
        "operation": "outbound.bin.inbound_batch@v1",
        "timestamp": 1_788_975_600_000,
        "data": {"task_id": "PICK-001", "rack_id": "R1", "rack_face": "90"},
    }

    assert await owner.validate_owner(object(), workline_id=7, request_payload=payload)
    assert not await owner.validate_owner(object(), workline_id=8, request_payload=payload)
    assert not await owner.validate_owner(
        object(), workline_id=7, request_payload=payload | {"data": payload["data"] | {"rack_face": "270"}}
    )


@pytest.mark.asyncio
async def test_inbound_result_reader_binds_frozen_request_to_response_evidence() -> None:
    operation_id = "019f0000-0000-7000-8000-000000000001"
    request = {
        "operation": "outbound.bin.inbound_batch@v1",
        "operation_id": operation_id,
        "timestamp": 1_788_975_600_000,
        "data": {"task_id": "PICK-001", "rack_id": "R1", "rack_face": "90"},
    }
    response = {
        "operation_id": operation_id,
        "code": "DECIDED",
        "timestamp": 1_788_975_600_100,
        "data": {
            "result": "READY",
            "bins": [
                {
                    "bin_code": "A000000001",
                    "source_locator": {"type": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "S1"},
                }
            ],
        },
    }
    confirmation = SimpleNamespace(
        operation=request["operation"],
        operation_id=operation_id,
        workline_id=7,
        response_evidence_id=31,
        request_payload=request,
        status="COMPLETED",
    )
    evidence = SimpleNamespace(
        id=31,
        operation=request["operation"],
        operation_id=operation_id,
        normalized_payload=response,
    )
    reader = bin_batch.BinBatchResultReader(_ConfirmationReader(confirmation))

    intent, outcome = await reader.read_inbound(object(), evidence, workline_id=7)

    assert type(intent) is sdk.BinInboundBatchIntent
    assert intent.task_id == "PICK-001"
    assert type(outcome.result) is sdk.BinInboundBatchReady
    assert outcome.result.bins[0].bin_code == "A000000001"
    with pytest.raises(ValueError, match="original confirmation"):
        await reader.read_inbound(object(), SimpleNamespace(**(vars(evidence) | {"id": 32})), workline_id=7)


@pytest.mark.asyncio
async def test_inbound_face_reader_rejects_multiple_distinct_allocations() -> None:
    class Db:
        async def execute(self, _statement):  # type: ignore[no-untyped-def]
            return SimpleNamespace(all=lambda: [(SimpleNamespace(id=1), datetime(2026, 9, 13, 12))] * 2)

    reader = bin_batch.BinBatchResultReader()
    with pytest.raises(ValueError, match="multiple inbound allocations"):
        await reader.latest_inbound_detail(Db(), workline_id=7, task_id="PICK-1", rack_id="R1", rack_face="90")


@pytest.mark.asyncio
async def test_return_result_reader_rejects_non_prefix_and_keeps_original_candidates() -> None:
    operation_id = "019f0000-0000-7000-8000-000000000002"
    request = {
        "operation": "outbound.bin.return_batch@v1",
        "operation_id": operation_id,
        "timestamp": 1_788_975_600_000,
        "data": {
            "workline_code": "LINE-1",
            "rack_id": "R1",
            "rack_face": "90",
            "return_candidates": [
                {
                    "sequence_no": 1,
                    "bin_code": "A000000001",
                    "source": {"type": "HANDOFF_POSITION", "location_code": "CNV0302"},
                },
                {
                    "sequence_no": 2,
                    "bin_code": "A000000002",
                    "source": {"type": "HANDOFF_POSITION", "location_code": "CNV0302"},
                },
            ],
        },
    }
    response = {
        "operation_id": operation_id,
        "code": "DECIDED",
        "timestamp": 1_788_975_600_100,
        "data": {
            "result": "READY",
            "moves": [
                {
                    "sequence_no": 1,
                    "bin_code": "A000000001",
                    "target": {"type": "RACK_BIN_SLOT", "rack_id": "R1", "rack_face": "90", "slot_id": "S1"},
                }
            ],
        },
    }
    confirmation = SimpleNamespace(
        operation=request["operation"],
        operation_id=operation_id,
        workline_id=7,
        response_evidence_id=32,
        request_payload=request,
        status="COMPLETED",
    )
    evidence = SimpleNamespace(
        id=32,
        operation=request["operation"],
        operation_id=operation_id,
        normalized_payload=response,
    )
    reader = bin_batch.BinBatchResultReader(_ConfirmationReader(confirmation))

    intent, outcome = await reader.read_return(object(), evidence, workline_id=7)

    assert type(intent) is sdk.BinReturnBatchIntent
    assert [candidate.bin_code for candidate in intent.return_candidates] == ["A000000001", "A000000002"]
    assert type(outcome.result) is sdk.BinReturnBatchReady
    assert [move.bin_code for move in outcome.result.moves] == ["A000000001"]
    with pytest.raises(ValueError, match="FIFO"):
        await reader.read_return(
            object(),
            SimpleNamespace(
                **(
                    vars(evidence)
                    | {
                        "normalized_payload": response
                        | {
                            "data": response["data"]
                            | {"moves": [response["data"]["moves"][0] | {"bin_code": "A000000002"}]}
                        }
                    }
                )
            ),
            workline_id=7,
        )
