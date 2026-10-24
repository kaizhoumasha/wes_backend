"""纯规则只消费已确认的来源授权与当前栈顶事实，不负责设备映射。"""

from dataclasses import replace

import pytest
from wes_plugin_sdk import BinWorkPlanReady, PickingCellSource


def fact(**changes):
    from automatic_picking.handlers.source_authorization import WorkSourceFact

    return replace(
        WorkSourceFact(
            work_id=1,
            workline_id=7,
            authorization_evidence_id=31,
            source=BinWorkPlanReady("CELL-7", 7, 2, 1, 20.0, 15.0),
            consumed_pickup_id=None,
            is_initial=True,
            top_removed=None,
            work_open=True,
            requirement_active=True,
            source_ready=True,
        ),
        **changes,
    )


def test_initial_ready_authorizes_only_the_wms_selected_cell():
    from automatic_picking.handlers.source_authorization import source_pickup_allowed

    current = fact()
    assert source_pickup_allowed(current)
    assert current.source.cell_id == "CELL-7" and current.source.cell_index == 7


@pytest.mark.parametrize(
    "changes",
    [
        {"authorization_evidence_id": None},
        {"source": None},
        {"consumed_pickup_id": "original"},
        {"work_open": False},
        {"requirement_active": False},
        {"source_ready": False},
        {"is_initial": False, "top_removed": None},
        {"is_initial": False, "top_removed": False},
    ],
)
def test_no_new_pickup_without_current_authorization_and_required_facts(changes):
    from automatic_picking.handlers.source_authorization import source_pickup_allowed

    assert not source_pickup_allowed(fact(**changes))


@pytest.mark.parametrize("cell_id,index", [("CELL-7", 7), ("CELL-2", 2)])
def test_same_cell_next_reel_and_other_cell_both_wait_for_reliable_top_removal(cell_id, index):
    from automatic_picking.handlers.source_authorization import source_pickup_allowed

    current = fact(is_initial=False, source=PickingCellSource(cell_id, index, 2, 1, 20.0, 15.0))
    assert not source_pickup_allowed(current)
    assert source_pickup_allowed(replace(current, top_removed=True))


def test_new_round_cannot_treat_an_unknown_current_source_as_empty():
    from automatic_picking.handlers.source_authorization import source_pickup_allowed

    assert not source_pickup_allowed(fact(work_id=2, source_ready=False))
