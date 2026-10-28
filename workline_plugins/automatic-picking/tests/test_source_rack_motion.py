"""普通来源换面只接收显式完整输入，不补设备默认值。"""

from dataclasses import FrozenInstanceError
from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.source_rack_motion import MatchedSourceRackRotation, SourceRackMotionFlow
from wes_plugin_sdk import TransportRackPosition


@pytest.mark.parametrize(
    "field,value",
    [
        ("motion_id", True),
        ("motion_id", 0),
        ("next_member_id", -1),
        ("next_member_id", 2**63),
        ("rack_id", " "),
        ("target_face", ""),
        ("position", {"location_code": "P"}),
    ],
)
def test_rejects_incomplete_or_untyped_rotation(field, value):
    values = {
        "motion_id": 1,
        "next_member_id": 2,
        "rack_id": "R",
        "position": TransportRackPosition("P"),
        "target_face": "B",
    }
    values[field] = value
    with pytest.raises(ValueError):
        MatchedSourceRackRotation(**values)


def test_rotation_input_is_immutable():
    value = MatchedSourceRackRotation(1, 2, "R", TransportRackPosition("P"), "B")
    with pytest.raises(FrozenInstanceError):
        value.target_face = "C"


@pytest.mark.asyncio
async def test_rotation_handoff_reuses_open_face_batch_without_changing_original_member():
    now = datetime(2026, 10, 16)
    current = N(
        inbound_client_request_id="ingress",
        round_started_at=now,
        round_causal_token=1,
        rotation_result_evidence_id=41,
        completed_source_member_ids=[1],
        archived_at=None,
    )
    original = N(
        inbound_client_request_id="ingress",
        round_started_at=now,
        round_causal_token=1,
        source_arrival_evidence_id=41,
        completed_source_member_ids=[],
        source_member_id=2,
        operation_id="original",
    )
    member = N(id=3)
    feeds = N(
        by_member=AsyncMock(return_value=None),
        open_for_face=AsyncMock(return_value=original),
        add_feed=AsyncMock(),
    )
    repository = N(
        transport=AsyncMock(
            return_value=(
                N(
                    outcome_version=1,
                    published_outcome_version=1,
                    status="SUCCEEDED",
                    request_json={"target_face": "B"},
                ),
                N(),
            )
        ),
        service_members=AsyncMock(return_value=(N(status="EXECUTING"), [member])),
    )
    returns = N(
        feeds=feeds,
        repository=N(
            evidence=AsyncMock(return_value=N(id=41, published_at=now, normalized_payload={"outcome_version": 1}))
        ),
        motions=N(current_source=AsyncMock(return_value=True)),
    )
    scheduler = N(create_in_session=AsyncMock())
    flow = SourceRackMotionFlow(returns, None, scheduler, None, repository=repository, clock=lambda: now)
    flow.feeds = feeds
    assert await flow._handoff(N(flush=AsyncMock()), N(id=7, is_active=True), current) == 0
    assert (original.source_member_id, original.operation_id) == (2, "original")
    assert original.completed_source_member_ids == [1]
    scheduler.create_in_session.assert_not_awaited()
    feeds.add_feed.assert_not_awaited()
