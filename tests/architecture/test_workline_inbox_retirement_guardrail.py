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
    assert CURRENT_DOC_FILES
    assert all(path.endswith(".md") for path in CURRENT_DOC_FILES)
    assert all(
        "/archive/" not in path and "/plans/" not in path and "/specs/" not in path for path in CURRENT_DOC_FILES
    )


def test_architecture_script_executes_shared_workline_inbox_scanner() -> None:
    source = (REPO_ROOT / "scripts/architecture-guardrails.sh").read_text(encoding="utf-8")

    assert "rule_workline_inbox_retirement" in source
    assert "workline_inbox_retirement_guardrail.py" in source


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
