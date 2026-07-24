"""Rack EXTERNAL_HTTP frozen binding 重入合同。"""

from __future__ import annotations

import pytest

from src.app.rack.models import RackTaskType
from src.app.resource.models import RackKind
from src.app.sys.models import (
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from tests.rack.test_rack_operation_service import (
    CLASSIFIER_WORK_POSITION_CODE,
    CLASSIFIER_WORK_POSITION_ROLE,
    RACK_OPERATION_TARGET_CODE,
    RACK_TRANSPORT_OPERATION_TYPE,
    FakeDb,
    FakeOutboxRepository,
    _external_outbox,
    _request_classifier_replacement,
    _service,
)


def _persisted_classifier_outbox_for_replay(*, operation_key: str) -> SystemOutbox:
    dispatch_key = f"rack-operation:{operation_key}:2:ALLOCATE_AND_MOVE_RACK"
    return _external_outbox(
        id=188,
        session_id=300,
        workline_id=45,
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        dispatch_key=dispatch_key,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code=RACK_OPERATION_TARGET_CODE,
        payload_json={
            "operation_key": operation_key,
            "operation_type": RACK_TRANSPORT_OPERATION_TYPE,
            "sequence_no": 2,
            "task_type": RackTaskType.ALLOCATE_AND_MOVE_RACK.value,
            "workline_code": "WL-SMT-01",
            "rack_kind": RackKind.SINGLE_LAYER.value,
            "source_position_code": None,
            "target_position_code": CLASSIFIER_WORK_POSITION_CODE,
            "target_position_role": CLASSIFIER_WORK_POSITION_ROLE,
            "trace_id": "trace-frozen-replay",
            "dispatch_key": dispatch_key,
            "actions": {"action": RackTaskType.ALLOCATE_AND_MOVE_RACK.value, "required": True},
        },
        status=SystemOutboxStatus.NEW,
    )


@pytest.mark.asyncio
async def test_request_replay_after_binding_rotation_reuses_persisted_binding_without_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_key = "op-frozen-binding-replay"
    system_outbox_repository = FakeOutboxRepository()
    existing_outbox = system_outbox_repository.add_existing(
        _persisted_classifier_outbox_for_replay(operation_key=operation_key)
    )
    persisted_snapshot = dict(existing_outbox.target_snapshot_json or {})
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )

    def fail_current_target_lookup(_target_code: str) -> None:
        raise AssertionError("existing dispatch replay must not resolve the current endpoint registry")

    monkeypatch.setattr(
        "src.app.sys.services.endpoint_registry.endpoint_registry.resolve",
        fail_current_target_lookup,
    )

    tasks = await _request_classifier_replacement(
        service,
        FakeDb(),
        operation_key=operation_key,
        trace_id="trace-frozen-replay-new",
        include_move_out=False,
    )

    assert len(tasks) == 1
    assert lifecycle.calls[0]["outbox"] is existing_outbox
    assert existing_outbox.target_snapshot_json == persisted_snapshot


@pytest.mark.asyncio
async def test_request_replay_after_binding_rotation_rejects_changed_immutable_payload_without_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_key = "op-frozen-binding-payload-conflict"
    system_outbox_repository = FakeOutboxRepository()
    system_outbox_repository.add_existing(_persisted_classifier_outbox_for_replay(operation_key=operation_key))
    service, _repo, lifecycle, _placements = _service(
        active_placements=[],
        active_count=0,
        system_outbox_repository=system_outbox_repository,
    )

    def fail_current_target_lookup(_target_code: str) -> None:
        raise AssertionError("existing dispatch replay must not resolve the current endpoint registry")

    monkeypatch.setattr(
        "src.app.sys.services.endpoint_registry.endpoint_registry.resolve",
        fail_current_target_lookup,
    )

    with pytest.raises(ValueError, match="existing rack operation outbox payload differs after dispatch"):
        await _request_classifier_replacement(
            service,
            FakeDb(),
            operation_key=operation_key,
            trace_id="trace-frozen-replay-new",
            include_move_out=False,
            work_position_code="CLASSIFIER-WORK-ROTATED",
        )

    assert lifecycle.calls == []
