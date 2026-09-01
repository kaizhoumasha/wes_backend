from __future__ import annotations

import pytest

from src.app.runtime.orchestration.services.inbox.object_transition_event_service import ObjectTransitionEventService
from src.app.runtime.orchestration.services.runtime_location_event_service import RuntimeLocationEventService


def test_event_services_normalize_required_idempotency_key_parts() -> None:
    assert (
        RuntimeLocationEventService.build_idempotency_key(
            object_type=" BIN ",
            object_key=" bin:1 ",
            location_scope=" RACK ",
            location_code=" rack-1 ",
            business_step=" ARRIVED ",
            source=" EC\\S ",
            source_event_id=" evt-1 ",
        )
        == "runtime-location:evt-1:EC\\\\S:BIN:bin\\:1:RACK:rack-1:ARRIVED"
    )
    assert (
        ObjectTransitionEventService.build_idempotency_key(
            source_event_id=" evt-1 ",
            domain=" RESOURCE ",
            object_type=" BIN ",
            object_key=" bin:1 ",
            projection_type=" PLACEMENT ",
            to_state=" ACTIVE ",
            reason_code=" ARRIVED ",
        )
        == "object-transition:evt-1:RESOURCE:BIN:bin\\:1:PLACEMENT:ACTIVE:ARRIVED"
    )


def test_event_services_reject_blank_required_idempotency_key_parts() -> None:
    with pytest.raises(ValueError, match="object_key 不能为空"):
        RuntimeLocationEventService.build_idempotency_key(
            object_type="BIN",
            object_key=" ",
            location_scope="RACK",
            location_code="rack-1",
            business_step="ARRIVED",
            source="ECS",
        )

    with pytest.raises(ValueError, match="object_key 不能为空"):
        ObjectTransitionEventService.build_idempotency_key(
            source_event_id="evt-1",
            domain="RESOURCE",
            object_type="BIN",
            object_key=" ",
            projection_type="PLACEMENT",
            to_state="ACTIVE",
            reason_code="ARRIVED",
        )
