"""粗分机扫码到准入决策窄闭环规格包测试。"""

import json
from pathlib import Path

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
        assert case["source_refs"], case["case_id"]


def test_business_spec_contains_stable_metadata_and_review_sections() -> None:
    content = SPEC_PATH.read_text(encoding="utf-8")

    assert "contract_version: rough-sorter-scan-decision.v1" in content
    assert "status: Review" in content
    assert "approved_by:" in content
    assert "approved_at:" in content
    for heading in (
        "## 切片边界",
        "## 状态与决策表",
        "## 能力与 Evidence 所有权",
        "## 异常矩阵",
        "## Replay 契约",
        "## 验收标准",
    ):
        assert heading in content
