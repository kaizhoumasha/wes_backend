from src.workline_runtime.resource_wait_evidence import ResourceWaitEvidence


def test_resource_wait_evidence_merges_first_seen_last_seen_and_wait_count() -> None:
    evidence = ResourceWaitEvidence.build(
        inbox_id=11,
        subject_type="TARGET_STATION",
        subject_key="station:TARGET_STATION",
        projection_type="ACTIVE_TARGET_BIN_RACK",
        reason_code="STATION_BUSY",
        message="目标 Station 忙",
        occurred_at="2026-01-01T00:00:10",
        existing={
            "first_seen_at": "2026-01-01T00:00:00",
            "last_seen_at": "2026-01-01T00:00:00",
            "wait_count": 2,
        },
    )

    assert evidence.diagnostic_key == "RESOURCE_WAIT:11:TARGET_STATION:ACTIVE_TARGET_BIN_RACK:station:TARGET_STATION"
    assert evidence.first_seen_at == "2026-01-01T00:00:00"
    assert evidence.last_seen_at == "2026-01-01T00:00:10"
    assert evidence.wait_count == 3


def test_resource_wait_evidence_is_single_source_for_context_and_diagnostic_payload() -> None:
    evidence = ResourceWaitEvidence.build(
        inbox_id=11,
        subject_type="TARGET_STATION",
        subject_key="station:TARGET_STATION",
        projection_type="ACTIVE_TARGET_BIN_RACK",
        reason_code="STATION_BUSY",
        message="目标 Station 忙",
        occurred_at="2026-01-01T00:00:10",
        session_id=22,
        workline_id=33,
        trace_id="trace-resource-wait",
        details={"active_session_id": 44},
    )

    session_context = evidence.to_session_context()
    diagnostic_payload = evidence.to_diagnostic_evidence()

    assert session_context["subject_key"] == diagnostic_payload["subject_key"]
    assert session_context["subject_type"] == diagnostic_payload["subject_type"]
    assert session_context["projection_type"] == diagnostic_payload["projection_type"]
    assert session_context["wait_count"] == diagnostic_payload["wait_count"]
    assert "details" not in session_context
    assert diagnostic_payload["details"] == {"active_session_id": 44}


def test_resource_wait_diagnostic_key_fits_model_column_length_for_large_ids() -> None:
    evidence = ResourceWaitEvidence.build(
        inbox_id=9_223_372_036_854_775_807,
        subject_type="TARGET_STATION",
        subject_key="station:" + "TARGET_STATION" * 100,
        projection_type="ACTIVE_TARGET_BIN_RACK",
        reason_code="STATION_BUSY",
        message="目标 Station 忙",
        occurred_at="2026-01-01T00:00:10",
    )

    assert len(evidence.diagnostic_key) <= 300
    assert evidence.diagnostic_key.startswith("RESOURCE_WAIT:9223372036854775807:TARGET_STATION:")
