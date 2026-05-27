import json

import pytest

from src.app.wms_integration.models import WmsCallEvidence, WmsEvidenceStatus
from src.app.wms_integration.services import (
    WmsCallEvidenceService,
    canonical_sha256,
    redact_sensitive,
    wms_call_evidence_service,
)
from src.app.wms_integration.services.redaction import MAX_SNAPSHOT_JSON_BYTES


def test_redaction_masks_nested_sensitive_fields() -> None:
    payload = {
        "token": "plain-token",
        "AuthHeader": {"Authorization": "Bearer abc", "x_api_key": "key-1"},
        "items": [
            {"sku": "A", "password": "secret-password"},
            {"cookie_value": "session=1", "normal": "visible"},
        ],
        "signature-v2": "signed",
    }

    redacted = redact_sensitive(payload)

    assert redacted["token"] == "***REDACTED***"
    assert redacted["AuthHeader"]["Authorization"] == "***REDACTED***"
    assert redacted["AuthHeader"]["x_api_key"] == "***REDACTED***"
    assert redacted["items"][0]["password"] == "***REDACTED***"
    assert redacted["items"][1]["cookie_value"] == "***REDACTED***"
    assert redacted["items"][1]["normal"] == "visible"
    assert redacted["signature-v2"] == "***REDACTED***"


def test_canonical_hash_is_stable_for_semantically_equal_dicts() -> None:
    left = {"b": [3, {"z": "last", "a": "first"}], "a": 1}
    right = {"a": 1, "b": [3, {"a": "first", "z": "last"}]}

    assert canonical_sha256(left) == canonical_sha256(right)


@pytest.mark.asyncio
async def test_evidence_records_sync_redacted_snapshot_and_hash(db_session) -> None:
    service = WmsCallEvidenceService()

    evidence = await service.record_sync_call(
        db_session,
        evidence_key="sync:reserve:REQ-001",
        operation_name="reserve_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.SUCCEEDED,
        request_snapshot={"sku": "A", "api_key": "key-1"},
        response_snapshot={"accepted": True, "token": "response-token"},
        request_id="REQ-001",
        trace_id="TRACE-001",
        http_status=200,
    )

    assert evidence.id is not None
    assert evidence.evidence_key == "sync:reserve:REQ-001"
    assert evidence.request_snapshot["api_key"] == "***REDACTED***"
    assert evidence.response_snapshot["token"] == "***REDACTED***"
    assert evidence.request_hash == canonical_sha256({"sku": "A", "api_key": "***REDACTED***"})
    assert evidence.response_hash == canonical_sha256({"accepted": True, "token": "***REDACTED***"})
    assert evidence.status == WmsEvidenceStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_evidence_reuses_existing_record_for_same_evidence_key(db_session) -> None:
    first = await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:query:REQ-002",
        operation_name="query_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.SUCCEEDED,
        request_snapshot={"sku": "A"},
        response_snapshot={"qty": 10},
        request_id="REQ-002",
        trace_id="TRACE-002",
    )
    second = await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:query:REQ-002",
        operation_name="query_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.FAILED,
        request_snapshot={"sku": "B"},
        response_snapshot={"error": "duplicate"},
        request_id="REQ-002",
        trace_id="TRACE-002",
    )

    assert second.id == first.id
    assert second.request_snapshot == first.request_snapshot


@pytest.mark.asyncio
async def test_async_evidence_keeps_associations_and_redacted_summary_without_full_payload(db_session) -> None:
    full_payload = {
        "dispatch_key": "DISPATCH-001",
        "request_id": "REQ-003",
        "trace_id": "TRACE-003",
        "inventory_lines": [{"sku": "A", "qty": 10}, {"sku": "B", "qty": 20}],
        "authorization": "Bearer should-not-copy",
    }

    evidence = await wms_call_evidence_service.record_async_summary(
        db_session,
        evidence_key="async:dispatch:DISPATCH-001",
        operation_name="dispatch_transport",
        target_code="WMS_RCS_BIN_OPERATION",
        status=WmsEvidenceStatus.ASYNC_RECORDED,
        dispatch_key="DISPATCH-001",
        request_id="REQ-003",
        trace_id="TRACE-003",
        source_ref_type="SYSTEM_OUTBOX",
        source_ref_id="101",
        summary=full_payload,
    )

    assert evidence.dispatch_key == "DISPATCH-001"
    assert evidence.request_id == "REQ-003"
    assert evidence.trace_id == "TRACE-003"
    assert evidence.source_ref_type == "SYSTEM_OUTBOX"
    assert evidence.source_ref_id == "101"
    assert evidence.request_snapshot["payload_keys"] == sorted(full_payload)
    assert evidence.request_snapshot["payload_key_count"] == len(full_payload)
    assert evidence.request_snapshot["payload_kind"] == "dict"
    assert len(evidence.request_snapshot["payload_hash"]) == 64
    assert "authorization" not in evidence.request_snapshot
    assert "dispatch_key" not in evidence.request_snapshot
    assert "inventory_lines" not in evidence.request_snapshot
    assert evidence.response_snapshot == {}


@pytest.mark.asyncio
async def test_async_evidence_does_not_copy_all_scalar_payload_fields(db_session) -> None:
    scalar_payload = {
        "dispatch_key": "DISPATCH-004",
        "request_id": "REQ-004",
        "trace_id": "TRACE-004",
        "sku": "SKU-A",
        "qty": 10,
        "status": "FAILED",
        "reason_code": "WMS_REJECTED",
        "error_code": "E" * 300,
    }

    evidence = await wms_call_evidence_service.record_async_summary(
        db_session,
        evidence_key="async:callback:DISPATCH-004",
        operation_name="callback_transport",
        target_code="WMS_RCS_BIN_OPERATION",
        status=WmsEvidenceStatus.ASYNC_RECORDED,
        dispatch_key="DISPATCH-004",
        request_id="REQ-004",
        trace_id="TRACE-004",
        source_ref_type="CALLBACK_LOG",
        source_ref_id="202",
        summary=scalar_payload,
    )

    snapshot = evidence.request_snapshot
    assert snapshot["payload_keys"] == sorted(scalar_payload)
    assert snapshot["payload_key_count"] == len(scalar_payload)
    assert snapshot["status"] == "FAILED"
    assert snapshot["reason_code"] == "WMS_REJECTED"
    assert snapshot["error_code"].startswith("E" * 240)
    assert "truncated" in snapshot["error_code"]
    assert "sku" not in snapshot
    assert "qty" not in snapshot
    assert "dispatch_key" not in snapshot
    assert evidence.request_hash == canonical_sha256(snapshot)


@pytest.mark.asyncio
async def test_sync_evidence_bounds_snapshot_size_and_hashes_persisted_snapshot(db_session) -> None:
    oversized_text = "x" * (MAX_SNAPSHOT_JSON_BYTES * 2)

    evidence = await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:reserve:REQ-005",
        operation_name="reserve_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.FAILED,
        request_snapshot={"sku": "A", "notes": oversized_text, "token": "plain-token"},
        response_snapshot={"error": oversized_text},
        request_id="REQ-005",
        trace_id="TRACE-005",
    )

    assert evidence.request_snapshot["sku"] == "A"
    assert evidence.request_snapshot["token"] == "***REDACTED***"
    assert "truncated" in evidence.request_snapshot["notes"]
    assert len(json.dumps(evidence.request_snapshot, ensure_ascii=False).encode("utf-8")) <= MAX_SNAPSHOT_JSON_BYTES
    assert evidence.request_hash == canonical_sha256(evidence.request_snapshot)
    assert "truncated" in evidence.response_snapshot["error"]
    assert len(json.dumps(evidence.response_snapshot, ensure_ascii=False).encode("utf-8")) <= MAX_SNAPSHOT_JSON_BYTES
    assert evidence.response_hash == canonical_sha256(evidence.response_snapshot)


def test_evidence_model_declares_required_indexes() -> None:
    table = WmsCallEvidence.__table__
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.name != "ix_wes_biz_wms_call_evidence_id"
    }

    assert set(indexes) == {
        "ux_wms_call_evidence_key",
        "ix_wms_call_evidence_trace_request_dispatch",
        "ix_wms_call_evidence_operation_started",
        "ix_wms_call_evidence_status_started",
    }
    assert indexes["ux_wms_call_evidence_key"] == ("evidence_key",)
    assert indexes["ix_wms_call_evidence_trace_request_dispatch"] == (
        "trace_id",
        "request_id",
        "dispatch_key",
    )
    assert indexes["ix_wms_call_evidence_operation_started"] == ("operation_name", "started_at")
    assert indexes["ix_wms_call_evidence_status_started"] == ("status", "started_at")
