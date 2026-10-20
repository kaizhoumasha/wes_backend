"""正常补架纯规则；不创建请求、current 或物理动作。"""

from dataclasses import replace

import pytest
from wes_plugin_sdk import (
    OperationConflict,
    OperationUnavailable,
    RackFaceSequence,
    ReturnBufferDrainReady,
    ReturnBufferDrainWait,
)


def member(return_id, *, line=7, location="BUFFER", allocated=False, eligible=True):
    from automatic_picking.drain_replenishment import DrainBufferFact

    return DrainBufferFact(return_id, line, location, allocated, eligible)


def fact(**changes):
    from automatic_picking.drain_replenishment import DrainReplenishmentFact

    values = {
        "workline_id": 7,
        "operation_id": "current-request",
        "reservation_operation_id": "original-ready-request",
        "return_ids": (11, 12, 13),
        "returned_ids": (),
        "inflight_return_ids": (),
        "fifo": (member(11), member(12), member(13)),
        "buffer_location_code": "BUFFER",
        "outcome": ReturnBufferDrainReady((RackFaceSequence("R1", ("A", "B")),)),
        "outcome_operation_id": "current-request",
        "published": True,
        "departure_handoffs": (("R1", "original-ctu03"),),
        "retry_at": None,
        "now": 5000,
    }
    return DrainReplenishmentFact(**(values | changes))


def evaluate(value):
    from automatic_picking.drain_replenishment import evaluate_replenishment

    return evaluate_replenishment(value)


def waiting(**changes):
    return fact(**({"outcome": ReturnBufferDrainWait(1000), "departure_handoffs": (), "retry_at": 5000} | changes))


def test_independent_inflight_responsibility_is_kept_without_blocking_other_requests():
    result = evaluate(fact(inflight_return_ids=(11,), fifo=(member(12), member(13))))
    assert result.action == "REQUEST"
    assert result.remaining_return_ids == (11, 12, 13)
    assert result.request_return_ids == (12, 13)


def test_all_supply_handoffs_are_required_without_reading_closed_faces_or_final_positions():
    ready = ReturnBufferDrainReady((RackFaceSequence("R1", ("A", "B")), RackFaceSequence("R2", ("C",))))
    assert evaluate(fact(outcome=ready)).action == "WAIT"
    result = evaluate(fact(outcome=ready, departure_handoffs=(("R2", "out-2"), ("R1", "out-1"))))
    assert result.action == "REQUEST"
    assert result.request_return_ids == (11, 12, 13)


def test_only_positive_returned_execution_ids_reduce_responsibility_in_original_order():
    result = evaluate(fact(returned_ids=(12,), fifo=(member(11), member(13))))
    assert result.remaining_return_ids == (11, 13)
    assert result.request_return_ids == (11, 13)


@pytest.mark.parametrize(
    "blocked",
    [
        {"location": None},
        {"location": "WORK_POSITION"},
        {"allocated": True},
        {"eligible": False},
    ],
)
def test_request_stops_at_first_unqualified_member_and_never_skips_it(blocked):
    result = evaluate(fact(fifo=(member(11), member(12, **blocked), member(13))))
    assert result.request_return_ids == (11,)
    result = evaluate(fact(fifo=(member(11, **blocked), member(12), member(13))))
    assert result.action == "WAIT"
    assert result.request_return_ids == ()
    assert result.remaining_return_ids == (11, 12, 13)


def test_new_execution_stops_the_prefix_even_when_following_members_belong_to_original_scope():
    result = evaluate(fact(fifo=(member(11), member(99), member(12), member(13))))
    assert result.request_return_ids == (11,)
    assert result.remaining_return_ids == (11, 12, 13)
    assert evaluate(fact(fifo=(member(99), member(11), member(12), member(13)))).action == "WAIT"


def test_missing_execution_is_not_inferred_inflight_from_absent_history():
    with pytest.raises(ValueError, match="scope"):
        evaluate(fact(fifo=(member(12), member(13))))


def test_inflight_only_keeps_responsibility_without_zero_quantity_request():
    result = evaluate(fact(inflight_return_ids=(11, 12, 13), fifo=()))
    assert result.action == "WAIT"
    assert result.request_return_ids == ()
    assert result.remaining_return_ids == (11, 12, 13)


@pytest.mark.parametrize("now,action", [(4999, "WAIT"), (5000, "REQUEST"), (9000, "REQUEST")])
def test_determinate_wait_uses_frozen_due_time_including_exact_boundary(now, action):
    value = waiting(now=now)
    assert evaluate(value).action == action
    assert value.retry_at == 5000


def test_due_wait_observes_ordinary_entry_consumption_and_never_adds_new_arrivals():
    result = evaluate(waiting(returned_ids=(11,), fifo=(member(12), member(13), member(99))))
    assert result.remaining_return_ids == (12, 13)
    assert result.request_return_ids == (12, 13)


def test_due_wait_keeps_independent_inflight_and_only_requests_unallocated_prefix():
    result = evaluate(waiting(inflight_return_ids=(11,), fifo=(member(12), member(13))))
    assert result.remaining_return_ids == (11, 12, 13)
    assert result.request_return_ids == (12, 13)
    assert evaluate(waiting(inflight_return_ids=(11, 12, 13), fifo=())).action == "WAIT"


def test_published_wait_can_exit_when_original_responsibility_is_satisfied_even_before_due():
    result = evaluate(waiting(returned_ids=(13, 11, 12), fifo=(member(99),), now=1))
    assert result.action == "EXIT_WAIT"
    assert result.remaining_return_ids == result.request_return_ids == ()


def test_satisfied_wait_exit_does_not_require_live_buffer_or_fifo():
    result = evaluate(waiting(returned_ids=(11, 12, 13), buffer_location_code=None, fifo=(), now=1))
    assert result.action == "EXIT_WAIT" and result.request_return_ids == ()


def test_request_evaluation_still_requires_an_authoritative_buffer():
    with pytest.raises(ValueError):
        evaluate(waiting(buffer_location_code=None))


def test_ready_with_zero_remaining_does_not_request_or_close_wait():
    result = evaluate(fact(returned_ids=(11, 12, 13), fifo=()))
    assert result.action == "WAIT"
    assert result.request_return_ids == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": None, "outcome_operation_id": None, "published": False},
        {"published": False},
        {"outcome": OperationUnavailable()},
        {"outcome": OperationConflict("STATE_CONFLICT")},
    ],
)
def test_unknown_unpublished_and_conflict_keep_original_owner_instead_of_renewing(changes):
    value = fact(**({"departure_handoffs": ()} | changes))
    result = evaluate(value)
    assert result.action == "RECOVER_CURRENT"
    assert result.request_return_ids == ()
    assert result.remaining_return_ids == (11, 12, 13)


def test_unpublished_wait_does_not_exit_or_renew_even_with_no_remaining_demand():
    result = evaluate(waiting(returned_ids=(11, 12, 13), fifo=(), published=False, retry_at=None))
    assert result.action == "RECOVER_CURRENT"


@pytest.mark.parametrize(
    "changes,action",
    [
        ({"outcome": None, "outcome_operation_id": None, "published": False}, "RECOVER_CURRENT"),
        ({"published": False}, "RECOVER_CURRENT"),
        ({"outcome": OperationUnavailable()}, "RECOVER_CURRENT"),
        ({"outcome": ReturnBufferDrainWait(1000), "retry_at": 6000}, "WAIT"),
        ({}, "WAIT"),
    ],
)
def test_original_obligation_is_resolved_before_requiring_fresh_fifo_candidates(changes, action):
    result = evaluate(fact(**({"fifo": (), "departure_handoffs": ()} | changes)))
    assert result.action == action
    assert result.remaining_return_ids == (11, 12, 13)
    assert result.request_return_ids == ()


def test_stale_result_cannot_supply_current_decision():
    with pytest.raises(ValueError, match="identity"):
        evaluate(fact(outcome_operation_id="previous-request"))


@pytest.mark.parametrize("reservation", [None, "", " "])
def test_replenishment_requires_explicit_original_ready_identity(reservation):
    with pytest.raises(ValueError, match="reservation"):
        evaluate(replace(fact(), reservation_operation_id=reservation))


def test_initial_wait_is_not_misclassified_as_a_replenishment_wait():
    with pytest.raises(ValueError, match="reservation"):
        evaluate(replace(waiting(), reservation_operation_id="current-request"))


def test_first_ready_can_supply_the_original_reservation_exhaustion_boundary():
    result = evaluate(replace(fact(), reservation_operation_id="current-request"))
    assert result.action == "REQUEST"


@pytest.mark.parametrize(
    "changes",
    [
        {"return_ids": (11, 11, 13)},
        {"returned_ids": (99,)},
        {"returned_ids": (11, 11)},
        {"inflight_return_ids": (99,)},
        {"inflight_return_ids": (11,)},
        {"returned_ids": (11,)},
        lambda: {"fifo": (member(11), member(11), member(13))},
        lambda: {"fifo": (member(12), member(11), member(13))},
    ],
)
def test_conflicting_membership_and_order_fail_closed(changes):
    changes = changes() if callable(changes) else changes
    with pytest.raises(ValueError):
        evaluate(fact(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"workline_id": True},
        {"operation_id": " "},
        {"return_ids": (True, 12, 13)},
        {"return_ids": ()},
        {"return_ids": [11, 12, 13]},
        {"buffer_location_code": ""},
        {"published": 1},
        {"now": True},
        {"now": -1},
        lambda: {"fifo": (member(11, line=8), member(12), member(13))},
        lambda: {"fifo": (member(11, allocated=1), member(12), member(13))},
        lambda: {"fifo": (member(11, eligible=None), member(12), member(13))},
        lambda: {"fifo": (member(11, location=" "), member(12), member(13))},
    ],
)
def test_explicit_input_boundary_rejects_invalid_or_ambiguous_values(changes):
    changes = changes() if callable(changes) else changes
    with pytest.raises(ValueError):
        evaluate(fact(**changes))


@pytest.mark.parametrize(
    "handoffs",
    [
        (("other-rack", "out"),),
        (("R1", ""),),
        (("R1", "out"), ("R1", "other-out")),
        [["R1", "out"]],
    ],
)
def test_handoff_requires_current_supply_and_unique_explicit_original_transport(handoffs):
    with pytest.raises(ValueError):
        evaluate(fact(departure_handoffs=handoffs))


def test_one_transport_cannot_prove_two_supply_handoffs():
    ready = ReturnBufferDrainReady((RackFaceSequence("R1", ("A",)), RackFaceSequence("R2", ("A",))))
    with pytest.raises(ValueError):
        evaluate(fact(outcome=ready, departure_handoffs=(("R1", "out"), ("R2", "out"))))


@pytest.mark.parametrize("retry_at", [None, True, -1])
def test_published_wait_requires_its_frozen_valid_deadline(retry_at):
    with pytest.raises(ValueError, match="retry_at"):
        evaluate(waiting(retry_at=retry_at))


def test_evaluation_does_not_mutate_inputs_or_establish_a_runtime_current():
    value = fact()
    before = replace(value)
    assert evaluate(value) == evaluate(value)
    assert value == before
