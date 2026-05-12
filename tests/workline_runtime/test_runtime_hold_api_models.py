"""Runtime Hold API schema tests."""

import pytest
from pydantic import ValidationError

from src.app.workline.models.runtime_hold_api import ResolveRuntimeHoldRequest


def _valid_return_to_ng_payload() -> dict[str, object]:
    return {
        "resolution": "FAILED",
        "checks": {"physical_state_confirmed": True},
        "operator_note": "现场确认物料进入 NG 暂存",
        "material_disposition": "RETURN_TO_NG",
        "ng_reason": {
            "source": "RUNTIME",
            "code": "UNKNOWN_PHYSICAL_STATE",
            "label": "设备动作状态未知",
        },
        "physical_handoff_evidence": {
            "ng_location_code": "NG_PLATFORM_01",
            "ng_location_scan": "NG_PLATFORM_01",
            "material_scan_payload": {"PkgID": "PKG-001"},
            "line_clear_checked": True,
            "late_callback_reviewed": True,
        },
        "hold_version": 3,
        "latest_evidence_hash": "sha256:abc",
    }


def test_resolve_runtime_hold_request_accepts_client_owned_return_to_ng_evidence() -> None:
    request = ResolveRuntimeHoldRequest.model_validate(_valid_return_to_ng_payload())

    assert request.material_disposition == "RETURN_TO_NG"
    assert request.physical_handoff_evidence is not None
    assert request.physical_handoff_evidence.material_scan_payload == {"PkgID": "PKG-001"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("handoff_confirmed_by", 1),
        ("handoff_confirmed_at", "2026-05-09T10:00:00Z"),
        ("material_identity", {"idempotency_key": "smt:PKG-001"}),
    ],
)
def test_resolve_runtime_hold_request_rejects_server_owned_handoff_fields(field: str, value: object) -> None:
    payload = _valid_return_to_ng_payload()
    evidence = dict(payload["physical_handoff_evidence"])  # type: ignore[arg-type]
    evidence[field] = value
    payload["physical_handoff_evidence"] = evidence

    with pytest.raises(ValidationError):
        ResolveRuntimeHoldRequest.model_validate(payload)
