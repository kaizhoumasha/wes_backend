"""WorklineInbox 退役终态 guardrail。"""

from pathlib import Path

from scripts.workline_inbox_retirement_guardrail import CURRENT_DOC_FILES, find_legacy_references
from src.app.runtime.orchestration.models.diagnostic import WorklineDiagnostic
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffSourceItem
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_FILES = (
    "src/app/runtime/orchestration/models/inbox.py",
    "src/app/runtime/orchestration/repositories/inbox_repository.py",
    "src/app/runtime/orchestration/services/inbox/inbox_service.py",
)


def _find_legacy_references(*, repo_root: Path, roots: tuple[str, ...]) -> list[str]:
    return sorted({finding.path for finding in find_legacy_references(repo_root=repo_root, roots=roots)})


def test_legacy_workline_inbox_surface_is_physically_removed() -> None:
    for relative_path in LEGACY_FILES:
        assert not (REPO_ROOT / relative_path).exists(), f"旧 Inbox 文件仍存在: {relative_path}"


def test_active_source_and_tests_have_zero_legacy_workline_inbox_references() -> None:
    offenders = sorted({finding.path for finding in find_legacy_references(repo_root=REPO_ROOT)})
    assert not offenders, f"active code/test 仍引用旧 WorklineInbox: {sorted(offenders)}"


def test_migration_roundtrip_test_is_the_only_allowed_test_reference(tmp_path: Path) -> None:
    migration_test = tmp_path / "tests/integration/test_runtime_inbox_migration_postgresql.py"
    migration_test.parent.mkdir(parents=True)
    migration_test.write_text("SELECT * FROM wes_biz.workline_inbox", encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("tests",)) == []


def test_other_test_file_with_legacy_reference_is_still_rejected(tmp_path: Path) -> None:
    migration_test = tmp_path / "tests/integration/test_runtime_inbox_migration_postgresql.py"
    migration_test.parent.mkdir(parents=True)
    migration_test.write_text("SELECT * FROM wes_biz.workline_inbox", encoding="utf-8")
    offender = tmp_path / "tests/api/test_legacy_reference.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("tests",)) == ["tests/api/test_legacy_reference.py"]


def test_scanner_covers_active_python_shell_and_current_markdown(tmp_path: Path) -> None:
    fixtures = {
        "src/app/offender.py": "from somewhere import WorklineInbox",
        "scripts/offender.sh": "task=src.celery_app.tasks.workline.process_inbox_batch",
        "docs/current.md": "SELECT * FROM wes_biz.workline_inbox",
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("src", "scripts", "docs")) == sorted(fixtures)


def test_scanner_rejects_case_path_and_concatenation_evasions(tmp_path: Path) -> None:
    fixtures = {
        "src/app/WorkLine_InBoX.py": "CURRENT = True",
        "src/app/concat.py": 'LEGACY = "Workline" + "Inbox"',
        "scripts/concat.sh": "legacy='process_'\"inbox_batch\"",
        "docs/current.md": "`WES_BIZ.` + `WORKLINE_INBOX`",
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("src", "scripts", "docs")) == sorted(fixtures)


def test_archive_and_exact_migration_evidence_do_not_hide_neighbor_offender(tmp_path: Path) -> None:
    archive = tmp_path / "docs/archive/history.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("WorklineInbox", encoding="utf-8")
    migration = tmp_path / "migrations/versions/retire_workline_inbox.py"
    migration.parent.mkdir(parents=True)
    migration.write_text('op.drop_table("workline_inbox")', encoding="utf-8")
    neighbor = tmp_path / "migrations/versions/retire_workline_inbox_copy.py"
    neighbor.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("docs", "migrations")) == [
        "migrations/versions/retire_workline_inbox_copy.py"
    ]


def test_signature_allowlist_does_not_skip_other_legacy_reference_in_same_file(tmp_path: Path) -> None:
    evidence = tmp_path / "tests/deployment/test_runtime_inbox_celery_cutover.py"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        "src.celery_app.tasks.workline.process_inbox_batch\nfrom somewhere import WorklineInbox",
        encoding="utf-8",
    )

    findings = find_legacy_references(repo_root=tmp_path, roots=("tests",))

    assert [(finding.path, finding.signature) for finding in findings] == [
        ("tests/deployment/test_runtime_inbox_celery_cutover.py", "legacy_symbol")
    ]


def test_current_docs_are_explicit_files_and_never_archive_or_plan_prefixes() -> None:
    required_current_docs = {
        "docs/architecture/file_index.md",
        "docs/architecture/runtime-ownership-map.md",
        "docs/business/e2e_conveyor_plan.md",
        "docs/business/workline_business_data_event_flow_spec.md",
        "docs/business/workline_runtime_workflow_guide.md",
        "docs/contracts/observability-contract.md",
        "docs/architecture/adr/2026-05-26-wms-integration-domain.md",
    }

    assert required_current_docs <= set(CURRENT_DOC_FILES)
    assert all(path.endswith(".md") for path in CURRENT_DOC_FILES)
    assert all(
        "/archive/" not in path and "/plans/" not in path and "/specs/" not in path for path in CURRENT_DOC_FILES
    )


def test_scanner_rejects_legacy_modules_without_workline_inbox_symbol(tmp_path: Path) -> None:
    fixtures = {
        "src/model_import.py": "import src.app.workline.models.inbox as legacy_model",
        "src/model_member_import.py": "from src.app.workline.models import inbox as legacy_model",
        "src/runtime_model_member_import.py": (
            "from src.app.runtime.orchestration.models import inbox as legacy_model"
        ),
        "src/repository_import.py": "from src.app.workline.repositories import inbox_repository as legacy_repo",
        "src/runtime_repository_import.py": (
            "from src.app.runtime.orchestration.repositories import inbox_repository as legacy_repo"
        ),
        "src/service_import.py": "from src.app.workline.services import inbox_service as legacy_service",
        "src/runtime_service_import.py": (
            "from src.app.runtime.orchestration.services.inbox import inbox_service as legacy_service"
        ),
        "src/processor_import.py": ("from src.app.workline.services import inbox_batch_processor as legacy_processor"),
        "src/runtime_processor_import.py": (
            "from src.app.runtime.orchestration.services.inbox import inbox_batch_processor as legacy_processor"
        ),
        "src/runtime_consumer_import.py": (
            "from src.app.runtime.orchestration.consumers import runtime_inbox_consumer as legacy_consumer"
        ),
        "src/runtime_consumer_repository_import.py": (
            "from src.app.runtime.orchestration.consumers import runtime_inbox_repository as legacy_repository"
        ),
        "src/runtime_claim_repository_import.py": (
            "from src.app.runtime.orchestration.repositories import runtime_inbox_claim_repository as legacy_claim"
        ),
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert {finding.path for finding in find_legacy_references(repo_root=tmp_path, roots=("src",))} == set(fixtures)


def test_scanner_rejects_parenthesized_alias_and_relative_python_imports(tmp_path: Path) -> None:
    fixtures = {
        "src/app/runtime/orchestration/services/inbox/service_caller.py": (
            "from . import (\n    inbox_service as legacy_service,\n)\n"
        ),
        "src/app/runtime/orchestration/repositories/repository_caller.py": (
            "from src.app.runtime.orchestration.repositories import (\n    inbox_repository as legacy_repository,\n)\n"
        ),
        "src/app/runtime/orchestration/models/model_caller.py": (
            "from src.app.runtime.orchestration.models import (\n    inbox as legacy_model,\n)\n"
        ),
        "src/app/runtime/orchestration/consumers/consumer_caller.py": (
            "from . import (\n    runtime_inbox_consumer as legacy_consumer,\n)\n"
        ),
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert {
        ("src/app/runtime/orchestration/services/inbox/service_caller.py", "legacy_service_member_import"),
        ("src/app/runtime/orchestration/repositories/repository_caller.py", "legacy_repository_member_import"),
        ("src/app/runtime/orchestration/models/model_caller.py", "legacy_model_member_import"),
        ("src/app/runtime/orchestration/consumers/consumer_caller.py", "legacy_consumer_member_import"),
    } <= {(finding.path, finding.signature) for finding in findings}


def test_default_scan_fails_closed_when_all_current_docs_are_missing(tmp_path: Path) -> None:
    findings = find_legacy_references(repo_root=tmp_path)

    assert {finding.path for finding in findings} == set(CURRENT_DOC_FILES)
    assert {finding.signature for finding in findings} == {"policy_error"}


def test_default_scan_fails_closed_when_one_current_doc_is_missing(tmp_path: Path) -> None:
    missing = CURRENT_DOC_FILES[-1]
    for relative_path in CURRENT_DOC_FILES[:-1]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# current\n", encoding="utf-8")

    findings = find_legacy_references(repo_root=tmp_path)

    assert [(finding.path, finding.signature) for finding in findings] == [(missing, "policy_error")]


def test_default_scan_reports_python_syntax_errors_but_explicit_fixture_scan_does_not(tmp_path: Path) -> None:
    for relative_path in CURRENT_DOC_FILES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# current\n", encoding="utf-8")
    broken = tmp_path / "src/broken.py"
    broken.parent.mkdir(parents=True)
    broken.write_text("not valid python ???", encoding="utf-8")

    default_findings = find_legacy_references(repo_root=tmp_path)
    explicit_findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.path, finding.signature) for finding in default_findings] == [("src/broken.py", "policy_error")]
    assert explicit_findings == []


def test_e2e_current_code_list_assigns_loader_to_runtime_inbox_bridge() -> None:
    source = (REPO_ROOT / "docs/business/e2e_conveyor_plan.md").read_text(encoding="utf-8")

    assert "RuntimeInboxProcessorBridge.process_claimed" in source
    assert "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py" in source
    task_entry, bridge_entry = source.split("src/celery_app/tasks/runtime_inbox.py", maxsplit=1)[1].split(
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py",
        maxsplit=1,
    )
    assert "_load_related_entities" not in task_entry
    assert "_load_related_entities" in bridge_entry


def test_scanner_rejects_retired_processor_and_consumer_symbols(tmp_path: Path) -> None:
    fixtures = {
        "src/processor.py": "processor = InboxBatchProcessor()",
        "src/consumer.py": "consumer: RuntimeInboxConsumer",
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert {finding.path for finding in find_legacy_references(repo_root=tmp_path, roots=("src",))} == set(fixtures)


def test_architecture_script_executes_shared_workline_inbox_scanner() -> None:
    source = (REPO_ROOT / "scripts/architecture-guardrails.sh").read_text(encoding="utf-8")

    assert "rule_workline_inbox_retirement" in source
    assert "workline_inbox_retirement_guardrail.py" in source
    assert "scanner_status -ne 0 && -z" in source
    assert "拒绝 fail open" in source


def test_runtime_inbox_and_dependent_models_point_to_current_authorities() -> None:
    assert RuntimeInbox.__table__.c.workline_session_id.foreign_keys.pop().target_fullname == (
        "wes_biz.workline_sessions.id"
    )
    runtime_target = "wes_runtime.runtime_inbox.id"
    for model, column_name in (
        (SmtInboundHandoffSourceItem, "source_pick_inbox_id"),
        (RuntimeHold, "source_inbox_id"),
        (WorklineDiagnostic, "inbox_id"),
    ):
        foreign_keys = getattr(model.__table__.c, column_name).foreign_keys
        assert len(foreign_keys) == 1
        assert next(iter(foreign_keys)).target_fullname == runtime_target
