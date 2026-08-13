"""设备回调 evidence 的部署级身份与规范化摘要。"""

from __future__ import annotations

from src.app.device.services.device_evidence_service import normalized_evidence_digest


def test_trace_id_does_not_change_evidence_digest() -> None:
    first = {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "event_type": "ARRIVED",
        "timestamp": 1_786_032_000_000,
        "source_event_id": "EVENT-001",
        "data": {"location": "STATION-A"},
        "trace_id": "TRACE-1",
    }
    second = {**first, "trace_id": "TRACE-2"}

    assert normalized_evidence_digest(first) == normalized_evidence_digest(second)


def test_object_order_does_not_change_evidence_digest_but_array_order_does() -> None:
    first = {
        "source_event_id": "EVENT-001",
        "device_code": "ARM-01",
        "data": {"items": ["A", "B"], "location": "STATION-A"},
    }
    reordered_object = {
        "data": {"location": "STATION-A", "items": ["A", "B"]},
        "device_code": "ARM-01",
        "source_event_id": "EVENT-001",
    }
    reordered_array = {
        "source_event_id": "EVENT-001",
        "device_code": "ARM-01",
        "data": {"items": ["B", "A"], "location": "STATION-A"},
    }

    assert normalized_evidence_digest(first) == normalized_evidence_digest(reordered_object)
    assert normalized_evidence_digest(first) != normalized_evidence_digest(reordered_array)


def test_omitted_field_and_explicit_null_have_different_digests() -> None:
    omitted = {"source_event_id": "EVENT-001", "device_code": "ARM-01", "data": {}}
    explicit_null = {**omitted, "error_detail": None}

    assert normalized_evidence_digest(omitted) != normalized_evidence_digest(explicit_null)
