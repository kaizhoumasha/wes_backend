"""WorkLine 准入先于动作；物理位置按箱码保留，不依赖箱执行实体。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.app.execution.services.position_projection_service import (
    PositionProjectionAuthorityError,
    PositionProjectionInvariantViolation,
    PositionProjectionService,
    ProjectionCausalRelation,
    ProjectionEffectPhase,
    ProjectionSource,
    compare_projection_sources,
)
from src.app.transport.contracts import TransportExecutionAuthority


class Repository:
    def __init__(self):
        self.calls = []
        self.line = SimpleNamespace(id=7, is_active=True)
        self.projection = None
        self.other_line_occupied = False

    async def get_workline_for_update(self, db, workline_id):
        self.calls.append("workline")
        return self.line

    async def lock_object_authority(self, db, object_type, object_id):
        self.calls.append("object")

    async def get_for_update(self, db, object_type, object_id):
        return self.projection

    async def is_workline_position(self, db, workline_id, position):
        return self.other_line_occupied

    async def add(self, db, projection):
        self.projection = projection
        return projection

    async def flush(self, db):
        self.calls.append("flush")


class Bindings:
    def __init__(self, *, causal_token=1, workline_id=7):
        self.binding = SimpleNamespace(causal_token=causal_token, workline_id=workline_id)

    async def get_by_client_request_id(self, _db, _client_request_id):
        return self.binding


async def apply(
    repo,
    *,
    object_id="BIN-001",
    authority=TransportExecutionAuthority(workline_id=7),
    causal_token=1,
):
    return await PositionProjectionService(
        repository=repo, binding_repository=Bindings(causal_token=causal_token)
    ).apply_transport_result(
        object(),
        authority=authority,
        object_type="BIN",
        object_id=object_id,
        position={"kind": "HANDOFF_POSITION", "location_code": "OUTSIDE"},
        position_unknown=False,
        arrival_face=None,
        client_request_id="request-1",
        operation_id="operation-1",
        transport_task_id="transport-1",
        updated_at=datetime(2026, 9, 6),
    )


@pytest.mark.asyncio
async def test_bin_code_gets_retained_position_without_bin_execution():
    repo = Repository()
    projection = await apply(repo)
    assert projection.object_id == "BIN-001"
    assert projection.workline_id == 7
    assert projection.position_json["location_code"] == "OUTSIDE"
    assert repo.calls == ["workline", "object", "flush"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown,occupied", [(True, False), (False, True)])
async def test_unordered_other_task_retains_position_and_marks_it_unconfirmed(unknown, occupied):
    repo = Repository()
    repo.projection = SimpleNamespace(
        object_type="BIN",
        object_id="BIN-001",
        workline_id=8,
        position_unknown=unknown,
        position_json={"location_code": "FOREIGN"},
        source_transport_task_id="transport-new",
        source_operation_id="new-result",
        source_causal_token=2,
        source_effect_phase=ProjectionEffectPhase.FINAL_RESULT,
        arrival_face=None,
        updated_at=datetime(2026, 9, 5),
    )
    repo.other_line_occupied = occupied
    await apply(repo)
    assert repo.projection.workline_id == 8
    assert repo.projection.position_unknown is unknown
    assert repo.projection.position_json == {"location_code": "FOREIGN"}
    assert repo.projection.source_transport_task_id == "transport-new"
    assert repo.projection.source_operation_id == "new-result"
    assert repo.projection.updated_at == datetime(2026, 9, 5)


@pytest.mark.asyncio
async def test_same_task_result_can_refine_unknown_position():
    repo = Repository()
    repo.projection = SimpleNamespace(
        object_type="BIN",
        object_id="BIN-001",
        workline_id=7,
        position_unknown=True,
        position_json=None,
        source_transport_task_id="transport-1",
        source_operation_id="operation-0",
        source_causal_token=1,
        source_effect_phase=ProjectionEffectPhase.ACK_INVALIDATION,
    )
    projection = await apply(repo)
    assert projection.workline_id == 7
    assert projection.position_unknown is False
    assert projection.source_effect_phase == ProjectionEffectPhase.FINAL_RESULT


@pytest.mark.asyncio
async def test_older_transport_result_cannot_overwrite_newer_projection():
    repo = Repository()
    repo.projection = SimpleNamespace(
        object_type="BIN",
        object_id="BIN-001",
        workline_id=7,
        position_unknown=False,
        position_json={"kind": "HANDOFF_POSITION", "location_code": "NEWER"},
        source_transport_task_id="transport-2",
        source_operation_id="operation-2",
        source_causal_token=2,
        source_effect_phase=ProjectionEffectPhase.FINAL_RESULT,
        arrival_face=None,
        updated_at=datetime(2026, 9, 6),
    )

    projection = await apply(repo, causal_token=1)

    assert projection.position_json["location_code"] == "NEWER"
    assert "flush" not in repo.calls


@pytest.mark.asyncio
async def test_final_result_prevents_late_ack_invalidation_for_same_source():
    repo = Repository()
    repo.projection = SimpleNamespace(
        object_type="BIN",
        object_id="BIN-001",
        workline_id=7,
        position_unknown=False,
        position_json={"kind": "HANDOFF_POSITION", "location_code": "FINAL"},
        source_transport_task_id="transport-1",
        source_operation_id="operation-1",
        source_causal_token=1,
        source_effect_phase=ProjectionEffectPhase.FINAL_RESULT,
        arrival_face=None,
        updated_at=datetime(2026, 9, 6),
    )
    service = PositionProjectionService(repository=repo, binding_repository=Bindings(causal_token=1))

    projection = await service.invalidate_transport_member(
        object(),
        authority=TransportExecutionAuthority(workline_id=7),
        object_type="BIN",
        object_id="BIN-001",
        client_request_id="request-1",
        operation_id="operation-1",
        transport_task_id="transport-1",
        updated_at=datetime(2026, 9, 7),
    )

    assert projection.position_unknown is False
    assert projection.position_json["location_code"] == "FINAL"
    assert "flush" not in repo.calls


@pytest.mark.asyncio
async def test_stopped_workline_does_not_block_retention_of_original_task_result():
    repo = Repository()
    repo.line.is_active = False
    projection = await apply(repo)
    assert projection.position_json["location_code"] == "OUTSIDE"
    assert repo.calls == ["workline", "object", "flush"]


@pytest.mark.asyncio
async def test_missing_workline_keeps_task_fact_without_creating_invalid_projection_owner():
    repo = Repository()
    repo.line = None
    assert await apply(repo) is None
    assert repo.projection is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unknown,source",
    [
        (True, {"kind": "HANDOFF_POSITION", "location_code": "P1"}),
        (False, {"kind": "HANDOFF_POSITION", "location_code": "OTHER"}),
    ],
)
@pytest.mark.parametrize("workline_id", [7, 8])
async def test_new_transport_ignores_stale_or_unknown_projection(unknown, source, workline_id):
    repo = Repository()
    repo.projection = SimpleNamespace(
        workline_id=workline_id,
        position_unknown=unknown,
        position_json={"kind": "HANDOFF_POSITION", "location_code": "P1"},
    )
    await PositionProjectionService(repository=repo).admit_transport_member(
        object(),
        authority=TransportExecutionAuthority(workline_id=7),
        object_type="BIN",
    )
    assert repo.projection.workline_id == workline_id
    assert repo.projection.position_unknown == unknown
    assert "flush" not in repo.calls


@pytest.mark.asyncio
async def test_debug_result_without_authority_is_noop():
    repo = Repository()
    assert await apply(repo, authority=None) is None
    assert repo.calls == []


@pytest.mark.asyncio
async def test_explicitly_stopped_workline_still_rejects_new_transport():
    repo = Repository()
    repo.line.is_active = False
    with pytest.raises(PositionProjectionAuthorityError, match="active WorkLine"):
        await PositionProjectionService(repository=repo).admit_transport_member(
            object(), authority=TransportExecutionAuthority(workline_id=7), object_type="RACK"
        )
    assert repo.calls == ["workline"]


@pytest.mark.parametrize(
    ("incoming_token", "current_token", "incoming_phase", "current_phase", "expected"),
    [
        (1, 1, ProjectionEffectPhase.FINAL_RESULT, ProjectionEffectPhase.FINAL_RESULT, ProjectionCausalRelation.SAME),
        (1, 2, ProjectionEffectPhase.FINAL_RESULT, ProjectionEffectPhase.FINAL_RESULT, ProjectionCausalRelation.BEFORE),
        (2, 1, ProjectionEffectPhase.FINAL_RESULT, ProjectionEffectPhase.FINAL_RESULT, ProjectionCausalRelation.AFTER),
        (
            4,
            4,
            ProjectionEffectPhase.ACK_INVALIDATION,
            ProjectionEffectPhase.FINAL_RESULT,
            ProjectionCausalRelation.BEFORE,
        ),
        (
            4,
            4,
            ProjectionEffectPhase.FINAL_RESULT,
            ProjectionEffectPhase.ACK_INVALIDATION,
            ProjectionCausalRelation.AFTER,
        ),
    ],
)
def test_projection_source_comparator_is_domain_scoped(
    incoming_token, current_token, incoming_phase, current_phase, expected
):
    incoming = ProjectionSource("BIN", "B-1", "source-1", incoming_token, incoming_phase)
    current = ProjectionSource("BIN", "B-1", "source-1", current_token, current_phase)

    assert compare_projection_sources(incoming, current) == expected


def test_projection_source_comparator_fails_closed_for_cross_object_or_identity_mismatch():
    incoming = ProjectionSource("BIN", "B-1", "source-1", 1, ProjectionEffectPhase.FINAL_RESULT)
    assert (
        compare_projection_sources(
            incoming,
            ProjectionSource("RACK", "R-1", "source-1", 1, ProjectionEffectPhase.FINAL_RESULT),
        )
        == ProjectionCausalRelation.INCOMPARABLE
    )
    assert (
        compare_projection_sources(
            incoming,
            ProjectionSource("BIN", "B-1", "source-2", 1, ProjectionEffectPhase.FINAL_RESULT),
        )
        == ProjectionCausalRelation.INCOMPARABLE
    )


@pytest.mark.parametrize(
    ("incoming", "current", "expected"),
    [
        (
            ProjectionSource("BIN", "B-1", "task-old", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "task-new", 20, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.BEFORE,
        ),
        (
            ProjectionSource("BIN", "B-1", "task-new", 20, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "task-old", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.AFTER,
        ),
        (
            ProjectionSource("BIN", "B-1", "task-a", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "task-b", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.INCOMPARABLE,
        ),
        (
            ProjectionSource("BIN", "B-1", "task-a", 10, ProjectionEffectPhase.ACK_INVALIDATION),
            ProjectionSource("BIN", "B-1", "task-a", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.BEFORE,
        ),
        (
            ProjectionSource("BIN", "B-1", "task-a", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "task-a", 10, ProjectionEffectPhase.ACK_INVALIDATION),
            ProjectionCausalRelation.AFTER,
        ),
    ],
)
def test_cross_transport_task_causal_ordering_truth_table(incoming, current, expected):
    """不同 TransportTask 只按 frozen token 排序；并列 identity 必须 fail closed。"""

    assert compare_projection_sources(incoming, current) is expected


@pytest.mark.parametrize(
    ("domain", "incoming", "current", "expected"),
    [
        (
            "taskless-drain-after-picking",
            ProjectionSource("RACK", "R-1", "drain-task", 20, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("RACK", "R-1", "picking-task", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.AFTER,
        ),
        (
            "picking-before-taskless-drain",
            ProjectionSource("RACK", "R-1", "picking-task", 10, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("RACK", "R-1", "drain-task", 20, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.BEFORE,
        ),
        (
            "bin-batch-cross-task",
            ProjectionSource("BIN", "B-1", "new-bin-task", 40, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "old-bin-task", 30, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.AFTER,
        ),
        (
            "multi-member-same-task-phase-progression",
            ProjectionSource("BIN", "B-1", "multi-bin-task", 50, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "multi-bin-task", 50, ProjectionEffectPhase.ACK_INVALIDATION),
            ProjectionCausalRelation.AFTER,
        ),
        (
            "multi-member-cross-object",
            ProjectionSource("BIN", "B-2", "multi-bin-task", 50, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("BIN", "B-1", "multi-bin-task", 50, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.INCOMPARABLE,
        ),
        (
            "conflicting-source-same-token",
            ProjectionSource("RACK", "R-1", "task-b", 60, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionSource("RACK", "R-1", "task-a", 60, ProjectionEffectPhase.FINAL_RESULT),
            ProjectionCausalRelation.INCOMPARABLE,
        ),
    ],
)
def test_projection_domain_source_pairs_are_ordered_or_fail_closed(domain, incoming, current, expected):
    del domain
    assert compare_projection_sources(incoming, current) is expected


@pytest.mark.asyncio
async def test_incomparable_projection_source_fails_closed_without_mutation():
    repo = Repository()
    repo.projection = SimpleNamespace(
        object_type="RACK",
        object_id="RACK-001",
        workline_id=7,
        position_unknown=False,
        position_json={"kind": "RACK_POSITION", "location_code": "CURRENT"},
        source_transport_task_id="transport-current",
        source_operation_id="operation-current",
        source_causal_token=7,
        source_effect_phase=ProjectionEffectPhase.FINAL_RESULT,
        arrival_face="A",
        updated_at=datetime(2026, 9, 6),
    )
    service = PositionProjectionService(repository=repo, binding_repository=Bindings(causal_token=7))

    with pytest.raises(PositionProjectionInvariantViolation, match="not causally comparable"):
        await service.apply_transport_result(
            object(),
            authority=TransportExecutionAuthority(workline_id=7),
            object_type="RACK",
            object_id="RACK-001",
            position={"kind": "RACK_POSITION", "location_code": "INCOMING"},
            position_unknown=False,
            arrival_face="B",
            client_request_id="request-incoming",
            operation_id="operation-incoming",
            transport_task_id="transport-incoming",
            updated_at=datetime(2026, 9, 7),
        )

    assert repo.projection.position_json == {"kind": "RACK_POSITION", "location_code": "CURRENT"}
    assert repo.projection.source_transport_task_id == "transport-current"
    assert repo.projection.source_operation_id == "operation-current"
    assert repo.projection.arrival_face == "A"
    assert "flush" not in repo.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival_order", [("old", "new"), ("new", "old")])
async def test_out_of_order_cross_task_replay_keeps_newest_projection(arrival_order):
    repo = Repository()
    bindings = {
        "old-request": SimpleNamespace(causal_token=10, workline_id=7),
        "new-request": SimpleNamespace(causal_token=20, workline_id=7),
    }

    class OrderedBindings:
        async def get_by_client_request_id(self, _db, client_request_id):
            return bindings[client_request_id]

    service = PositionProjectionService(repository=repo, binding_repository=OrderedBindings())
    sources = {
        "old": ("old-request", "task-old", "OLD"),
        "new": ("new-request", "task-new", "NEW"),
    }
    for label in arrival_order:
        request_id, task_id, location = sources[label]
        await service.apply_transport_result(
            object(),
            authority=TransportExecutionAuthority(workline_id=7),
            object_type="BIN",
            object_id="BIN-001",
            position={"kind": "HANDOFF_POSITION", "location_code": location},
            position_unknown=False,
            arrival_face=None,
            client_request_id=request_id,
            operation_id=f"operation-{label}",
            transport_task_id=task_id,
            updated_at=datetime(2026, 9, 6),
        )

    assert repo.projection.source_transport_task_id == "task-new"
    assert repo.projection.source_causal_token == 20
    assert repo.projection.position_json["location_code"] == "NEW"
