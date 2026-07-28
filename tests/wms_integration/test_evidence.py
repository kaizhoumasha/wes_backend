import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from src.app.wms_integration.models import WmsCallEvidence, WmsCallEvidenceArchive, WmsEvidenceStatus
from src.app.wms_integration.repositories import wms_call_evidence_archive_repository
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
        provider_profile_identity="wms.test.production",
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
        provider_profile_identity="wms.test.production",
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
        provider_profile_identity="wms.test.production",
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
        target_code="WMS_INVENTORY_TRANSFER",
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
        target_code="WMS_INVENTORY_TRANSFER",
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
        provider_profile_identity="wms.test.production",
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
        "ix_wms_call_evidence_provider_operation_started",
        "ix_wms_call_evidence_operation_started",
        "ix_wms_call_evidence_status_started",
        "ix_wms_call_evidence_request_snapshot_gin",
        "ix_wms_call_evidence_response_snapshot_gin",
    }
    assert indexes["ux_wms_call_evidence_key"] == ("evidence_key",)
    assert indexes["ix_wms_call_evidence_trace_request_dispatch"] == (
        "trace_id",
        "request_id",
        "dispatch_key",
    )
    assert indexes["ix_wms_call_evidence_provider_operation_started"] == (
        "provider_profile_identity",
        "operation_name",
        "started_at",
    )
    assert indexes["ix_wms_call_evidence_operation_started"] == ("operation_name", "started_at")
    assert indexes["ix_wms_call_evidence_status_started"] == ("status", "started_at")
    assert indexes["ix_wms_call_evidence_request_snapshot_gin"] == ("request_snapshot",)
    assert indexes["ix_wms_call_evidence_response_snapshot_gin"] == ("response_snapshot",)

    assert table.c.provider_profile_identity.type.length == 240
    assert WmsCallEvidenceArchive.__table__.c.provider_profile_identity.type.length == 240
    assert isinstance(table.c.request_snapshot.type, JSONB)
    assert isinstance(table.c.response_snapshot.type, JSONB)

    gin_indexes = {
        index.name: index.dialect_options["postgresql"].get("using")
        for index in table.indexes
        if index.name in {"ix_wms_call_evidence_request_snapshot_gin", "ix_wms_call_evidence_response_snapshot_gin"}
    }
    assert gin_indexes == {
        "ix_wms_call_evidence_request_snapshot_gin": "gin",
        "ix_wms_call_evidence_response_snapshot_gin": "gin",
    }


@pytest.mark.asyncio
async def test_wms_external_reference_drift_job_classifies_evidence_envelopes(db_session) -> None:
    from src.app.wms_integration.evidence import (
        EvidenceEnvelope,
        ExternalReference,
        ExternalReferenceCatalog,
        ExternalReferenceCatalogEntry,
        ExternalReferenceDriftKind,
    )

    envelope = EvidenceEnvelope(
        schema_version="evidence.v1",
        source_system="WMS",
        source_event_id="EVT-DRIFT-001",
        source_version="wms-42",
        evidence_type="PKG_BOUND",
        occurred_at="2026-07-01T02:00:00Z",
        external_refs=[
            ExternalReference(
                system="WMS",
                object_type="PKG",
                code="PKG-OK",
                schema_version="wms.pkg.v1",
                validated_at="2026-07-01T02:00:00Z",
                source_version="wms-42",
            ),
            ExternalReference(
                system="WMS",
                object_type="PKG",
                code="PKG-DRIFT",
                schema_version="wms.pkg.v1",
                validated_at="2026-07-01T02:00:00Z",
                source_version="wms-41",
            ),
        ],
        request_hash="b" * 64,
        payload_hash="a" * 64,
        payload={"pkg_code": "PKG-DRIFT"},
    )
    await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:pkg-bound:REQ-DRIFT-001",
        provider_profile_identity="wms.test.production",
        operation_name="pkg_bound",
        target_code="WMS",
        status=WmsEvidenceStatus.SUCCEEDED,
        request_snapshot=envelope.model_dump(),
        response_snapshot={},
        request_id="REQ-DRIFT-001",
        trace_id="TRACE-DRIFT-001",
        http_status=200,
    )

    report = await wms_call_evidence_service.run_external_reference_drift_job(
        db_session,
        catalog=ExternalReferenceCatalog(
            [
                ExternalReferenceCatalogEntry(
                    system="WMS",
                    object_type="PKG",
                    schema_version="wms.pkg.v1",
                    source_version="wms-42",
                )
            ]
        ),
    )

    assert report.scanned_evidence_count == 1
    assert report.drift_count == 1
    assert report.drift_items[0].evidence_key == "sync:pkg-bound:REQ-DRIFT-001"
    assert report.drift_items[0].snapshot_field == "request_snapshot"
    assert report.drift_items[0].kind == ExternalReferenceDriftKind.SOURCE_VERSION_MISMATCH
    assert report.drift_items[0].reference.code == "PKG-DRIFT"
    assert report.drift_items[0].expected_source_version == "wms-42"


@pytest.mark.asyncio
async def test_wms_evidence_retention_archives_expired_finished_rows(db_session) -> None:
    now = datetime(2026, 7, 2, 12, 0, 0)
    expired_started_at = now - timedelta(days=181)
    fresh_started_at = now - timedelta(days=7)

    expired = await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:retention:expired",
        provider_profile_identity="wms.test.production",
        operation_name="query_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.SUCCEEDED,
        request_snapshot={"sku": "EXPIRED"},
        response_snapshot={"qty": 1},
        started_at=expired_started_at,
        finished_at=expired_started_at + timedelta(seconds=1),
    )
    fresh = await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:retention:fresh",
        provider_profile_identity="wms.test.production",
        operation_name="query_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.SUCCEEDED,
        request_snapshot={"sku": "FRESH"},
        response_snapshot={"qty": 2},
        started_at=fresh_started_at,
        finished_at=fresh_started_at + timedelta(seconds=1),
    )
    in_flight = await wms_call_evidence_service.record_sync_call(
        db_session,
        evidence_key="sync:retention:in-flight",
        provider_profile_identity="wms.test.production",
        operation_name="query_inventory",
        target_code="WMS_INVENTORY",
        status=WmsEvidenceStatus.STARTED,
        request_snapshot={"sku": "IN-FLIGHT"},
        response_snapshot=None,
        started_at=expired_started_at,
        finished_at=None,
    )

    report = await wms_call_evidence_service.archive_expired_evidence(
        db_session,
        now=now,
        retention_days=180,
        limit=10,
    )

    assert report.scanned_count == 1
    assert report.archived_count == 1
    assert report.deleted_count == 1
    assert report.cutoff_at == now - timedelta(days=180)

    assert await wms_call_evidence_service.repo.get_by_evidence_key(db_session, expired.evidence_key) is None
    assert await wms_call_evidence_service.repo.get_by_evidence_key(db_session, fresh.evidence_key) is not None
    assert await wms_call_evidence_service.repo.get_by_evidence_key(db_session, in_flight.evidence_key) is not None

    archived = await wms_call_evidence_archive_repository.get_by_evidence_key(db_session, expired.evidence_key)
    assert archived is not None
    assert archived.original_evidence_id == expired.id
    assert archived.evidence_key == expired.evidence_key
    assert archived.request_hash == expired.request_hash
    assert archived.response_hash == expired.response_hash
    assert archived.request_snapshot == expired.request_snapshot
    assert archived.response_snapshot == expired.response_snapshot
    assert archived.archived_at == now
