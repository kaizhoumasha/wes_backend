import pytest

from src.workline_runtime.runtime_intent import (
    BlockScope,
    Destination,
    RuntimeIntent,
    RuntimeIntentKind,
)


def test_update_context_intent_carries_patch() -> None:
    intent = RuntimeIntent.update_context({"pkg_id": "L0001-1"})

    assert intent.kind == RuntimeIntentKind.UPDATE_CONTEXT
    assert intent.context_patch == {"pkg_id": "L0001-1"}


def test_complete_intent_carries_optional_context_patch() -> None:
    intent = RuntimeIntent.complete({"bin_code": "BIN_463"})

    assert intent.kind == RuntimeIntentKind.COMPLETE
    assert intent.context_patch == {"bin_code": "BIN_463"}


def test_mark_ng_intent_records_business_fact_without_failure() -> None:
    intent = RuntimeIntent.mark_ng(
        reason_code="SCAN_NG",
        message="扫码判定 NG",
        payload={"PkgID": "BAD"},
    )

    assert intent.kind == RuntimeIntentKind.MARK_NG
    assert intent.reason_code == "SCAN_NG"
    assert intent.message == "扫码判定 NG"
    assert intent.payload_json == {"PkgID": "BAD"}


def test_continue_next_uses_topology_destination() -> None:
    intent = RuntimeIntent.continue_next(action="MOVE_FORWARD", payload={"pkg_id": "L0001-1"})

    assert intent.kind == RuntimeIntentKind.CONTINUE_NEXT
    assert intent.destination == Destination.next()
    assert intent.action == "MOVE_FORWARD"


def test_external_request_intent_carries_http_dispatch_contract() -> None:
    intent = RuntimeIntent.external_request(
        dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
        target_code="WMS_RCS_FULL_BOX_EXCHANGE",
        payload={"rack_release_id": "release-001"},
        timeout_seconds=1800,
        source_system="WMS_RCS",
    )

    assert intent.kind == RuntimeIntentKind.EXTERNAL_REQUEST
    assert intent.dispatch_key == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert intent.target_code == "WMS_RCS_FULL_BOX_EXCHANGE"
    assert intent.payload_json == {"rack_release_id": "release-001"}
    assert intent.timeout_seconds == 1800
    assert intent.source_system == "WMS_RCS"


def test_external_request_requires_dispatch_key_target_payload_and_timeout() -> None:
    with pytest.raises(ValueError, match="EXTERNAL_REQUEST intent requires dispatch_key"):
        RuntimeIntent.external_request(
            dispatch_key="",
            target_code="WMS_RCS_FULL_BOX_EXCHANGE",
            payload={"rack_release_id": "release-001"},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="EXTERNAL_REQUEST intent requires target_code"):
        RuntimeIntent.external_request(
            dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
            target_code="",
            payload={"rack_release_id": "release-001"},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="EXTERNAL_REQUEST intent requires payload"):
        RuntimeIntent.external_request(
            dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
            target_code="WMS_RCS_FULL_BOX_EXCHANGE",
            payload={},
            timeout_seconds=1800,
        )

    with pytest.raises(ValueError, match="EXTERNAL_REQUEST intent requires timeout_seconds"):
        RuntimeIntent.external_request(
            dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
            target_code="WMS_RCS_FULL_BOX_EXCHANGE",
            payload={"rack_release_id": "release-001"},
            timeout_seconds=None,
        )


def test_block_still_requires_scope_reason_and_message() -> None:
    intent = RuntimeIntent.block(
        scope=BlockScope.MATERIAL,
        reason_code="PAYLOAD_INVALID",
        message="缺少 PkgID",
    )

    assert intent.kind == RuntimeIntentKind.BLOCK


def test_mark_ng_requires_reason_code() -> None:
    with pytest.raises(ValueError, match="MARK_NG intent requires reason_code"):
        RuntimeIntent.mark_ng(reason_code="", message="扫码判定 NG")


def test_mark_ng_requires_message() -> None:
    with pytest.raises(ValueError, match="MARK_NG intent requires message"):
        RuntimeIntent.mark_ng(reason_code="SCAN_NG", message="")


def test_builder_payloads_and_patches_are_stable_snapshots() -> None:
    command_payload = {"meta": {"pkg_id": "L0001-1"}}
    update_patch = {"meta": {"pkg_id": "L0001-1"}}
    complete_patch = {"meta": {"bin_code": "BIN_463"}}
    mark_ng_payload = {"meta": {"PkgID": "BAD"}}
    continue_payload = {"meta": {"pkg_id": "L0001-1"}}

    command_intent = RuntimeIntent.command(action="SCAN", payload=command_payload)
    update_intent = RuntimeIntent.update_context(update_patch)
    complete_intent = RuntimeIntent.complete(complete_patch)
    mark_ng_intent = RuntimeIntent.mark_ng(
        reason_code="SCAN_NG",
        message="扫码判定 NG",
        payload=mark_ng_payload,
    )
    continue_intent = RuntimeIntent.continue_next(action="MOVE_FORWARD", payload=continue_payload)

    command_payload["meta"]["pkg_id"] = "MUTATED"
    update_patch["meta"]["pkg_id"] = "MUTATED"
    complete_patch["meta"]["bin_code"] = "MUTATED"
    mark_ng_payload["meta"]["PkgID"] = "MUTATED"
    continue_payload["meta"]["pkg_id"] = "MUTATED"

    assert command_intent.payload_json == {"meta": {"pkg_id": "L0001-1"}}
    assert update_intent.context_patch == {"meta": {"pkg_id": "L0001-1"}}
    assert complete_intent.context_patch == {"meta": {"bin_code": "BIN_463"}}
    assert mark_ng_intent.payload_json == {"meta": {"PkgID": "BAD"}}
    assert continue_intent.payload_json == {"meta": {"pkg_id": "L0001-1"}}
