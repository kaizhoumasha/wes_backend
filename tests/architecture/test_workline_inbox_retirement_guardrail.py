"""WorklineInbox 退役终态 guardrail。"""

import shutil
import subprocess
import sys
from pathlib import Path

from scripts.workline_inbox_retirement_guardrail import (
    ALLOWED_EVIDENCE,
    find_legacy_references,
    main,
)

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


def test_retired_migration_test_reference_is_rejected(tmp_path: Path) -> None:
    migration_test = tmp_path / "tests/integration/test_runtime_inbox_migration_postgresql.py"
    migration_test.parent.mkdir(parents=True)
    migration_test.write_text("SELECT * FROM wes_biz.workline_inbox", encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("tests",)) == [
        "tests/integration/test_runtime_inbox_migration_postgresql.py"
    ]


def test_other_test_file_with_legacy_reference_is_still_rejected(tmp_path: Path) -> None:
    migration_test = tmp_path / "tests/integration/test_runtime_inbox_migration_postgresql.py"
    migration_test.parent.mkdir(parents=True)
    migration_test.write_text("SELECT * FROM wes_biz.workline_inbox", encoding="utf-8")
    offender = tmp_path / "tests/api/test_legacy_reference.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("tests",)) == [
        "tests/api/test_legacy_reference.py",
        "tests/integration/test_runtime_inbox_migration_postgresql.py",
    ]


def test_scanner_covers_active_python_and_shell(tmp_path: Path) -> None:
    fixtures = {
        "src/app/offender.py": "from somewhere import WorklineInbox",
        "scripts/offender.sh": "task=src.celery_app.tasks.workline.process_inbox_batch",
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("src", "scripts")) == sorted(fixtures)


def test_scanner_does_not_treat_archive_named_source_directory_as_external_archive(tmp_path: Path) -> None:
    offender = tmp_path / "src" / "archive" / "offender.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("src",)) == ["src/archive/offender.py"]


def test_scanner_rejects_case_path_and_concatenation_evasions(tmp_path: Path) -> None:
    fixtures = {
        "src/app/WorkLine_InBoX.py": "CURRENT = True",
        "src/app/concat.py": 'LEGACY = "Workline" + "Inbox"',
        "scripts/concat.sh": "legacy='process_'\"inbox_batch\"",
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _find_legacy_references(repo_root=tmp_path, roots=("src", "scripts")) == sorted(fixtures)


def test_scanner_folds_parenthesized_nested_and_static_fstring_references(tmp_path: Path) -> None:
    fixtures = {
        "src/parenthesized.py": 'MODULE = "src.app.runtime.orchestration.models." + ("inbox")\n',
        "src/nested.py": ('MODULE = (("src.app.workline.repositories.")) + (("inbox_repository"))\n'),
        "src/importlib_path.py": (
            "import importlib\n"
            'importlib.import_module("src.app.runtime.orchestration.services.inbox." + ("inbox_service"))\n'
        ),
        "src/static_fstring.py": (
            "MODULE = f\"{'src.app.runtime.orchestration.consumers.'}{'runtime_inbox_consumer'}\"\n"
        ),
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.path, finding.signature) for finding in findings] == [
        ("src/importlib_path.py", "legacy_service_import"),
        ("src/nested.py", "legacy_workline_repository_import"),
        ("src/parenthesized.py", "legacy_model_import"),
        ("src/static_fstring.py", "legacy_consumer_import"),
    ]


def test_static_string_folding_does_not_evaluate_dynamic_values(tmp_path: Path) -> None:
    fixtures = {
        "src/dynamic_name.py": 'MODULE = "src.app.runtime.orchestration.models." + suffix\n',
        "src/dynamic_call.py": ('MODULE = "src.app.runtime.orchestration.models." + build_module_name()\n'),
        "src/dynamic_fstring.py": ('MODULE = f"src.app.runtime.orchestration.models.{module_name}"\n'),
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert find_legacy_references(repo_root=tmp_path, roots=("src",)) == []


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


def test_ast_import_findings_suppress_overlapping_symbol_regex(tmp_path: Path) -> None:
    fixtures = {
        "src/consumer.py": ("from src.app.runtime.orchestration.consumers import runtime_inbox_consumer as legacy\n"),
        "src/processor.py": (
            "from src.app.runtime.orchestration.services.inbox import inbox_batch_processor as legacy\n"
        ),
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.path, finding.signature) for finding in findings] == [
        ("src/consumer.py", "legacy_consumer_member_import"),
        ("src/processor.py", "legacy_batch_processor_member_import"),
    ]


def test_ast_import_suppression_keeps_later_non_import_symbol_reference(tmp_path: Path) -> None:
    path = tmp_path / "src/consumer.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from src.app.runtime.orchestration.consumers import runtime_inbox_consumer as legacy\n"
        "consumer_type = RuntimeInboxConsumer\n",
        encoding="utf-8",
    )

    findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.line, finding.signature) for finding in findings] == [
        (1, "legacy_consumer_member_import"),
        (2, "legacy_consumer_symbol"),
    ]


def test_ast_import_evidence_needs_only_import_allowlist_key(tmp_path: Path, monkeypatch) -> None:
    relative = "src/consumer.py"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(
        "from src.app.runtime.orchestration.consumers import runtime_inbox_consumer as legacy\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(ALLOWED_EVIDENCE, relative, frozenset({"legacy_consumer_member_import"}))

    assert find_legacy_references(repo_root=tmp_path, roots=("src",)) == []


def test_scanner_rejects_root_outside_repository_without_reading_it(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    (outside / "legacy.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")

    findings = find_legacy_references(repo_root=repo_root, roots=("../outside",))

    assert [(finding.path, finding.signature) for finding in findings] == [("../outside", "policy_error")]


def test_scanner_rejects_lexical_escape_even_when_root_resolves_back_inside_repository(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    source = repo_root / "src"
    source.mkdir(parents=True)

    findings = find_legacy_references(repo_root=repo_root, roots=("../repo/src",))

    assert [(finding.path, finding.signature) for finding in findings] == [("../repo/src", "policy_error")]


def test_scanner_accepts_absolute_root_inside_repository_and_rejects_absolute_root_outside(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    source = repo_root / "src"
    source.mkdir(parents=True)
    (source / "offender.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()

    internal_findings = find_legacy_references(repo_root=repo_root, roots=(str(source),))
    external_findings = find_legacy_references(repo_root=repo_root, roots=(str(outside),))

    assert [(finding.path, finding.signature) for finding in internal_findings] == [
        ("src/offender.py", "legacy_symbol")
    ]
    assert [(finding.path, finding.signature) for finding in external_findings] == [
        (outside.as_posix(), "policy_error")
    ]


def test_scanner_accepts_dot_and_nested_parent_normalization_that_never_escape_repository(tmp_path: Path) -> None:
    (tmp_path / "src/nested").mkdir(parents=True)
    offender = tmp_path / "tests/offender.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    dot_findings = find_legacy_references(repo_root=tmp_path, roots=("./tests",))
    nested_findings = find_legacy_references(repo_root=tmp_path, roots=("src/nested/../../tests",))

    assert [(finding.path, finding.signature) for finding in dot_findings] == [("tests/offender.py", "legacy_symbol")]
    assert [(finding.path, finding.signature) for finding in nested_findings] == [
        ("src/nested/../../tests/offender.py", "legacy_symbol")
    ]


def test_equivalent_relative_dot_and_absolute_roots_keep_the_same_suffix_policy(tmp_path: Path) -> None:
    fixtures = {
        "scripts": ("offender.sh", "task=process_inbox_batch\n", "legacy_task_short"),
        "src": ("offender.py", "WorklineInbox\n", "legacy_symbol"),
        "tests": ("offender.py", "WorklineInbox\n", "legacy_symbol"),
    }
    for root_name, (filename, content, expected_signature) in fixtures.items():
        scan_root = tmp_path / root_name
        scan_root.mkdir()
        (scan_root / filename).write_text(content, encoding="utf-8")

        for equivalent_root in (root_name, f"./{root_name}", str(scan_root)):
            findings = find_legacy_references(repo_root=tmp_path, roots=(equivalent_root,))

            assert [(finding.path, finding.signature) for finding in findings] == [
                (f"{root_name}/{filename}", expected_signature)
            ], equivalent_root


def test_scanner_rejects_external_file_and_directory_symlinks_without_traversal(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "legacy.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")
    source = tmp_path / "repo/src"
    source.mkdir(parents=True)
    (source / "linked.py").symlink_to(outside / "legacy.py")
    (source / "linked_dir").symlink_to(outside, target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path / "repo", roots=("src",))

    assert [(finding.path, finding.signature) for finding in findings] == [
        ("src/linked.py", "policy_error"),
        ("src/linked_dir", "policy_error"),
    ]


def test_scanner_rejects_internal_directory_symlink_without_traversal(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "legacy.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")
    linked = tmp_path / "src/linked_dir"
    linked.parent.mkdir()
    linked.symlink_to(shared, target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.path, finding.signature) for finding in findings] == [("src/linked_dir", "policy_error")]


def test_scanner_rejects_directory_symlink_used_as_explicit_root(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "legacy.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")
    (tmp_path / "linked_root").symlink_to(shared, target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path, roots=("linked_root",))

    assert [(finding.path, finding.signature) for finding in findings] == [("linked_root", "policy_error")]


def test_scanner_rejects_directory_symlink_in_an_explicit_root_component(tmp_path: Path) -> None:
    nested = tmp_path / "real/nested"
    nested.mkdir(parents=True)
    (nested / "offender.py").write_text("WorklineInbox\n", encoding="utf-8")
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path, roots=("alias/nested",))

    assert [(finding.path, finding.signature) for finding in findings] == [("alias/nested", "policy_error")]


def test_scanner_checks_directory_symlink_before_a_following_parent_component(tmp_path: Path) -> None:
    (tmp_path / "real/deep").mkdir(parents=True)
    target = tmp_path / "real/target"
    target.mkdir()
    (target / "offender.py").write_text("WorklineInbox\n", encoding="utf-8")
    (tmp_path / "alias").symlink_to(tmp_path / "real/deep", target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path, roots=("alias/../target",))

    assert [(finding.path, finding.signature) for finding in findings] == [("alias/../target", "policy_error")]


def test_scanner_rejects_broken_symlink_used_as_explicit_root(tmp_path: Path) -> None:
    (tmp_path / "broken_root").symlink_to(tmp_path / "missing", target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path, roots=("broken_root",))

    assert [(finding.path, finding.signature) for finding in findings] == [("broken_root", "policy_error")]


def test_default_scan_rejects_broken_symlink_root(tmp_path: Path) -> None:
    (tmp_path / "src").symlink_to(tmp_path / "missing", target_is_directory=True)

    findings = find_legacy_references(repo_root=tmp_path)

    assert [(finding.path, finding.signature) for finding in findings] == [("src", "policy_error")]


def test_default_scan_rejects_directory_symlink_root_before_traversal(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "legacy.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src").symlink_to(outside, target_is_directory=True)

    findings = find_legacy_references(repo_root=repo_root)

    assert [(finding.path, finding.signature) for finding in findings] == [("src", "policy_error")]


def test_missing_optional_explicit_scan_root_is_an_empty_surface(tmp_path: Path) -> None:
    assert find_legacy_references(repo_root=tmp_path, roots=("missing",)) == []


def test_scanner_reads_internal_file_symlink_but_reports_repository_path(tmp_path: Path) -> None:
    shared = tmp_path / "shared/legacy.py"
    shared.parent.mkdir()
    shared.write_text("from somewhere import WorklineInbox", encoding="utf-8")
    linked = tmp_path / "src/linked.py"
    linked.parent.mkdir()
    linked.symlink_to(shared)

    findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.path, finding.signature) for finding in findings] == [("src/linked.py", "legacy_symbol")]


def test_default_scan_reports_python_syntax_errors_but_explicit_fixture_scan_does_not(tmp_path: Path) -> None:
    broken = tmp_path / "src/broken.py"
    broken.parent.mkdir(parents=True)
    broken.write_text("not valid python ???", encoding="utf-8")

    default_findings = find_legacy_references(repo_root=tmp_path)
    explicit_findings = find_legacy_references(repo_root=tmp_path, roots=("src",))

    assert [(finding.path, finding.signature) for finding in default_findings] == [("src/broken.py", "policy_error")]
    assert explicit_findings == []


def test_main_emits_tsv_and_returns_one_for_policy_findings(tmp_path: Path, capsys) -> None:
    offender = tmp_path / "src/offender.py"
    offender.parent.mkdir()
    offender.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    status = main(["--format", "tsv"], repo_root=tmp_path)

    output = capsys.readouterr().out.splitlines()
    assert status == 1
    assert len(output) == 1
    assert all(len(line.split("\t")) == 3 for line in output)


def test_cli_process_exits_one_for_policy_findings(tmp_path: Path) -> None:
    script = tmp_path / "scripts/workline_inbox_retirement_guardrail.py"
    script.parent.mkdir()
    shutil.copy(REPO_ROOT / "scripts/workline_inbox_retirement_guardrail.py", script)
    offender = tmp_path / "src/offender.py"
    offender.parent.mkdir()
    offender.write_text("from somewhere import WorklineInbox", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script), "--format", "tsv"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert len(completed.stdout.splitlines()) == 1
    assert completed.stderr == ""


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
