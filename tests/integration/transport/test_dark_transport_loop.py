from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete

from src.app.transport import composition as transport_composition
from src.app.transport.contracts import (
    BinExchangePair,
    BinMove,
    HandoffPosition,
    RackBinSlot,
    RackFace,
    RackPosition,
    TransportCaller,
)
from src.app.transport.models import (
    TransportCallbackReceipt,
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportResourceBinding,
    TransportTask,
)
from src.app.wms_adapter.transport_wire import RESULT_OPERATION
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from tests.contracts.wms_integration.provider_profile_support import build_compiled_provider_profile
from tests.support.transport_callbacks import record_valid_callback

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportOutcome


@dataclass
class _DeliveryState:
    value: str = "RESPONSE_RECEIVED"


@dataclass
class _AccessResult:
    delivery_state: _DeliveryState
    status_code: int
    json_body: dict[str, object]
    json_failure: str | None = None


class _AcceptedClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def post_json_bytes(self, path: str, *, body: bytes, **kwargs: object) -> _AccessResult:
        message = json.loads(body)
        self.calls.append(message)
        data = message["data"]
        assert isinstance(data, dict)
        return _AccessResult(
            delivery_state=_DeliveryState(),
            status_code=202,
            json_body={
                "operation_id": message["operation_id"],
                "code": "RECEIVED",
                "timestamp": 1,
                "data": {"transport_task_id": data["transport_task_id"]},
            },
        )

    async def aclose(self) -> None:
        return None


class _Publisher:
    def __init__(self) -> None:
        self.outcomes: list[TransportOutcome] = []

    async def publish(self, outcome: TransportOutcome) -> None:
        self.outcomes.append(outcome)


pytestmark = pytest.mark.asyncio


async def test_dark_composition_runs_four_methods_through_the_explicit_closed_loop(
    integration_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.wms_adapter import factory

    suffix = uuid.uuid4().hex
    client = _AcceptedClient()
    publisher = _Publisher()
    monkeypatch.setattr(factory, "build_wms_client", lambda **_kwargs: client)
    runtime = await transport_composition.build_transport_runtime(
        startup=SimpleNamespace(compiled_profile=build_compiled_provider_profile()),
        session_factory=integration_session_factory,
    )
    service = runtime.service
    caller = TransportCaller("DARK_LINE", "STATION_A")
    rack_id = f"rack-move-{suffix}"
    rotate_rack_id = f"rack-rotate-{suffix}"
    moved_bin_id = f"bin-move-{suffix}"
    move_source_rack = f"rack-bin-source-{suffix}"
    exchange_left_rack = f"rack-exchange-left-{suffix}"
    exchange_right_rack = f"rack-exchange-right-{suffix}"
    exchange_bin_ids = [f"bin-exchange-{index}-{suffix}" for index in range(4)]
    task_ids: list[str] = []
    callback_operation_ids: list[str] = []

    async with integration_session_factory.begin() as db:
        db.add_all(
            [
                TransportPositionProjection(
                    object_type="RACK",
                    object_id=position_rack_id,
                    position_json={"kind": "RACK_POSITION", "location_code": "RACK_WAIT"},
                    arrival_face="A",
                    source_operation_id=new_uuid7(),
                    updated_at=timezone.now_for_db(),
                )
                for position_rack_id in (move_source_rack, exchange_left_rack, exchange_right_rack)
            ]
            + [
                TransportPositionProjection(
                    object_type="RACK",
                    object_id=rotate_rack_id,
                    position_json={"kind": "RACK_POSITION", "location_code": "ROTATE_POINT"},
                    arrival_face="A",
                    source_operation_id=new_uuid7(),
                    updated_at=timezone.now_for_db(),
                )
            ]
        )
    try:
        rack_handle = await service.move_rack(
            new_uuid7(), caller, rack_id, RackPosition("RACK_WAIT"), RackPosition("RACK_WORK"), RackFace.A
        )
        rotate_handle = await service.rotate_rack(
            new_uuid7(), caller, rotate_rack_id, RackPosition("ROTATE_POINT"), RackFace.B
        )
        bin_handle = await service.move_bins(
            new_uuid7(),
            caller,
            (BinMove(moved_bin_id, RackBinSlot(move_source_rack, RackFace.A, "1"), HandoffPosition("ROLLER_IN")),),
        )
        exchange_handle = await service.exchange_bins(
            new_uuid7(),
            caller,
            (
                BinExchangePair(
                    exchange_bin_ids[0],
                    RackBinSlot(exchange_left_rack, RackFace.A, "1"),
                    exchange_bin_ids[1],
                    RackBinSlot(exchange_right_rack, RackFace.A, "1"),
                ),
                BinExchangePair(
                    exchange_bin_ids[2],
                    RackBinSlot(exchange_left_rack, RackFace.A, "2"),
                    exchange_bin_ids[3],
                    RackBinSlot(exchange_right_rack, RackFace.A, "2"),
                ),
            ),
        )
        handles = [rack_handle, rotate_handle, bin_handle, exchange_handle]
        task_ids.extend(handle.transport_task_id for handle in handles)

        assert await service.submit_pending_tasks(10) == 4
        result_payloads = [
            (
                rack_handle,
                {
                    "kind": "RACK_MOVE",
                    "outcome_revision": 1,
                    "rack_id": rack_id,
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "RACK_POSITION", "location_code": "RACK_WORK"},
                    "arrival_face": "A",
                },
            ),
            (
                rotate_handle,
                {
                    "kind": "RACK_ROTATE",
                    "outcome_revision": 1,
                    "rack_id": rotate_rack_id,
                    "status": "SUCCEEDED",
                    "final_position": {"kind": "RACK_POSITION", "location_code": "ROTATE_POINT"},
                    "arrival_face": "B",
                },
            ),
            (
                bin_handle,
                {
                    "kind": "BIN_MOVE",
                    "outcome_revision": 1,
                    "results": [
                        {
                            "container_id": moved_bin_id,
                            "status": "SUCCEEDED",
                            "final_position": {"kind": "HANDOFF_POSITION", "location_code": "ROLLER_IN"},
                        }
                    ],
                },
            ),
            (
                exchange_handle,
                {
                    "kind": "BIN_EXCHANGE",
                    "outcome_revision": 1,
                    "results": [
                        {
                            "container_id": exchange_bin_ids[0],
                            "status": "SUCCEEDED",
                            "final_position": {
                                "kind": "RACK_BIN_SLOT",
                                "rack_id": exchange_right_rack,
                                "rack_face": "A",
                                "slot_id": "1",
                            },
                        },
                        {
                            "container_id": exchange_bin_ids[1],
                            "status": "SUCCEEDED",
                            "final_position": {
                                "kind": "RACK_BIN_SLOT",
                                "rack_id": exchange_left_rack,
                                "rack_face": "A",
                                "slot_id": "1",
                            },
                        },
                        {
                            "container_id": exchange_bin_ids[2],
                            "status": "SUCCEEDED",
                            "final_position": {
                                "kind": "RACK_BIN_SLOT",
                                "rack_id": exchange_right_rack,
                                "rack_face": "A",
                                "slot_id": "2",
                            },
                        },
                        {
                            "container_id": exchange_bin_ids[3],
                            "status": "SUCCEEDED",
                            "final_position": {
                                "kind": "RACK_BIN_SLOT",
                                "rack_id": exchange_left_rack,
                                "rack_face": "A",
                                "slot_id": "2",
                            },
                        },
                    ],
                },
            ),
        ]
        for handle, payload in result_payloads:
            operation_id = new_uuid7()
            callback_operation_ids.append(operation_id)
            await record_valid_callback(
                service,
                operation_id=operation_id,
                transport_task_id=handle.transport_task_id,
                operation=RESULT_OPERATION,
                timestamp=1,
                payload=payload,
            )

        assert await service.process_pending_evidence(10) == 4
        assert await service.reconcile_overdue_tasks(10) == 0
        assert await service.publish_pending_outcomes(10, publisher) == 4
        assert len(client.calls) == 4
        assert len(publisher.outcomes) == 4
        assert {outcome.status.value for outcome in publisher.outcomes} == {"SUCCEEDED"}
    finally:
        await runtime.aclose()
        async with integration_session_factory.begin() as db:
            if task_ids:
                await db.execute(
                    delete(TransportCallbackReceipt).where(
                        TransportCallbackReceipt.operation_id.in_(callback_operation_ids)
                    )
                )
                await db.execute(delete(TransportEvidence).where(TransportEvidence.transport_task_id.in_(task_ids)))
                await db.execute(
                    delete(TransportResourceBinding).where(TransportResourceBinding.transport_task_id.in_(task_ids))
                )
                await db.execute(delete(TransportMember).where(TransportMember.transport_task_id.in_(task_ids)))
                await db.execute(delete(TransportTask).where(TransportTask.transport_task_id.in_(task_ids)))
            await db.execute(
                delete(TransportPositionProjection).where(
                    TransportPositionProjection.object_id.in_(
                        [
                            rotate_rack_id,
                            rack_id,
                            move_source_rack,
                            exchange_left_rack,
                            exchange_right_rack,
                            moved_bin_id,
                            *exchange_bin_ids,
                        ]
                    )
                )
            )
