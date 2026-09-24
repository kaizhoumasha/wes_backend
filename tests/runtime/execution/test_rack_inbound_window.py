"""目标点滚动 Transport 窗口按物理货架生命周期计数。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.execution.services.rack_inbound_window import RackInboundWindowService
from src.app.transport.contracts import TransportHandle


class _Positions:
    async def get_by_workline_logic_location_for_update(self, _db, *, workline_code, logic_location_code):
        return SimpleNamespace(
            workline_id=1,
            workline_code=workline_code,
            logic_location_code=logic_location_code,
            enabled=True,
            capacity=3,
        )


class _Bindings:
    def __init__(self):
        self.rows = []

    async def list_active_window_for_target(self, _db, *, workline_id, target_location_code):
        return [
            row
            for row in self.rows
            if row.workline_id == workline_id
            and row.window_target_location_code == target_location_code
            and row.window_released_at is None
        ]

    async def list_active_window_for_rack(self, _db, *, workline_id, rack_id):
        return [
            row
            for row in self.rows
            if row.workline_id == workline_id
            and row.resource_fence_id == rack_id
            and row.window_target_location_code is not None
            and row.window_released_at is None
        ]

    async def get_by_client_request_id(self, _db, client_request_id):
        return next((row for row in self.rows if row.client_request_id == client_request_id), None)

    async def get_by_client_request_id_for_update(self, _db, client_request_id):
        return await self.get_by_client_request_id(_db, client_request_id)

    async def get_window_by_departure_for_update(self, _db, client_request_id):
        return next((row for row in self.rows if row.window_departure_client_request_id == client_request_id), None)


@pytest.mark.asyncio
async def test_transport_progress_routes_release_decision_to_window_owner():
    window = RackInboundWindowService(positions=_Positions(), bindings=_Bindings())
    window.release_on_departure_accepted = AsyncMock(return_value=True)
    window.release_unarrived_terminal = AsyncMock(return_value=True)
    db = object()
    task = SimpleNamespace(status="ACCEPTED", client_request_id="leave-1")

    assert await window.on_transport_progress(db, task) is True
    window.release_on_departure_accepted.assert_awaited_once_with(db, client_request_id="leave-1")
    task.status = "FAILED"
    assert await window.on_transport_progress(db, task) is True
    window.release_unarrived_terminal.assert_awaited_once_with(db, task)
    task.status = "RECONCILING"
    assert await window.on_transport_progress(db, task) is False


@pytest.mark.asyncio
async def test_window_refills_only_after_leave_accepted_and_reuses_cross_revision_rack():
    bindings = _Bindings()
    window = RackInboundWindowService(positions=_Positions(), bindings=bindings)
    created = []

    class _Session:
        async def flush(self):
            return None

    db = _Session()

    async def submit(rack_id, target):
        request_id = f"{target}:{rack_id}:{len(created)}"
        bindings.rows.append(
            SimpleNamespace(
                workline_id=1,
                picking_task_id=7,
                resource_fence_id=rack_id,
                client_request_id=request_id,
                window_target_location_code=None,
                window_departure_client_request_id=None,
                window_released_at=None,
            )
        )
        created.append(request_id)
        return TransportHandle(f"transport:{request_id}", request_id)

    async def admit(rack_id, target="A"):
        return await window.admit(
            db,
            workline_id=1,
            workline_code="LINE3",
            target_location_code=target,
            rack_id=rack_id,
            picking_task_id=7,
            create=lambda: submit(rack_id, target),
        )

    assert [await admit(f"RACK-{index}") for index in range(1, 6)] == [
        "CREATED",
        "CREATED",
        "CREATED",
        "PENDING",
        "PENDING",
    ]
    assert await admit("RACK-1") == "REUSED"  # rev2/另一面关联同一物理生命周期
    assert await admit("RACK-1", "B") == "PENDING"  # 同一物理货架不能占用另一目标点
    assert await admit("RETURN-RACK-1", "B") == "CREATED"  # 目标点窗口独立
    assert len(created) == 4

    # 到位、业务完成或离场任务仅创建，都不能释放名额。
    await window.attach_departure(db, workline_id=1, rack_id="RACK-1", client_request_id="leave-1")
    assert await admit("RACK-4") == "PENDING"
    assert await window.release_on_departure_accepted(db, client_request_id="leave-1") is True
    assert await admit("RACK-4") == "CREATED"
    assert await admit("RACK-5") == "PENDING"
    assert await window.release_on_departure_accepted(db, client_request_id="leave-1") is False


@pytest.mark.asyncio
async def test_definite_unarrived_inbound_releases_window_but_unknown_or_arrived_does_not():
    bindings = _Bindings()
    binding = SimpleNamespace(
        workline_id=1,
        picking_task_id=7,
        resource_fence_id="RACK-1",
        client_request_id="in-1",
        window_target_location_code="A",
        window_departure_client_request_id=None,
        window_released_at=None,
    )
    bindings.rows.append(binding)
    window = RackInboundWindowService(positions=_Positions(), bindings=bindings)
    db = SimpleNamespace(flush=AsyncMock())
    task = SimpleNamespace(client_request_id="in-1", status="RECONCILING", outcome_json=None)

    assert await window.release_unarrived_terminal(db, task) is False
    task.status = "FAILED"
    task.outcome_json = {
        "members": [{"object_id": "RACK-1", "final_position": {"kind": "RACK_POSITION", "location_code": "A"}}]
    }
    assert await window.release_unarrived_terminal(db, task) is False
    task.outcome_json["members"][0]["final_position"]["location_code"] = "SOURCE"
    assert await window.release_unarrived_terminal(db, task) is True
    assert binding.window_released_at is not None

    binding.window_released_at = None
    task.status = "REJECTED"
    task.outcome_json = None
    assert await window.release_unarrived_terminal(db, task) is True


@pytest.mark.asyncio
async def test_rejected_departure_can_be_replaced_without_manual_window_reset():
    bindings = _Bindings()
    bindings.rows.append(
        SimpleNamespace(
            workline_id=1,
            resource_fence_id="RACK-1",
            window_target_location_code="A",
            window_departure_client_request_id="leave-1",
            window_released_at=None,
        )
    )
    prior = SimpleNamespace(status="PENDING")
    transports = SimpleNamespace(get_task_by_client_request=AsyncMock(return_value=prior))
    window = RackInboundWindowService(positions=_Positions(), bindings=bindings, transports=transports)
    db = SimpleNamespace(flush=AsyncMock())

    with pytest.raises(ValueError, match="another departure"):
        await window.attach_departure(db, workline_id=1, rack_id="RACK-1", client_request_id="leave-2")
    prior.status = "REJECTED"
    assert await window.attach_departure(db, workline_id=1, rack_id="RACK-1", client_request_id="leave-2")
    assert bindings.rows[0].window_departure_client_request_id == "leave-2"


@pytest.mark.asyncio
async def test_terminal_inbound_at_target_retries_inside_existing_physical_window():
    bindings = _Bindings()
    primary = SimpleNamespace(
        workline_id=1,
        picking_task_id=7,
        resource_fence_id="RACK-1",
        client_request_id="in-1",
        window_target_location_code="A",
        window_departure_client_request_id=None,
        window_released_at=None,
    )
    bindings.rows.append(primary)
    prior = SimpleNamespace(
        status="PENDING",
        outcome_json={
            "members": [{"object_id": "RACK-1", "final_position": {"kind": "RACK_POSITION", "location_code": "A"}}]
        },
    )
    transports = SimpleNamespace(get_task_by_client_request=AsyncMock(return_value=prior))
    window = RackInboundWindowService(positions=_Positions(), bindings=bindings, transports=transports)
    db = SimpleNamespace(flush=AsyncMock())

    async def create_retry():
        bindings.rows.append(
            SimpleNamespace(
                workline_id=1,
                picking_task_id=7,
                resource_fence_id="RACK-1",
                client_request_id="retry-1",
                window_target_location_code=None,
                window_departure_client_request_id=None,
                window_released_at=None,
            )
        )
        return TransportHandle("transport-retry-1", "retry-1")

    async def admit():
        return await window.admit(
            db,
            workline_id=1,
            workline_code="LINE3",
            target_location_code="A",
            rack_id="RACK-1",
            picking_task_id=7,
            create=create_retry,
            retry_terminal_inbound=True,
        )

    assert await admit() == "REUSED"
    assert len(bindings.rows) == 1
    prior.status = "FAILED"
    assert await admit() == "CREATED"
    assert await bindings.list_active_window_for_target(db, workline_id=1, target_location_code="A") == [primary]
    assert await window.release_unarrived_terminal(
        db,
        SimpleNamespace(
            client_request_id="retry-1",
            status="FAILED",
            outcome_json={
                "members": [
                    {"object_id": "RACK-1", "final_position": {"kind": "RACK_POSITION", "location_code": "SOURCE"}}
                ]
            },
        ),
    )
    assert primary.window_released_at is not None
