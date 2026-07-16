"""粗分机扫码到准入决策窄闭环规格包测试。"""

import json
from pathlib import Path

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import normalize_six_in_one_payload
from src.app.workline.domain.models import BarcodeDecisionType
from src.app.workline.domain.services.barcode_decision_service import barcode_decision_service

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json"
SPEC_PATH = REPOSITORY_ROOT / "docs/business/rough_sorter_scan_decision_contract.md"

EXPECTED_CASE_IDS = [f"RS-SD-{index:03d}" for index in range(1, 14)]
REQUIRED_CASE_FIELDS = {
    "case_id",
    "trigger",
    "preconditions",
    "recorded_evidence",
    "expected_state",
    "expected_intents",
    "expected_outcome",
    "replay_expectation",
    "source_refs",
    "implementation_status",
}
ALLOWED_IMPLEMENTATION_STATUSES = {"covered", "partial", "gap"}
EXPECTED_CASE_CONTRACTS = {
    "RS-SD-001": {
        "trigger": {
            "event_type": "SCAN_COMPLETED",
            "source_event_id": "evt-scan-001",
            "payload": {
                "data": {
                    "HHPN": "HH-001",
                    "MfrPN": "MF-001",
                    "Qty": "100",
                    "DateCode": "260701",
                    "LotCode": "LOT-01",
                    "PkgID": "PKG-001",
                }
            },
        },
        "expected_state": {
            "material": "IN_TRANSIT",
            "context_phase": "PICK_TO_PIPELINE",
            "session": "WAITING_COMMAND_RESULT",
        },
        "expected_intents": [
            {"kind": "CREATE_MATERIAL_UNIT"},
            {"kind": "UPDATE_CONTEXT"},
            {"kind": "COMMAND", "action": "PICK_AND_PUT"},
        ],
        "expected_outcome": {"result": "PICK_AND_PUT_PERSISTED", "reason_code": None},
        "replay_keywords": ("同键同 digest", "不重复 CREATE_MATERIAL_UNIT", "COMMAND EFFECT"),
        "implementation_status": "covered",
    },
    "RS-SD-002": {
        "trigger": {
            "event_type": "SCAN_COMPLETED",
            "source_event_id": "evt-scan-002",
            "payload": {
                "data": {
                    "HHPN": "HH-NG",
                    "MfrPN": "MF-002",
                    "Qty": "100",
                    "DateCode": "260701",
                    "LotCode": "LOT-02",
                    "PkgID": "PKG-SIZENG-002",
                }
            },
        },
        "expected_state": {"material": "NG", "context_phase": "NG_MOVING", "session": "WAITING_COMMAND_RESULT"},
        "expected_intents": [
            {"kind": "CREATE_MATERIAL_UNIT"},
            {"kind": "UPDATE_CONTEXT"},
            {"kind": "MARK_NG"},
            {"kind": "COMMAND", "action": "MOVE_TO_NG"},
        ],
        "expected_outcome": {"result": "MOVE_TO_NG_PERSISTED", "reason_code": "SCAN_NG_BY_RULE"},
        "replay_keywords": ("首次条码 NG 判定", "不重复 MOVE_TO_NG EFFECT"),
        "implementation_status": "covered",
    },
    "RS-SD-003": {
        "trigger": {
            "event_type": "SCAN_COMPLETED",
            "source_event_id": "evt-scan-003",
            "payload": {
                "data": {
                    "HHPN": "HH-003",
                    "MfrPN": "MF-003",
                    "Qty": "100",
                    "DateCode": "260701",
                    "LotCode": "LOT-03",
                }
            },
        },
        "expected_state": {"material": "NOT_CREATED", "command": "NOT_CREATED", "session": "MANUAL_HOLD"},
        "expected_intents": [{"kind": "BLOCK", "scope": "MATERIAL"}],
        "expected_outcome": {"result": "HOLD", "reason_code": "ROUGH_SORTER_CONTEXT_MISSING"},
        "replay_keywords": ("保持原 Hold", "不补建物料或命令"),
        "implementation_status": "gap",
    },
    "RS-SD-004": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-004",
            "payload": {
                "command_code": "CMD-PICK-004",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {
                    "measurement_result": "OK",
                    "length_mm": "120",
                    "width_mm": "80",
                    "height_mm": "15",
                },
            },
        },
        "expected_state": {
            "material": "IN_TRANSIT",
            "context_phase": "MOVING_FORWARD",
            "session": "WAITING_COMMAND_RESULT",
        },
        "expected_intents": [{"kind": "UPDATE_CONTEXT"}, {"kind": "COMMAND", "action": "MOVE_FORWARD"}],
        "expected_outcome": {"result": "MOVE_FORWARD_PERSISTED", "reason_code": None},
        "replay_keywords": ("不重新查询 WMS", "不重复 MOVE_FORWARD EFFECT"),
        "implementation_status": "gap",
    },
    "RS-SD-005": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-005",
            "payload": {
                "command_code": "CMD-PICK-005",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {"measurement_result": "NG", "reason": "HEIGHT_OUT_OF_RANGE"},
            },
        },
        "expected_state": {"material": "NG", "context_phase": "NG_MOVING", "session": "WAITING_COMMAND_RESULT"},
        "expected_intents": [
            {"kind": "UPDATE_CONTEXT"},
            {"kind": "MARK_NG"},
            {"kind": "COMMAND", "action": "MOVE_TO_NG"},
        ],
        "expected_outcome": {"result": "MOVE_TO_NG_PERSISTED", "reason_code": "MEASUREMENT_NG"},
        "replay_keywords": ("首次测量 NG 决策", "不查询 WMS", "不重复 MOVE_TO_NG EFFECT"),
        "implementation_status": "gap",
    },
    "RS-SD-006": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-006",
            "payload": {
                "command_code": "CMD-PICK-006",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {"measurement_result": "OK"},
            },
        },
        "expected_state": {"material": "NG", "context_phase": "NG_MOVING", "session": "WAITING_COMMAND_RESULT"},
        "expected_intents": [
            {"kind": "UPDATE_CONTEXT"},
            {"kind": "MARK_NG"},
            {"kind": "COMMAND", "action": "MOVE_TO_NG"},
        ],
        "expected_outcome": {"result": "MOVE_TO_NG_PERSISTED", "reason_code": "WMS_REJECTED"},
        "replay_keywords": ("首次 WMS 拒绝摘要", "不重新查询 WMS", "不重复 MOVE_TO_NG EFFECT"),
        "implementation_status": "gap",
    },
    "RS-SD-007": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-007",
            "payload": {
                "command_code": "CMD-PICK-007",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {"measurement_result": "OK", "length_mm": "invalid"},
            },
        },
        "expected_state": {"material": "MANUAL_HOLD", "command": "UNCHANGED", "session": "MANUAL_HOLD"},
        "expected_intents": [{"kind": "BLOCK", "scope": "MATERIAL"}],
        "expected_outcome": {"result": "HOLD", "reason_code": "ROUGH_SORTER_MEASUREMENT_INVALID"},
        "replay_keywords": ("保持原 Hold", "不查询 WMS", "不生成运输命令"),
        "implementation_status": "gap",
    },
    "RS-SD-008": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-008",
            "payload": {
                "command_code": "CMD-PICK-008",
                "command_type": "PICK_AND_PUT",
                "result": "FAILED",
                "error_detail": {"error_code": "DEVICE_BUSY"},
            },
        },
        "expected_state": {"material": "IN_TRANSIT", "command": "MANUAL_HOLD", "session": "MANUAL_HOLD"},
        "expected_intents": [{"kind": "BLOCK", "scope": "COMMAND"}],
        "expected_outcome": {"result": "HOLD", "reason_code": "DEVICE_BUSY"},
        "replay_keywords": ("保持原命令 Hold", "不生成后续命令"),
        "implementation_status": "covered",
    },
    "RS-SD-009": {
        "trigger": {
            "event_type": "TIMER_TIMEOUT",
            "source_event_id": "evt-timeout-009",
            "payload": {"command_code": "CMD-PICK-009", "wait_type": "COMMAND_RESULT"},
        },
        "expected_state": {"material": "IN_TRANSIT", "command": "MANUAL_HOLD", "session": "MANUAL_HOLD"},
        "expected_intents": [{"kind": "BLOCK", "scope": "COMMAND"}],
        "expected_outcome": {"result": "HOLD", "reason_code": "ROUGH_SORTER_PICK_RESULT_TIMEOUT"},
        "replay_keywords": ("保持首次超时 Hold", "迟到结果单独记录", "不自动推进"),
        "implementation_status": "partial",
    },
    "RS-SD-010": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-010",
            "payload": {
                "command_code": "CMD-PICK-010",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
                "data": {"measurement_result": "OK"},
            },
        },
        "expected_state": {"material": "MANUAL_HOLD", "command": "UNCHANGED", "session": "MANUAL_HOLD"},
        "expected_intents": [{"kind": "BLOCK", "scope": "MATERIAL"}],
        "expected_outcome": {
            "result": "HOLD",
            "reason_code": "WMS_TIMEOUT",
            "business_reason": "ROUGH_SORTER_WMS_ADMISSION_UNAVAILABLE",
        },
        "replay_keywords": ("首次 timeout evidence", "不在 replay 实时重查 WMS", "不把失败伪装成成功 evidence"),
        "implementation_status": "gap",
    },
    "RS-SD-011": {
        "trigger": {
            "event_type": "REPLAY_REQUEST",
            "source_event_id": "evt-replay-011",
            "payload": {
                "idempotency_key": "rough-sorter:PKG-011:scan-decision",
                "payload_digest": "sha256:same-digest",
            },
        },
        "expected_state": {"material": "UNCHANGED", "command": "UNCHANGED", "session": "UNCHANGED"},
        "expected_intents": [],
        "expected_outcome": {"result": "REPLAY_ACCEPTED_NOOP", "reason_code": None},
        "replay_keywords": ("首次 evidence", "不重复 WMS QUERY", "EFFECT"),
        "implementation_status": "gap",
    },
    "RS-SD-012": {
        "trigger": {
            "event_type": "REPLAY_REQUEST",
            "source_event_id": "evt-replay-012",
            "payload": {
                "idempotency_key": "rough-sorter:PKG-012:scan-decision",
                "payload_digest": "sha256:different-digest",
            },
        },
        "expected_state": {"material": "MANUAL_HOLD", "command": "UNCHANGED", "session": "MANUAL_HOLD"},
        "expected_intents": [{"kind": "BLOCK", "scope": "MATERIAL"}],
        "expected_outcome": {"result": "HOLD", "reason_code": "IDEMPOTENCY_CONFLICT"},
        "replay_keywords": ("记录冲突并 Hold", "不执行 QUERY", "EFFECT"),
        "implementation_status": "gap",
    },
    "RS-SD-013": {
        "trigger": {
            "event_type": "COMMAND_RESULT",
            "source_event_id": "evt-result-013",
            "payload": {
                "command_code": "CMD-OLD-013",
                "command_type": "PICK_AND_PUT",
                "result": "SUCCESS",
            },
        },
        "expected_state": {"material": "UNCHANGED", "command": "CORRELATION_HOLD", "session": "UNCHANGED"},
        "expected_intents": [{"kind": "BLOCK", "scope": "COMMAND"}],
        "expected_outcome": {
            "result": "HOLD_WITHOUT_SESSION_ADVANCE",
            "reason_code": "COMMAND_RESULT_CORRELATION_MISMATCH",
        },
        "replay_keywords": ("correlation mismatch evidence", "不得推进当前 Session", "生成后续命令"),
        "implementation_status": "partial",
    },
}
EXPECTED_EVIDENCE_CONTRACTS = {
    "RS-SD-001": {
        "preconditions": ("RuntimeInbox 已接受并归一化事件", "本地条码规则判定 OK"),
        "first_attempt": ("normalized_input_snapshot", "payload_digest", "barcode_decision", "intent_identity"),
        "replay": ("original_decision", "original_intent_identity", "payload_digest"),
        "replay_expectation": "同键同 digest 返回原始决策，不重复 CREATE_MATERIAL_UNIT 或 COMMAND EFFECT。",
    },
    "RS-SD-002": {
        "preconditions": ("RuntimeInbox 已接受并归一化事件", "本地条码规则判定业务 NG"),
        "first_attempt": (
            "normalized_input_snapshot",
            "payload_digest",
            "barcode_ng_reason",
            "intent_identity",
        ),
        "replay": ("original_decision", "original_intent_identity", "payload_digest"),
        "replay_expectation": "复用首次条码 NG 判定，不重复 MOVE_TO_NG EFFECT。",
    },
    "RS-SD-003": {
        "preconditions": ("RuntimeInbox 已接受并归一化事件", "输入缺少 PkgID"),
        "first_attempt": ("normalized_input_snapshot", "payload_digest", "missing_fields", "hold_reason"),
        "replay": ("original_hold", "payload_digest"),
        "replay_expectation": "保持原 Hold，不补建物料或命令。",
    },
    "RS-SD-004": {
        "preconditions": ("PICK_AND_PUT 等待锚点匹配", "测量合同有效", "WMS 准入 ACCEPTED"),
        "first_attempt": (
            "command_result_snapshot",
            "measurement_snapshot",
            "wms_response_summary",
            "decision",
            "intent_identity",
            "payload_digest",
        ),
        "replay": (
            "original_measurement",
            "original_wms_response_summary",
            "original_decision",
            "original_intent_identity",
            "payload_digest",
        ),
        "replay_expectation": "不重新查询 WMS，不重复 MOVE_FORWARD EFFECT。",
    },
    "RS-SD-005": {
        "preconditions": ("PICK_AND_PUT 等待锚点匹配", "测量合同有效且业务判定 NG"),
        "first_attempt": (
            "command_result_snapshot",
            "measurement_snapshot",
            "decision",
            "intent_identity",
            "payload_digest",
        ),
        "replay": (
            "original_measurement",
            "original_decision",
            "original_intent_identity",
            "payload_digest",
        ),
        "replay_expectation": "复用首次测量 NG 决策，不查询 WMS、不重复 MOVE_TO_NG EFFECT。",
    },
    "RS-SD-006": {
        "preconditions": ("PICK_AND_PUT 等待锚点匹配", "测量合同有效", "WMS 返回 REJECTED 或无匹配"),
        "first_attempt": (
            "command_result_snapshot",
            "measurement_snapshot",
            "wms_response_summary",
            "decision",
            "intent_identity",
            "payload_digest",
        ),
        "replay": (
            "original_wms_response_summary",
            "original_decision",
            "original_intent_identity",
            "payload_digest",
        ),
        "replay_expectation": "复用首次 WMS 拒绝摘要，不重新查询 WMS、不重复 MOVE_TO_NG EFFECT。",
    },
    "RS-SD-007": {
        "preconditions": ("PICK_AND_PUT 等待锚点匹配", "测量字段缺失、类型错误或越界"),
        "first_attempt": ("command_result_snapshot", "measurement_validation_errors", "decision", "payload_digest"),
        "replay": ("original_validation_errors", "original_hold", "payload_digest"),
        "replay_expectation": "保持原 Hold，不查询 WMS、不生成运输命令。",
    },
    "RS-SD-008": {
        "preconditions": ("PICK_AND_PUT 等待锚点匹配", "设备返回终态失败"),
        "first_attempt": ("command_result_snapshot", "device_error_summary", "hold_reason", "payload_digest"),
        "replay": ("original_device_error", "original_hold", "payload_digest"),
        "replay_expectation": "保持原命令 Hold，不生成后续命令。",
    },
    "RS-SD-009": {
        "preconditions": (
            "PICK_AND_PUT 已持久化",
            "等待结果超过命令结果 deadline",
            "没有匹配终态结果 evidence",
        ),
        "first_attempt": ("command_identity", "deadline_snapshot", "timeout_event", "hold_reason", "payload_digest"),
        "replay": ("original_timeout_decision", "original_hold", "payload_digest"),
        "replay_expectation": "保持首次超时 Hold；迟到结果单独记录，不自动推进。",
    },
    "RS-SD-010": {
        "preconditions": ("PICK_AND_PUT 等待锚点匹配", "测量合同有效", "首次 WMS QUERY timeout 或 unavailable"),
        "first_attempt": (
            "command_result_snapshot",
            "measurement_snapshot",
            "wms_timeout_summary",
            "decision",
            "payload_digest",
        ),
        "replay": (
            "original_timeout_summary",
            "original_hold",
            "payload_digest",
            "no_successful_wms_evidence",
        ),
        "replay_expectation": "保留首次 timeout evidence，不把失败伪装成成功 evidence，也不在 replay 实时重查 WMS。",
    },
    "RS-SD-011": {
        "preconditions": ("同 idempotency key 的首次 attempt 已完成", "replay payload digest 与首次一致"),
        "first_attempt": (
            "normalized_input_snapshot",
            "query_response_summary",
            "decision",
            "intent_identity",
            "payload_digest",
        ),
        "replay": ("replay_request", "matched_payload_digest", "reused_decision", "reused_intent_identity"),
        "replay_expectation": "返回首次 evidence；不重复 WMS QUERY 或任何 EFFECT。",
    },
    "RS-SD-012": {
        "preconditions": ("同 idempotency key 的首次 attempt 已存在", "incoming digest 与首次不同"),
        "first_attempt": ("original_payload_digest", "original_decision", "original_intent_identity"),
        "replay": ("incoming_payload_digest", "digest_mismatch", "conflict_audit"),
        "replay_expectation": "记录冲突并 Hold；不执行 QUERY 或 EFFECT。",
    },
    "RS-SD-013": {
        "preconditions": ("callback 未匹配当前 Session 的等待命令", "callback 属于迟到命令或未知命令"),
        "first_attempt": (
            "callback_snapshot",
            "correlation_lookup",
            "current_wait_anchor",
            "mismatch_reason",
            "payload_digest",
        ),
        "replay": ("original_mismatch_evidence", "payload_digest"),
        "replay_expectation": "复用 correlation mismatch evidence；不得推进当前 Session，也不得生成后续命令。",
    },
}


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_trace_fixture_declares_fixed_slice_and_case_set() -> None:
    fixture = _load_fixture()
    cases = fixture["cases"]
    case_ids = [case["case_id"] for case in cases]

    assert fixture["schema_version"] == "rough-sorter-scan-decision.v1"
    assert fixture["slice_id"] == "rough_sorter.scan_to_admission_decision"
    assert case_ids == EXPECTED_CASE_IDS
    assert len(case_ids) == len(set(case_ids))


def test_every_trace_case_has_reviewable_evidence_and_source_refs() -> None:
    cases = _load_fixture()["cases"]

    for case in cases:
        assert case.keys() >= REQUIRED_CASE_FIELDS, case["case_id"]
        assert case["implementation_status"] in ALLOWED_IMPLEMENTATION_STATUSES
        assert isinstance(case["trigger"], dict) and case["trigger"], case["case_id"]
        assert isinstance(case["preconditions"], list) and all(case["preconditions"]), case["case_id"]
        assert isinstance(case["recorded_evidence"], dict), case["case_id"]
        assert set(case["recorded_evidence"]) == {"first_attempt", "replay"}, case["case_id"]
        assert all(case["recorded_evidence"].values()), case["case_id"]
        assert isinstance(case["expected_state"], dict) and all(case["expected_state"].values()), case["case_id"]
        assert isinstance(case["expected_intents"], list), case["case_id"]
        assert all(intent.get("kind") for intent in case["expected_intents"]), case["case_id"]
        assert case["expected_outcome"].get("result"), case["case_id"]
        assert "reason_code" in case["expected_outcome"], case["case_id"]
        assert isinstance(case["replay_expectation"], str) and case["replay_expectation"].strip(), case["case_id"]
        assert case["source_refs"] and all(
            isinstance(source_ref, str) and source_ref.strip() and (REPOSITORY_ROOT / source_ref).is_file()
            for source_ref in case["source_refs"]
        ), case["case_id"]


def test_every_case_matches_the_stable_decision_contract() -> None:
    cases_by_id = {case["case_id"]: case for case in _load_fixture()["cases"]}

    assert cases_by_id.keys() == EXPECTED_CASE_CONTRACTS.keys()
    for case_id, expected in EXPECTED_CASE_CONTRACTS.items():
        case = cases_by_id[case_id]
        for field_name in (
            "trigger",
            "expected_state",
            "expected_intents",
            "expected_outcome",
            "implementation_status",
        ):
            assert case[field_name] == expected[field_name], f"{case_id}.{field_name}"
        assert all(keyword in case["replay_expectation"] for keyword in expected["replay_keywords"]), case_id


def test_every_case_preserves_first_attempt_and_replay_evidence_ownership() -> None:
    cases_by_id = {case["case_id"]: case for case in _load_fixture()["cases"]}

    assert cases_by_id.keys() == EXPECTED_EVIDENCE_CONTRACTS.keys()
    for case_id, expected in EXPECTED_EVIDENCE_CONTRACTS.items():
        case = cases_by_id[case_id]
        assert tuple(case["preconditions"]) == expected["preconditions"], f"{case_id}.preconditions"
        assert tuple(case["recorded_evidence"]["first_attempt"]) == expected["first_attempt"], (
            f"{case_id}.recorded_evidence.first_attempt"
        )
        assert tuple(case["recorded_evidence"]["replay"]) == expected["replay"], f"{case_id}.recorded_evidence.replay"
        assert case["replay_expectation"] == expected["replay_expectation"], f"{case_id}.replay_expectation"


def test_current_barcode_rules_explain_scan_case_coverage_status() -> None:
    cases_by_id = {case["case_id"]: case for case in _load_fixture()["cases"]}

    rule_ng = barcode_decision_service.evaluate(
        normalize_six_in_one_payload(cases_by_id["RS-SD-002"]["trigger"]["payload"])
    )
    missing_pkg_id = barcode_decision_service.evaluate(
        normalize_six_in_one_payload(cases_by_id["RS-SD-003"]["trigger"]["payload"])
    )

    assert (rule_ng.decision, rule_ng.reason_code) == (BarcodeDecisionType.NG, "SCAN_NG_BY_RULE")
    assert (missing_pkg_id.decision, missing_pkg_id.reason_code) == (
        BarcodeDecisionType.INCOMPLETE,
        "BARCODE_INCOMPLETE",
    )
    assert cases_by_id["RS-SD-003"]["implementation_status"] == "gap"


def test_business_spec_contains_stable_metadata_and_review_sections() -> None:
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
