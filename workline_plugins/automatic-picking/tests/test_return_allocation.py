"""离线位置/架面读口的严格身份边界。"""

from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.return_allocation_flow import (
    AutomaticPickingReturnBatchFlow,
    MatchedReturnFace,
    MatchedReturnLocation,
)

from src.app.execution.services.inbound_evidence_service import InboundEvidenceDigestPolicy, normalize_payload


@pytest.mark.parametrize(
    "bad",
    [
        {"arrival_evidence_id": True},
        {"workline_id": 0},
        {"arrival_evidence_id": 2**63},
        {"rack_id": 123},
        {"rack_face": 123},
        {"rack_id": " "},
        {"rack_face": ""},
    ],
)
def test_face_requires_explicit_typed_identity(bad):
    with pytest.raises(ValueError):
        MatchedReturnFace(**{"arrival_evidence_id": 1, "workline_id": 2, "rack_id": "R1", "rack_face": "A", **bad})


@pytest.mark.parametrize(
    "bad",
    [
        {"return_id": False},
        {"workline_id": 0},
        {"bin_code": ""},
        {"location_code": " "},
    ],
)
def test_location_cannot_guess_identity_or_handoff_code(bad):
    with pytest.raises(ValueError):
        MatchedReturnLocation(
            **{"return_id": 1, "workline_id": 2, "bin_code": "BIN-1", "location_code": "OUTLET-1", **bad}
        )


@pytest.mark.parametrize("kind", ["DEVICE_EVENT", "TRANSPORT_RESULT"])
@pytest.mark.parametrize("corrupt", [False, True])
async def test_face_digest_follows_ingress_policy_and_rejects_changed_facts(kind, corrupt):
    face = MatchedReturnFace(1, 2, "R1", "A")
    payload, digest = normalize_payload(
        {"trace_id": "ingress-trace", "data": {"rack_id": "R1"}},
        digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE
        if kind == "DEVICE_EVENT"
        else InboundEvidenceDigestPolicy.EXACT,
    )
    evidence = N(
        workline_id=2,
        apply_status="APPLIED",
        kind=kind,
        published_at=1,
        normalized_payload=payload,
        payload_digest=digest,
    )
    if corrupt:
        payload["data"]["rack_id"] = "OTHER"
    flow = AutomaticPickingReturnBatchFlow(
        N(evidence=AsyncMock(return_value=evidence)),
        None,
        None,
        N(read_in_session=AsyncMock(return_value=(face,))),
        None,
    )
    assert await flow._faces(None, N(id=2)) == (((), False) if corrupt else ((face,), True))
