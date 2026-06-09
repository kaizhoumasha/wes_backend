from src.workline_runtime.resource_wait_evidence import ResourceWaitEvidence


def test_resource_wait_evidence_merges_first_seen_last_seen_and_wait_count() -> None:
    evidence = ResourceWaitEvidence.build(
        inbox_id=11,
        resource_kind="STATION",
        resource_key="station:TARGET_STATION",
        reason_code="STATION_BUSY",
        message="目标 Station 忙",
        occurred_at="2026-01-01T00:00:10",
        existing={
            "first_seen_at": "2026-01-01T00:00:00",
            "last_seen_at": "2026-01-01T00:00:00",
            "wait_count": 2,
        },
    )

    assert evidence.diagnostic_key == "RESOURCE_WAIT:11:station:TARGET_STATION"
    assert evidence.first_seen_at == "2026-01-01T00:00:00"
    assert evidence.last_seen_at == "2026-01-01T00:00:10"
    assert evidence.wait_count == 3


def test_resource_wait_evidence_is_single_source_for_context_and_diagnostic_payload() -> None:
    evidence = ResourceWaitEvidence.build(
        inbox_id=11,
        resource_kind="STATION",
        resource_key="station:TARGET_STATION",
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

    assert session_context["resource_key"] == diagnostic_payload["resource_key"]
    assert session_context["wait_count"] == diagnostic_payload["wait_count"]
    assert "details" not in session_context
    assert diagnostic_payload["details"] == {"active_session_id": 44}
