"""按目标点容量可靠限制物理货架的进场 Transport 下发。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from src.app.execution.repositories import transport_decision_binding_repository
from src.app.runtime.orchestration.repositories.workline_position_repository import workline_position_repository
from src.app.transport.repository import TransportRepository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from src.app.transport.contracts import TransportHandle

WindowAdmission = Literal["CREATED", "REUSED", "PENDING"]


class RackInboundWindowService:
    """进场窗口只记录 Transport 生命周期，不表示 RCS 物理排队位。"""

    def __init__(
        self,
        *,
        positions: Any = workline_position_repository,
        bindings: Any = transport_decision_binding_repository,
        transports: Any = None,
    ):
        self._positions = positions
        self._bindings = bindings
        self._transports = transports or TransportRepository()

    async def on_transport_progress(self, db: Any, task: Any) -> bool:
        if task.status in {"ACCEPTED", "SUCCEEDED"}:
            return await self.release_on_departure_accepted(db, client_request_id=task.client_request_id)
        if task.status in {"REJECTED", "FAILED"}:
            return await self.release_unarrived_terminal(db, task)
        return False

    async def admit(
        self,
        db: Any,
        *,
        workline_id: int,
        workline_code: str,
        target_location_code: str,
        rack_id: str,
        picking_task_id: int | None,
        create: Callable[[], Awaitable[TransportHandle]],
        retry_terminal_inbound: bool = False,
    ) -> WindowAdmission:
        position = await self._positions.get_by_workline_logic_location_for_update(
            db, workline_code=workline_code, logic_location_code=target_location_code
        )
        if position is None or position.workline_id != workline_id or not position.enabled or position.capacity < 1:
            raise ValueError("target rack position capacity unavailable")
        active = await self._bindings.list_active_window_for_target(
            db, workline_id=workline_id, target_location_code=target_location_code
        )
        same_rack = next((row for row in active if row.resource_fence_id == rack_id), None)
        if same_rack is not None:
            if retry_terminal_inbound and same_rack.picking_task_id == picking_task_id:
                prior = await self._transports.get_task_by_client_request(db, same_rack.client_request_id)
                members = (prior.outcome_json or {}).get("members", []) if prior is not None else []
                if (
                    prior is not None
                    and prior.status == "FAILED"
                    and len(members) == 1
                    and members[0].get("object_id") == rack_id
                    and members[0].get("final_position")
                    == {"kind": "RACK_POSITION", "location_code": target_location_code}
                ):
                    await create()
                    return "CREATED"
            return "REUSED" if same_rack.picking_task_id == picking_task_id else "PENDING"
        if await self._bindings.list_active_window_for_rack(db, workline_id=workline_id, rack_id=rack_id):
            return "PENDING"
        if len(active) >= position.capacity:
            return "PENDING"
        handle = await create()
        binding = await self._bindings.get_by_client_request_id(db, handle.client_request_id)
        if binding is None or binding.workline_id != workline_id or binding.resource_fence_id != rack_id:
            raise ValueError("inbound Transport lacks matching decision binding")
        if (
            binding.window_target_location_code not in (None, target_location_code)
            or binding.window_released_at is not None
        ):
            raise ValueError("inbound Transport window identity conflict")
        binding.window_target_location_code = target_location_code
        await db.flush()
        return "CREATED"

    async def attach_departure(self, db: Any, *, workline_id: int, rack_id: str, client_request_id: str) -> bool:
        active = await self._bindings.list_active_window_for_rack(db, workline_id=workline_id, rack_id=rack_id)
        if not active:
            return False
        if len(active) != 1:
            raise ValueError("rack has multiple active inbound windows")
        binding = active[0]
        if binding.window_departure_client_request_id not in (None, client_request_id):
            prior = await self._transports.get_task_by_client_request(db, binding.window_departure_client_request_id)
            if prior is None or prior.status != "REJECTED":
                raise ValueError("rack inbound window already has another departure")
        binding.window_departure_client_request_id = client_request_id
        await db.flush()
        return True

    async def release_unarrived_terminal(self, db: Any, task: Any) -> bool:
        binding = await self._bindings.get_by_client_request_id_for_update(db, task.client_request_id)
        if binding is None:
            return False
        if binding.window_target_location_code is None:
            active = await self._bindings.list_active_window_for_rack(
                db, workline_id=binding.workline_id, rack_id=binding.resource_fence_id
            )
            if len(active) != 1 or active[0].picking_task_id != binding.picking_task_id or task.status != "FAILED":
                return False
            binding = active[0]
        if binding.window_released_at is not None:
            return False
        if task.status == "REJECTED":
            pass
        elif task.status == "FAILED":
            members = (task.outcome_json or {}).get("members", [])
            if len(members) != 1 or members[0].get("object_id") != binding.resource_fence_id:
                return False
            position = members[0].get("final_position")
            if not isinstance(position, dict) or not isinstance(position.get("location_code"), str):
                return False
            if position == {"kind": "RACK_POSITION", "location_code": binding.window_target_location_code}:
                return False
        else:
            return False
        binding.window_released_at = timezone.now_for_db()
        await db.flush()
        return True

    async def release_on_departure_accepted(self, db: Any, *, client_request_id: str) -> bool:
        binding = await self._bindings.get_window_by_departure_for_update(db, client_request_id)
        if binding is None or binding.window_released_at is not None:
            return False
        binding.window_released_at = timezone.now_for_db()
        await db.flush()
        return True


__all__ = ["RackInboundWindowService", "WindowAdmission"]
