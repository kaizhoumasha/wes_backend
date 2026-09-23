from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import (
    PickingTaskPlanAppliedFact,
    PickingTaskPlanHandlingResult,
    PickingTaskPlanRack,
    PickingTaskRackTransportIntent,
    PositionBindingSnapshot,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
)


def _service_type():  # type: ignore[no-untyped-def]
    try:
        from src.app.wms_integration.outbound_picking.services.picking_task_plan_activation import (
            PickingTaskPlanActivationService,
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"plan activation service is missing: {exc}")
    return PickingTaskPlanActivationService


class _Sessions:
    def __init__(self) -> None:
        self.calls = 0

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        self.calls += 1
        yield object()


class _Worklines:
    def __init__(self, line: object | None) -> None:
        self.line = line
        self.rows = [(7, "sample_plugin", "0.1.0")]

    async def list_active_for_plugin_identities(self, _db, identities, *, limit, after_id=0):  # type: ignore[no-untyped-def]
        assert identities == (("sample_plugin", "0.1.0"),)
        assert limit == 100
        return [row for row in self.rows if row[0] > after_id][:limit]

    async def get_for_update(self, _db, workline_id):  # type: ignore[no-untyped-def]
        assert workline_id == 7
        return self.line

    get_for_authority_update = get_for_update


class _Handler:
    def __init__(self) -> None:
        self.fact = None

    def __call__(self, fact):  # type: ignore[no-untyped-def]
        self.fact = fact
        target = fact.target_rack
        return PickingTaskPlanHandlingResult(
            transports=(
                PickingTaskRackTransportIntent(
                    task_id=fact.task_id,
                    fact_id=fact.fact_id,
                    source_evidence_id=target.source_evidence_id,
                    position_role="TARGET_SLOT",
                    rack_id=target.rack_id,
                    source=TransportRackReference(target.rack_id),
                    target=TransportRackPosition("TARGET-POS"),
                    target_face=target.rack_faces[0],
                    rcs_template_id=TransportRcsTemplateId.CTU02,
                ),
                *(
                    PickingTaskRackTransportIntent(
                        task_id=fact.task_id,
                        fact_id=fact.fact_id,
                        source_evidence_id=bin_rack.source_evidence_id,
                        position_role="SOURCE_SLOT",
                        rack_id=bin_rack.rack_id,
                        source=TransportRackReference(bin_rack.rack_id),
                        target=TransportRackPosition("SOURCE-POS"),
                        target_face=bin_rack.rack_faces[0],
                        rcs_template_id=TransportRcsTemplateId.CTU03,
                    )
                    for bin_rack in fact.pending_bin_source_racks
                ),
            ),
        )


class _Creator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, _db, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)


class _BatchDriver:
    def __init__(self, completed_count: int = 0) -> None:
        self.context = None
        self.completed_count = completed_count

    async def advance_in_session(self, _db, line, task):  # type: ignore[no-untyped-def]
        self.context = (line.line_code, task.task_id)
        return 1

    async def advance_completed_in_session(self, _db, line):  # type: ignore[no-untyped-def]
        self.context = (line.line_code, "COMPLETED_RETURN")
        return self.completed_count


class _CompletionDriver:
    def __init__(self) -> None:
        self.context = None

    async def advance_in_session(self, _db, line, task):  # type: ignore[no-untyped-def]
        self.context = (line.line_code, task.task_id)
        return 1


@pytest.mark.asyncio
async def test_batch_does_not_read_business_state_without_plan_handler() -> None:
    sessions = _Sessions()
    service = _service_type()(
        sessions,
        plugins=(
            SimpleNamespace(
                plugin_key="other",
                plugin_version="1.0",
                picking_task_plan_applied_handler=None,
            ),
        ),
        transport_creator=SimpleNamespace(),
    )

    assert await service.activate_batch() == 0
    assert sessions.calls == 0


@pytest.mark.asyncio
async def test_plan_activation_scans_worklines_after_first_full_page() -> None:
    worklines = _Worklines(None)
    worklines.rows = [(index, "sample_plugin", "0.1.0") for index in range(1, 103)]
    service = _service_type()(
        _Sessions(),
        plugins=(
            SimpleNamespace(
                plugin_key="sample_plugin", plugin_version="0.1.0", picking_task_plan_applied_handler=_Handler()
            ),
        ),
        transport_creator=SimpleNamespace(),
        workline_repository=worklines,
    )

    async def activate(workline_id: int, **_kwargs: object) -> int:
        if workline_id == 1:
            raise RuntimeError("one workline failed")
        return 0

    service._activate_workline = AsyncMock(side_effect=activate)

    await service.activate_batch()

    assert [call.args[0] for call in service._activate_workline.await_args_list] == list(range(1, 103))


@pytest.mark.asyncio
async def test_reserved_workline_does_not_create_rack_transport() -> None:
    creator = _Creator()
    reserved = AsyncMock(return_value=True)
    task_reader = AsyncMock()
    service = _service_type()(
        _Sessions(),
        plugins=(
            SimpleNamespace(
                plugin_key="sample_plugin",
                plugin_version="0.1.0",
                picking_task_plan_applied_handler=_Handler(),
            ),
        ),
        transport_creator=creator,
        workline_repository=_Worklines(
            SimpleNamespace(is_active=True, is_deleted=False, plugin_key="sample_plugin", plugin_version="0.1.0")
        ),
        task_repository=SimpleNamespace(get_executing_for_workline_for_update=task_reader),
        workline_reserved=reserved,
    )

    assert await service.activate_batch() == 0
    reserved.assert_awaited_once()
    task_reader.assert_not_awaited()
    assert creator.calls == []


@pytest.mark.asyncio
async def test_completed_task_source_obligation_does_not_block_new_executing_task() -> None:
    driver = _BatchDriver(completed_count=1)
    handler = _Handler()
    creator = _Creator()
    line = SimpleNamespace(
        id=7,
        line_code="L-1",
        is_active=True,
        is_deleted=False,
        plugin_key="sample_plugin",
        plugin_version="0.1.0",
        position_bindings={
            "SOURCE_SLOT": {"location_id": "SOURCE-POS", "location_type": "RACK_POSITION"},
            "TARGET_SLOT": {"location_id": "TARGET-POS", "location_type": "RACK_POSITION"},
        },
    )
    task = SimpleNamespace(
        id=1,
        task_id="PICK-NEW",
        status="EXECUTING",
        workline_id=7,
        last_applied_plan_revision=1,
        target_rack_id="TARGET-1",
        target_rack_face="90",
        initial_plan_evidence_id=10,
        last_plan_evidence_id=10,
        plan_blocked_evidence_id=None,
    )
    task_reader = AsyncMock(return_value=task)
    service = _service_type()(
        _Sessions(),
        plugins=(
            SimpleNamespace(
                plugin_key="sample_plugin",
                plugin_version="0.1.0",
                picking_task_plan_applied_handler=handler,
                picking_task_batch_driver=driver,
            ),
        ),
        transport_creator=creator,
        workline_repository=_Worklines(line),
        task_repository=SimpleNamespace(get_executing_for_workline_for_update=task_reader),
        plan_repository=SimpleNamespace(
            list_active_bin_source_racks=AsyncMock(return_value=[]),
            list_active_direct_picks=AsyncMock(return_value=[]),
        ),
        transport_binding_repository=SimpleNamespace(list_task_resource_fence_ids=AsyncMock(return_value=set())),
    )

    assert await service.activate_batch() == 3
    assert driver.context == ("L-1", "PICK-NEW")
    task_reader.assert_awaited_once()
    assert handler.fact is not None
    assert [call["resource_fence_id"] for call in creator.calls] == ["TARGET-1"]


@pytest.mark.asyncio
async def test_batch_creates_one_transport_per_rack_with_plugin_selected_mapping() -> None:
    line = SimpleNamespace(
        id=7,
        line_code="L-1",
        is_active=True,
        is_deleted=False,
        plugin_key="sample_plugin",
        plugin_version="0.1.0",
        position_bindings={
            "SOURCE_SLOT": {"location_id": "SOURCE-POS", "location_type": "RACK_POSITION"},
            "TARGET_SLOT": {"location_id": "TARGET-POS", "location_type": "RACK_POSITION"},
        },
    )
    task = SimpleNamespace(
        id=1,
        task_id="TASK-1",
        status="EXECUTING",
        workline_id=7,
        last_applied_plan_revision=2,
        target_rack_id="TARGET-1",
        target_rack_face="90",
        initial_plan_evidence_id=10,
        last_plan_evidence_id=12,
        plan_blocked_evidence_id=None,
    )
    rows = [
        SimpleNamespace(id=1, rack_id="BIN-2", rack_face="90", plan_revision=1, source_evidence_id=12),
        SimpleNamespace(id=2, rack_id="BIN-1", rack_face="90", plan_revision=1, source_evidence_id=11),
        SimpleNamespace(id=3, rack_id="BIN-1", rack_face="270", plan_revision=1, source_evidence_id=11),
    ]
    handler = _Handler()
    creator = _Creator()
    batch_driver = _BatchDriver()
    completion_driver = _CompletionDriver()
    service = _service_type()(
        _Sessions(),
        plugins=(
            SimpleNamespace(
                plugin_key="sample_plugin",
                plugin_version="0.1.0",
                picking_task_plan_applied_handler=handler,
                picking_task_batch_driver=batch_driver,
                picking_task_completion_driver=completion_driver,
            ),
        ),
        transport_creator=creator,
        workline_repository=_Worklines(line),
        task_repository=SimpleNamespace(get_executing_for_workline_for_update=AsyncMock(return_value=task)),
        plan_repository=SimpleNamespace(
            list_active_bin_source_racks=AsyncMock(return_value=rows),
            list_active_direct_picks=AsyncMock(return_value=[]),
        ),
        transport_binding_repository=SimpleNamespace(
            list_task_resource_fence_ids=AsyncMock(return_value=set()),
        ),
    )

    assert await service.activate_batch() == 5
    assert batch_driver.context == ("L-1", "TASK-1")
    assert completion_driver.context == ("L-1", "TASK-1")
    assert [(rack.rack_id, rack.rack_faces) for rack in handler.fact.pending_bin_source_racks] == [
        ("BIN-2", ("90",)),
        ("BIN-1", ("90", "270")),
    ]
    assert [call["resource_fence_id"] for call in creator.calls] == ["TARGET-1", "BIN-2", "BIN-1"]
    assert [call["source_evidence_id"] for call in creator.calls] == [10, 12, 11]
    assert [call["step"] for call in creator.calls] == [
        "PICKING_TASK_TARGET_RACK_IN",
        "PICKING_TASK_BIN_SOURCE_RACK_IN",
        "PICKING_TASK_BIN_SOURCE_RACK_IN",
    ]
    assert [call["correlation_id"] for call in creator.calls] == [
        "pt:1:e:10:rack:TARGET-1",
        "pt:1:e:12:rack:BIN-2",
        "pt:1:e:11:rack:BIN-1",
    ]


@pytest.mark.asyncio
async def test_old_transport_failure_does_not_block_new_rack_submission() -> None:
    line = SimpleNamespace(
        id=7,
        is_active=True,
        is_deleted=False,
        plugin_key="sample_plugin",
        plugin_version="0.1.0",
        position_bindings={
            "SOURCE_SLOT": {"location_id": "SOURCE-POS", "location_type": "RACK_POSITION"},
            "TARGET_SLOT": {"location_id": "TARGET-POS", "location_type": "RACK_POSITION"},
        },
    )
    task = SimpleNamespace(
        id=1,
        task_id="TASK-1",
        status="EXECUTING",
        workline_id=7,
        last_applied_plan_revision=1,
        plan_blocked_evidence_id=None,
        target_rack_id="TARGET-1",
        target_rack_face="90",
        initial_plan_evidence_id=10,
        last_plan_evidence_id=11,
    )
    creator = _Creator()
    service = _service_type()(
        _Sessions(),
        plugins=(
            SimpleNamespace(
                plugin_key="sample_plugin",
                plugin_version="0.1.0",
                picking_task_plan_applied_handler=_Handler(),
            ),
        ),
        transport_creator=creator,
        workline_repository=_Worklines(line),
        task_repository=SimpleNamespace(get_executing_for_workline_for_update=AsyncMock(return_value=task)),
        plan_repository=SimpleNamespace(
            list_active_bin_source_racks=AsyncMock(
                return_value=[SimpleNamespace(rack_id="BIN-1", rack_face="90", plan_revision=1, source_evidence_id=11)]
            ),
            list_active_direct_picks=AsyncMock(return_value=[]),
        ),
        transport_binding_repository=SimpleNamespace(list_task_resource_fence_ids=AsyncMock(return_value=set())),
    )

    assert await service.activate_batch() == 2
    assert [call["resource_fence_id"] for call in creator.calls] == ["TARGET-1", "BIN-1"]


def test_host_allows_handler_to_defer_pending_source_racks() -> None:
    fact = PickingTaskPlanAppliedFact(
        fact_id="FACT-1",
        evidence_id="12",
        fact_version="1.0",
        task_id="TASK-1",
        plan_revision=2,
        target_rack=None,
        pending_bin_source_racks=(
            PickingTaskPlanRack("BIN-A", ("90",), "11", 1),
            PickingTaskPlanRack("BIN-B", ("270",), "12", 2),
        ),
        position_bindings=(PositionBindingSnapshot("SOURCE_SLOT", "SOURCE-POS", "RACK_POSITION"),),
    )
    result = PickingTaskPlanHandlingResult(transports=())

    _service_type()._validate_result(fact, result)


@pytest.mark.parametrize(
    ("intent_index", "change", "binding_type"),
    [
        (0, {"target_face": "270"}, None),
        (1, {"target_face": "180"}, None),
        (0, {"position_role": "UNBOUND_SLOT"}, None),
        (1, {"target": TransportRackPosition("OTHER-POS")}, None),
        (1, {}, "ZONE"),
    ],
)
def test_host_rejects_transport_outside_plan_and_position_contract(
    intent_index: int, change: dict[str, object], binding_type: str | None
) -> None:
    fact = PickingTaskPlanAppliedFact(
        fact_id="FACT-1",
        evidence_id="12",
        fact_version="1.0",
        task_id="TASK-1",
        plan_revision=2,
        target_rack=PickingTaskPlanRack("TARGET-1", ("90",), "10", 1),
        pending_bin_source_racks=(PickingTaskPlanRack("BIN-1", ("90", "270"), "12", 2),),
        position_bindings=(
            PositionBindingSnapshot("TARGET_SLOT", "TARGET-POS", "RACK_POSITION"),
            PositionBindingSnapshot("SOURCE_SLOT", "SOURCE-POS", binding_type or "RACK_POSITION"),
        ),
    )
    result = _Handler()(fact)
    transports = list(result.transports)
    transports[intent_index] = replace(transports[intent_index], **change)

    with pytest.raises(ValueError, match="outside the frozen fact"):
        _service_type()._validate_result(fact, PickingTaskPlanHandlingResult(tuple(transports)))


@pytest.mark.parametrize("include_target", [True, False])
def test_host_allows_source_subset_but_requires_pending_target(include_target):
    fact = PickingTaskPlanAppliedFact(
        fact_id="FACT-1",
        evidence_id="12",
        fact_version="1.0",
        task_id="TASK-1",
        plan_revision=2,
        target_rack=PickingTaskPlanRack("TARGET-1", ("90",), "10", 1),
        pending_bin_source_racks=(PickingTaskPlanRack("BIN-1", ("90",), "12", 2),),
        position_bindings=(
            PositionBindingSnapshot("TARGET_SLOT", "TARGET-POS", "RACK_POSITION"),
            PositionBindingSnapshot("SOURCE_SLOT", "SOURCE-POS", "RACK_POSITION"),
        ),
    )
    target, source = _Handler()(fact).transports
    result = PickingTaskPlanHandlingResult((target,) if include_target else (source,))
    if include_target:
        _service_type()._validate_result(fact, result)
    else:
        with pytest.raises(ValueError, match="omitted a pending target"):
            _service_type()._validate_result(fact, result)
