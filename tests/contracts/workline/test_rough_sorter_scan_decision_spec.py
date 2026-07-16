"""粗分机扫码到准入决策窄闭环规格包测试。"""

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json"
SPEC_PATH = REPOSITORY_ROOT / "docs/business/rough_sorter_scan_decision_contract.md"

EXPECTED_CASE_OVERVIEW = {
    # trigger(event, discriminator), outcome, state(material, command, session, context_phase),
    # intents(kind, action, scope), implementation status, reason code
    "RS-SD-001": (
        ("SCAN_COMPLETED", (("barcode_decision", "OK"), ("pkg_id_condition", "PRESENT"))),
        "PICK_AND_PUT_PERSISTED",
        ("IN_TRANSIT", None, "WAITING_COMMAND_RESULT", "PICK_TO_PIPELINE"),
        (
            ("CREATE_MATERIAL_UNIT", None, None),
            ("UPDATE_CONTEXT", None, None),
            ("COMMAND", "PICK_AND_PUT", None),
        ),
        "covered",
        None,
    ),
    "RS-SD-002": (
        ("SCAN_COMPLETED", (("barcode_decision", "RULE_NG"), ("pkg_ng_rule", "SIZENG"))),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_COMMAND_RESULT", "NG_MOVING"),
        (
            ("CREATE_MATERIAL_UNIT", None, None),
            ("UPDATE_CONTEXT", None, None),
            ("MARK_NG", None, None),
            ("COMMAND", "MOVE_TO_NG", None),
        ),
        "covered",
        "SCAN_NG_BY_RULE",
    ),
    "RS-SD-003": (
        ("SCAN_COMPLETED", (("barcode_decision", "INCOMPLETE"), ("pkg_id_condition", "MISSING"))),
        "HOLD",
        ("NOT_CREATED", "NOT_CREATED", "MANUAL_HOLD", "UNCHANGED"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "ROUGH_SORTER_CONTEXT_MISSING",
    ),
    "RS-SD-004": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "ADMIT")),
        ),
        "MOVE_FORWARD_PERSISTED",
        ("IN_TRANSIT", None, "WAITING_COMMAND_RESULT", "MOVING_FORWARD"),
        (("UPDATE_CONTEXT", None, None), ("COMMAND", "MOVE_FORWARD", None)),
        "gap",
        None,
    ),
    "RS-SD-005": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "NG"), ("wms_admission", "NOT_QUERIED")),
        ),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_COMMAND_RESULT", "NG_MOVING"),
        (("UPDATE_CONTEXT", None, None), ("MARK_NG", None, None), ("COMMAND", "MOVE_TO_NG", None)),
        "gap",
        "MEASUREMENT_NG",
    ),
    "RS-SD-006": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "REJECT")),
        ),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_COMMAND_RESULT", "NG_MOVING"),
        (("UPDATE_CONTEXT", None, None), ("MARK_NG", None, None), ("COMMAND", "MOVE_TO_NG", None)),
        "gap",
        "WMS_REJECTED",
    ),
    "RS-SD-007": (
        (
            "COMMAND_RESULT",
            (
                ("command_result", "SUCCESS"),
                ("measurement_contract", "INVALID"),
                ("wms_admission", "NOT_QUERIED"),
            ),
        ),
        "HOLD",
        ("MANUAL_HOLD", "UNCHANGED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "ROUGH_SORTER_MEASUREMENT_INVALID",
    ),
    "RS-SD-008": (
        ("COMMAND_RESULT", (("command_result", "FAILURE"),)),
        "HOLD",
        ("IN_TRANSIT", "MANUAL_HOLD", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "COMMAND"),),
        "covered",
        "DEVICE_BUSY",
    ),
    "RS-SD-009": (
        ("TIMER_TIMEOUT", (("command_result", "TIMEOUT"),)),
        "HOLD",
        ("IN_TRANSIT", "MANUAL_HOLD", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "COMMAND"),),
        "partial",
        "ROUGH_SORTER_PICK_RESULT_TIMEOUT",
    ),
    "RS-SD-010": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "TIMEOUT")),
        ),
        "HOLD",
        ("MANUAL_HOLD", "UNCHANGED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "WMS_TIMEOUT",
    ),
    "RS-SD-011": (
        ("REPLAY_REQUEST", (("duplicate_digest", "SAME"),)),
        "REPLAY_ACCEPTED_NOOP",
        ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED"),
        (),
        "gap",
        None,
    ),
    "RS-SD-012": (
        ("REPLAY_REQUEST", (("duplicate_digest", "DIFFERENT"),)),
        "HOLD",
        ("MANUAL_HOLD", "UNCHANGED", "MANUAL_HOLD", "UNCHANGED"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "IDEMPOTENCY_CONFLICT",
    ),
    "RS-SD-013": (
        ("COMMAND_RESULT", (("correlation", "LATE_OR_UNKNOWN_MISMATCH"),)),
        "ARCHIVED_EVIDENCE",
        ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED"),
        (),
        "partial",
        "COMMAND_RESULT_CORRELATION_MISMATCH",
    ),
}
REQUIRED_CASE_FIELDS = {
    "case_id",
    "trigger",
    "preconditions",
    "recorded_evidence",
    "expected_state",
    "expected_intents",
    "expected_outcome",
    "replay_expectation",
    "replay_policy",
    "source_refs",
    "implementation_status",
}
ALLOWED_TRIGGER_TYPES = {"SCAN_COMPLETED", "COMMAND_RESULT", "TIMER_TIMEOUT", "REPLAY_REQUEST"}
ALLOWED_STATE_VALUES = {
    "material": {"IN_TRANSIT", "NG", "NOT_CREATED", "MANUAL_HOLD", "UNCHANGED"},
    "context_phase": {"PICK_TO_PIPELINE", "NG_MOVING", "MOVING_FORWARD", "UNCHANGED"},
    "command": {"NOT_CREATED", "UNCHANGED", "MANUAL_HOLD"},
    "session": {"WAITING_COMMAND_RESULT", "MANUAL_HOLD", "UNCHANGED"},
}
ALLOWED_INTENT_KINDS = {"CREATE_MATERIAL_UNIT", "UPDATE_CONTEXT", "MARK_NG", "COMMAND", "BLOCK"}
ALLOWED_COMMAND_ACTIONS = {"PICK_AND_PUT", "MOVE_FORWARD", "MOVE_TO_NG"}
ALLOWED_BLOCK_SCOPES = {"MATERIAL", "COMMAND"}
ALLOWED_OUTCOMES = {
    "PICK_AND_PUT_PERSISTED",
    "MOVE_FORWARD_PERSISTED",
    "MOVE_TO_NG_PERSISTED",
    "HOLD",
    "REPLAY_ACCEPTED_NOOP",
    "ARCHIVED_EVIDENCE",
}
QUERY_REPLAY_CASES = {"RS-SD-004", "RS-SD-006", "RS-SD-010", "RS-SD-011"}


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _normalize_trigger_signature(case: dict) -> tuple[str, tuple[tuple[str, str], ...]]:
    trigger = case["trigger"]
    return trigger["event_type"], tuple(sorted(trigger["decision_discriminator"].items()))


def test_fixture_has_fixed_case_semantic_signatures() -> None:
    fixture = _load_fixture()
    cases = fixture["cases"]
    cases_by_id = {case["case_id"]: case for case in cases}

    assert fixture["schema_version"] == "rough-sorter-scan-decision.v1"
    assert fixture["slice_id"] == "rough_sorter.scan_to_admission_decision"
    assert list(cases_by_id) == list(EXPECTED_CASE_OVERVIEW)
    assert len(cases_by_id) == len(cases)
    for case_id, expected_signature in EXPECTED_CASE_OVERVIEW.items():
        case = cases_by_id[case_id]
        state = case["expected_state"]
        actual_signature = (
            _normalize_trigger_signature(case),
            case["expected_outcome"]["result"],
            (state["material"], state.get("command"), state["session"], state["context_phase"]),
            tuple((intent["kind"], intent.get("action"), intent.get("scope")) for intent in case["expected_intents"]),
            case["implementation_status"],
            case["expected_outcome"]["reason_code"],
        )
        assert actual_signature == expected_signature, case_id


def test_case_fields_use_closed_non_empty_structures() -> None:
    for case in _load_fixture()["cases"]:
        case_id = case["case_id"]
        assert case.keys() >= REQUIRED_CASE_FIELDS, case_id
        assert case["trigger"].keys() >= {
            "event_type",
            "source_event_id",
            "payload",
            "decision_discriminator",
        }, case_id
        assert case["trigger"]["event_type"] in ALLOWED_TRIGGER_TYPES, case_id
        assert case["trigger"]["source_event_id"].strip(), case_id
        assert isinstance(case["trigger"]["payload"], dict) and case["trigger"]["payload"], case_id
        assert isinstance(case["trigger"]["decision_discriminator"], dict), case_id
        assert case["trigger"]["decision_discriminator"] and all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in case["trigger"]["decision_discriminator"].items()
        ), case_id
        assert isinstance(case["preconditions"], list) and all(case["preconditions"]), case_id
        assert set(case["recorded_evidence"]) == {"first_attempt", "replay"}, case_id
        assert all(
            isinstance(items, list) and items and all(isinstance(item, str) and item for item in items)
            for items in case["recorded_evidence"].values()
        ), case_id
        assert case["expected_state"].keys() >= {"material", "session"}, case_id
        assert all(
            key in ALLOWED_STATE_VALUES and value in ALLOWED_STATE_VALUES[key]
            for key, value in case["expected_state"].items()
        ), case_id
        assert isinstance(case["expected_intents"], list), case_id
        assert case["expected_outcome"].keys() >= {"result", "reason_code"}, case_id
        assert case["expected_outcome"]["result"] in ALLOWED_OUTCOMES, case_id
        assert isinstance(case["replay_expectation"], str) and case["replay_expectation"].strip(), case_id
        assert set(case["replay_policy"]) == {"query", "effect", "session_progress"}, case_id
        assert case["replay_policy"]["query"] in {"NOT_APPLICABLE", "REUSE_RECORDED"}, case_id
        assert case["replay_policy"]["effect"] == "NO_NEW_EFFECT", case_id
        assert case["replay_policy"]["session_progress"] == "NO_PROGRESS", case_id

    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}
    ng_pkg_id = cases["RS-SD-002"]["trigger"]["payload"]["data"]["PkgID"].upper()
    assert cases["RS-SD-002"]["trigger"]["decision_discriminator"]["pkg_ng_rule"] in ng_pkg_id
    assert "PkgID" not in cases["RS-SD-003"]["trigger"]["payload"]["data"]


def test_intents_outcomes_and_replay_policies_are_consistent() -> None:
    for case in _load_fixture()["cases"]:
        case_id = case["case_id"]
        intents = case["expected_intents"]
        outcome = case["expected_outcome"]["result"]
        intent_kinds = [intent["kind"] for intent in intents]

        assert set(intent_kinds) <= ALLOWED_INTENT_KINDS, case_id
        for intent in intents:
            if intent["kind"] == "COMMAND":
                assert intent.get("action") in ALLOWED_COMMAND_ACTIONS, case_id
            if intent["kind"] == "BLOCK":
                assert intent.get("scope") in ALLOWED_BLOCK_SCOPES, case_id

        if outcome.endswith("_PERSISTED"):
            assert intent_kinds.count("COMMAND") == 1 and "BLOCK" not in intent_kinds, case_id
        elif outcome == "HOLD":
            assert intent_kinds == ["BLOCK"], case_id
        else:
            assert outcome in {"REPLAY_ACCEPTED_NOOP", "ARCHIVED_EVIDENCE"} and not intents, case_id

        expected_query_policy = "REUSE_RECORDED" if case_id in QUERY_REPLAY_CASES else "NOT_APPLICABLE"
        assert case["replay_policy"]["query"] == expected_query_policy, case_id

    late_callback = _load_fixture()["cases"][-1]
    assert late_callback["case_id"] == "RS-SD-013"
    assert late_callback["expected_state"] == {
        "material": "UNCHANGED",
        "command": "UNCHANGED",
        "session": "UNCHANGED",
        "context_phase": "UNCHANGED",
    }
    assert late_callback["expected_intents"] == []
    assert late_callback["expected_outcome"] == {
        "result": "ARCHIVED_EVIDENCE",
        "reason_code": "COMMAND_RESULT_CORRELATION_MISMATCH",
    }


def test_case_categories_retain_required_evidence_ownership() -> None:
    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}

    for case_id in ("RS-SD-001", "RS-SD-002", "RS-SD-003"):
        evidence = cases[case_id]["recorded_evidence"]
        assert {"normalized_input_snapshot", "payload_digest"} <= set(evidence["first_attempt"]), case_id
        assert "payload_digest" in evidence["replay"], case_id

    for case_id in ("RS-SD-004", "RS-SD-005", "RS-SD-006", "RS-SD-007", "RS-SD-010"):
        assert "command_result_snapshot" in cases[case_id]["recorded_evidence"]["first_attempt"], case_id
    assert "measurement_snapshot" in cases["RS-SD-005"]["recorded_evidence"]["first_attempt"]
    assert "measurement_validation_errors" in cases["RS-SD-007"]["recorded_evidence"]["first_attempt"]
    for case_id in ("RS-SD-004", "RS-SD-006"):
        evidence = cases[case_id]["recorded_evidence"]
        assert "wms_response_summary" in evidence["first_attempt"], case_id
        assert "original_wms_response_summary" in evidence["replay"], case_id

    assert {"wms_timeout_summary", "payload_digest"} <= set(cases["RS-SD-010"]["recorded_evidence"]["first_attempt"])
    assert {"original_timeout_summary", "no_successful_wms_evidence"} <= set(
        cases["RS-SD-010"]["recorded_evidence"]["replay"]
    )
    assert {"incoming_payload_digest", "digest_mismatch", "conflict_audit"} <= set(
        cases["RS-SD-012"]["recorded_evidence"]["replay"]
    )
    assert {"command_result_snapshot", "device_error_summary", "hold_reason", "payload_digest"} <= set(
        cases["RS-SD-008"]["recorded_evidence"]["first_attempt"]
    )
    assert {"command_identity", "deadline_snapshot", "timeout_event", "hold_reason", "payload_digest"} <= set(
        cases["RS-SD-009"]["recorded_evidence"]["first_attempt"]
    )
    assert {"normalized_input_snapshot", "query_response_summary", "decision", "intent_identity"} <= set(
        cases["RS-SD-011"]["recorded_evidence"]["first_attempt"]
    )
    assert {"replay_request", "matched_payload_digest", "reused_decision", "reused_intent_identity"} <= set(
        cases["RS-SD-011"]["recorded_evidence"]["replay"]
    )
    assert {
        "callback_snapshot",
        "correlation_lookup",
        "current_wait_anchor",
        "mismatch_reason",
        "payload_digest",
    } <= set(cases["RS-SD-013"]["recorded_evidence"]["first_attempt"])
    assert {"original_mismatch_evidence", "payload_digest"} <= set(cases["RS-SD-013"]["recorded_evidence"]["replay"])


def test_source_refs_resolve_to_repository_files() -> None:
    for case in _load_fixture()["cases"]:
        assert case["source_refs"] and all(
            isinstance(source_ref, str) and source_ref.strip() and (REPOSITORY_ROOT / source_ref).is_file()
            for source_ref in case["source_refs"]
        ), case["case_id"]


def test_business_spec_has_strict_metadata_and_stable_sections() -> None:
    content = SPEC_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    metadata = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith(">"):
            break
        key, separator, value = line.partition(":")
        assert separator, line
        metadata[key.strip()] = value.strip()

    assert lines[0] == "# 粗分机扫码到准入决策窄闭环合同"
    assert metadata == {
        "contract_version": "rough-sorter-scan-decision.v1",
        "status": "Review",
        "owner": "业务 Owner（待明确）",
        "approved_by": "",
        "approved_at": "",
    }
    for heading in (
        "## 切片边界",
        "## 输入身份与归一化",
        "## 状态与决策表",
        "## 能力与 Evidence 所有权",
        "## 异常矩阵",
        "## Replay 契约",
        "## 原因码决策记录",
        "## 当前实现对照",
        "## 验收标准",
    ):
        assert heading in content
    assert (
        "本切片有四类合法终点：下一设备命令已持久化、稳定原因码 Hold、late/unknown callback 的 "
        "evidence-only 归档、replay no-op；后两类均不得推进当前 Session。"
    ) in content
