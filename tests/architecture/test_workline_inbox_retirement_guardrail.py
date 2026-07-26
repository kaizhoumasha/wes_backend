"""WorklineInbox 退役终态 guardrail。"""

import shutil
import subprocess
import sys
from pathlib import Path

from scripts.workline_inbox_retirement_guardrail import (
    ALLOWED_EVIDENCE,
    CURRENT_DOC_FILES,
    find_legacy_references,
    main,
)
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
MACHINE_GATE_TEXT_SUFFIXES = frozenset({".py", ".sh", ".toml", ".yaml", ".yml"})


def _find_legacy_references(*, repo_root: Path, roots: tuple[str, ...]) -> list[str]:
    return sorted({finding.path for finding in find_legacy_references(repo_root=repo_root, roots=roots)})


def _find_runtime_inbox_plan_gate_references(repo_root: Path) -> list[str]:
    candidates: set[Path] = set()
    for root in ("scripts", "tests", ".github"):
        root_path = repo_root / root
        if root_path.is_dir():
            candidates.update(
                path for path in root_path.rglob("*") if path.is_file() and path.suffix in MACHINE_GATE_TEXT_SUFFIXES
            )
    candidates.update(path for path in repo_root.glob("Jenkinsfile*") if path.is_file())

    offenders: list[str] = []
    for path in sorted(candidates):
        if path.resolve() == Path(__file__).resolve():
            continue
        source = path.read_text(encoding="utf-8")
        if "docs/superpowers/" in source and "runtime-inbox" in source and ("/plans/" in source or "/specs/" in source):
            offenders.append(str(path.relative_to(repo_root)))
    return offenders


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


def test_runtime_inbox_machine_gates_do_not_read_active_superpowers_plans_or_specs() -> None:
    offenders = _find_runtime_inbox_plan_gate_references(REPO_ROOT)
    assert not offenders, f"RuntimeInbox 机器门禁仍读取历史 plan/spec: {sorted(offenders)}"


def test_runtime_inbox_machine_gate_scanner_covers_shell_and_ci_files(tmp_path: Path) -> None:
    fixtures = {
        "scripts/offender.sh": ("plan=docs/superpowers/plans/2026-07-10-runtime-inbox-single-source-of-truth.md"),
        "Jenkinsfile.backend-ci": (
            "spec='docs/superpowers/specs/2026-07-12-runtime-inbox-acceptance-closure-design.md'"
        ),
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _find_runtime_inbox_plan_gate_references(tmp_path) == sorted(fixtures)


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
        "docs": ("current.md", "WorklineInbox\n", "legacy_symbol"),
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
    for relative_path in CURRENT_DOC_FILES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# current\n", encoding="utf-8")

    findings = find_legacy_references(repo_root=tmp_path)

    assert [(finding.path, finding.signature) for finding in findings] == [("src", "policy_error")]


def test_default_scan_rejects_directory_symlink_root_before_traversal(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "legacy.py").write_text("from somewhere import WorklineInbox", encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "src").symlink_to(outside, target_is_directory=True)
    for relative_path in CURRENT_DOC_FILES:
        path = repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# current\n", encoding="utf-8")

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


def test_main_emits_tsv_and_returns_one_for_policy_findings(tmp_path: Path, capsys) -> None:
    status = main(["--format", "tsv"], repo_root=tmp_path)

    output = capsys.readouterr().out.splitlines()
    assert status == 1
    assert len(output) == len(CURRENT_DOC_FILES)
    assert all(len(line.split("\t")) == 3 for line in output)


def test_cli_process_exits_one_for_policy_findings(tmp_path: Path) -> None:
    script = tmp_path / "scripts/workline_inbox_retirement_guardrail.py"
    script.parent.mkdir()
    shutil.copy(REPO_ROOT / "scripts/workline_inbox_retirement_guardrail.py", script)

    completed = subprocess.run(
        [sys.executable, str(script), "--format", "tsv"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert len(completed.stdout.splitlines()) == len(CURRENT_DOC_FILES)
    assert completed.stderr == ""


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
