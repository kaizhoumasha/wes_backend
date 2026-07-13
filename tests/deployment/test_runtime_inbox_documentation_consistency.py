"""RuntimeInbox 当前事实文档与实施状态合同。"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.workline_inbox_retirement_guardrail import CURRENT_DOC_FILES

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN = REPO_ROOT / "docs/superpowers/specs/2026-07-12-runtime-inbox-acceptance-closure-design.md"
PLAN = REPO_ROOT / "docs/superpowers/plans/2026-07-10-runtime-inbox-single-source-of-truth.md"
FILE_INDEX = REPO_ROOT / "docs/architecture/file_index.md"
BUSINESS_SSOT = REPO_ROOT / "docs/business/workline_business_data_event_flow_spec.md"
RUNTIME_SPEC = REPO_ROOT / "docs/architecture/runtime-orchestration-spec.md"
WORKFLOW_GUIDE = REPO_ROOT / "docs/business/workline_runtime_workflow_guide.md"
TODOS = REPO_ROOT / "TODOS.md"

RUNTIME_INBOX_KINDS = {
    "COMMAND_RESULT",
    "DEVICE_EVENT",
    "EXTERNAL_HTTP",
    "INTERNAL_EVENT",
    "TIMER_TIMEOUT",
    "REPLAY_REQUEST",
}
RUNTIME_INBOX_STATES = {"RECEIVED", "PROCESSING", "PROCESSED", "FAILED", "DEAD_LETTER"}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_runtime_docs_lock_six_kinds_five_states_and_audit_only_boundary():
    documents = "\n".join(_text(path) for path in (BUSINESS_SSOT, RUNTIME_SPEC, WORKFLOW_GUIDE))

    assert all(kind in documents for kind in RUNTIME_INBOX_KINDS)
    assert all(state in documents for state in RUNTIME_INBOX_STATES)
    assert "EXTERNAL_CALLBACK" not in _text(BUSINESS_SSOT)
    assert "MANUAL_OPERATION" not in _text(BUSINESS_SSOT)
    assert "NEW → (claim)" not in _text(RUNTIME_SPEC)
    assert "PRE_CUTOVER_AUDIT_ONLY" in documents
    assert "不可 claim、retry 或 replay" in documents


def test_current_runtime_docs_describe_service_replay_reset_heavy_and_ci_paths():
    documents = "\n".join(_text(REPO_ROOT / path) for path in CURRENT_DOC_FILES)
    required_contracts = (
        "services/runtime_inbox/runtime_inbox_service.py",
        "REPLAY_REQUEST",
        "request_id",
        "actor",
        "reason",
        "scripts/data/reset_runtime_data.py",
        "scripts/run_runtime_inbox_postgresql_acceptance.py",
        "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh",
        "Jenkinsfile.backend-ci",
        "runtime-inbox-claim-benchmark.json",
    )

    assert all(contract in documents for contract in required_contracts)


def test_acceptance_design_marks_t1_to_t9_complete_and_keeps_t10_pending():
    design = _text(DESIGN)

    for task in range(1, 10):
        assert re.search(rf"- \[x\] \*\*T{task} ", design)
    assert re.search(r"- \[ \] \*\*T10 ", design)
    assert "T10 最终全量验收尚未执行" in design
    assert "## T1–T9 提交与证据摘要" in design


def test_original_plan_removes_stale_warnings_without_claiming_t10_complete():
    plan = _text(PLAN)
    task_9 = plan.split("### Task 9：", maxsplit=1)[1].split("## 测试覆盖图", maxsplit=1)[0]

    assert "⚠️ 未跑 `alembic upgrade head`" not in plan
    assert "仍保留（28 个 consumer 依赖" not in plan
    assert "T9 文档同步完成；T10 最终验收待执行" in task_9
    assert "全量测试 `2090 passed, 5 skipped`" not in task_9


def test_file_index_lists_runtime_inbox_migrations_operations_ci_tests_and_response_codes():
    index = _text(FILE_INDEX)
    required_paths = (
        "Jenkinsfile.backend-ci",
        "20260711_1815_b8a28e1bfec8_extend_runtime_inbox.py",
        "20260711_1819_ec426c628516_retire_workline_inbox.py",
        "scripts/data/reset_runtime_data.py",
        "scripts/workline_inbox_retirement_guardrail.py",
        "scripts/run_runtime_inbox_postgresql_acceptance.py",
        "scripts/run_runtime_inbox_postgresql_acceptance_ci.sh",
        "tests/deployment/test_runtime_inbox_postgresql_acceptance_ci.py",
        "tests/integration/test_runtime_inbox_processing_postgresql.py",
        "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py",
        "tests/load/test_runtime_inbox_claim_benchmark.py",
    )

    assert all(path in index for path in required_paths)
    assert "RUNTIME_INBOX_NOT_FOUND" in index
    assert "RUNTIME_INBOX_REPLAY_NOT_ALLOWED" in index


def test_todos_active_section_contains_no_completed_runtime_inbox_work():
    active = _text(TODOS).split("## Completed", maxsplit=1)[0]

    assert "**Completed:**" not in active
    assert "WorkLine 域模型 / 仓库物理迁移" not in active
    assert "28 处 workline 域 import 跨域改写" not in active
    assert "统一运营看板、告警与 Runbook" in active


def test_current_doc_files_and_local_markdown_links_exist():
    checked_files = [*(REPO_ROOT / path for path in CURRENT_DOC_FILES), DESIGN, PLAN, TODOS]
    assert all(path.is_file() for path in checked_files)

    missing_links: list[str] = []
    for document in checked_files:
        for target in re.findall(r"\[[^]]*]\(([^)]+)\)", _text(document)):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            relative_target = target.split("#", maxsplit=1)[0]
            if relative_target and not (document.parent / relative_target).resolve().exists():
                missing_links.append(f"{document.relative_to(REPO_ROOT)} -> {target}")

    assert not missing_links, "当前文档存在失效本地链接:\n  " + "\n  ".join(missing_links)
