import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

from scripts.select_heavy_tests import (
    SelectorError,
    build_parser,
    expand_braces,
    get_changed_files,
    load_config,
    main,
    matches_glob,
    select_heavy_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY_TEST = "tests/integration/test_authoritative_runtime.py"
BASE_REPOSITORY_HOOKS_HEAVY_TEST = "tests/integration/test_base_repository_hooks.py"
CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST = "tests/integration/test_callback_external_payload_limit.py"
CELERY_ASYNC_RUNTIME_HEAVY_TEST = "tests/integration/test_celery_async_runtime.py"
CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST = "tests/integration/test_celery_async_runtime_postgresql.py"
CELERY_PREFORK_HARNESS_CLEANUP_HEAVY_TEST = "tests/integration/test_celery_prefork_harness_cleanup.py"
COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST = "tests/integration/test_command_result_correlation_authority.py"
DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST = "tests/integration/test_device_runtime_projection_writer_service.py"
EFFECT_FRESH_IMPORT_HEAVY_TEST = "tests/integration/test_effect_contract_fresh_import.py"
EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST = "tests/integration/workline_capabilities/test_effect_reducer_postgresql.py"
ECS_MOCK_SERVER_HEAVY_TEST = "tests/mock/test_ecs_mock_server.py"
MOCK_DOCKERFILE_HEAVY_TEST = "tests/mock/test_mock_dockerfile.py"
OPTIMISTIC_LOCK_HEAVY_TEST = "tests/integration/test_optimistic_lock.py"
RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST = "tests/resilience/test_external_http_effect_crash_matrix_postgresql.py"
RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST = "tests/integration/test_external_http_transport_attempt_postgresql.py"
RUNTIME_INBOX_CRASH_RECOVERY_HEAVY_TEST = "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py"
RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST = "tests/integration/test_runtime_inbox_consumer_service.py"
RUNTIME_INBOX_PROCESSING_HEAVY_TEST = "tests/integration/test_runtime_inbox_processing_postgresql.py"
RUNTIME_INBOX_SERVICE_INTERNAL_EVENTS_HEAVY_TEST = "tests/integration/test_runtime_inbox_service_internal_events.py"
RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST = "tests/integration/test_runtime_intent_log_effect_repository.py"
RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST = "tests/integration/test_runtime_intent_log_idempotency.py"
RUNTIME_REMAINING_ENTITIES_HEAVY_TEST = "tests/integration/test_runtime_remaining_entities.py"
RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST = "tests/integration/test_runtime_production_closure_contract.py"
RUNTIME_ECS_STATUS_BENCHMARK_HEAVY_TEST = "tests/load/test_ecs_status_command_benchmark.py"
RUNTIME_INTEGRATION_LAB_HEAVY_TEST = "tests/resilience/test_runtime_integration_lab.py"
RUNTIME_PLANE_SNAPSHOT_BENCHMARK_HEAVY_TEST = "tests/load/test_plane_snapshot_benchmark.py"
RUNTIME_SCENARIO_REPLAY_HEAVY_TEST = "tests/resilience/test_runtime_scenario_replay.py"
SYSTEM_OUTBOX_CANONICAL_PAYLOAD_HEAVY_TEST = "tests/integration/test_system_outbox_canonical_payload_postgresql.py"
SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_CORE_HEAVY_TEST = "tests/integration/test_system_outbox_dispatch_concurrency.py"
SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_HEAVY_TEST = (
    "tests/integration/test_system_outbox_dispatch_concurrency_postgresql.py"
)
SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST = "tests/integration/test_system_outbox_repository.py"
WMS_DEPLOYMENT_HEAVY_TEST = "tests/integration/test_wms_deployment_attestation.py"
WMS_CIRCUIT_BREAKER_HEAVY_TEST = "tests/resilience/test_wms_circuit_breaker.py"
WMS_FEASIBILITY_HEAVY_TEST = "tests/integration/test_wms_northbound_feasibility_probe.py"
WMS_MOCK_SERVER_HEAVY_TEST = "tests/mock/test_wms_mock_server.py"
WMS_NORTHBOUND_CONTRACT_HEAVY_TEST = "tests/mock/test_wms_northbound_contract.py"
WMS_POSTGRESQL_HEAVY_TEST = "tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py"
WMS_PROVIDER_COLLECTION_HEAVY_TEST = "tests/integration/test_wms_provider_conformance_collection.py"
WMS_PROVIDER_SIMULATOR_HEAVY_TEST = "tests/mock/test_wms_provider_conformance_simulator.py"
WMS_EVENT_RUNTIME_INBOX_IDEMPOTENCY_HEAVY_TEST = "tests/integration/test_wms_event_runtime_inbox_idempotency.py"
SHARED_FAST_DB_FIXTURE_HEAVY_TESTS = (
    "tests/integration/test_base_repository_crud.py",
    "tests/integration/test_base_repository_hooks.py",
    "tests/integration/test_callback_external_payload_limit.py",
    "tests/integration/test_command_result_correlation_authority.py",
    "tests/integration/test_device_runtime_projection_writer_service.py",
    "tests/integration/test_optimistic_lock.py",
    "tests/integration/test_runtime_inbox_claim_repository.py",
    "tests/integration/test_runtime_inbox_consumer_service.py",
    "tests/integration/test_runtime_inbox_repository_consumers.py",
    "tests/integration/test_runtime_inbox_service_internal_events.py",
    "tests/integration/test_runtime_intent_log_effect_repository.py",
    "tests/integration/test_runtime_intent_log_idempotency.py",
    "tests/integration/test_system_outbox_dispatch_concurrency.py",
    "tests/integration/test_system_outbox_repository.py",
    "tests/integration/test_wms_event_runtime_inbox_idempotency.py",
    "tests/resilience/test_runtime_inbox_failure_state_machine.py",
    "tests/resilience/test_wms_circuit_breaker.py",
)


def _write_mapping(
    tmp_path: Path,
    mappings: list[tuple[str, list[str]]] | None = None,
    *,
    ignore_globs: list[str] | None = None,
) -> Path:
    lines = [f"ignore_globs = {json.dumps(ignore_globs or ['ignored/**', 'tests/**', 'Jenkinsfile'])}"]
    for source_glob, heavy_tests in mappings or []:
        lines.extend(
            [
                "",
                "[[mapping]]",
                f"source_glob = {json.dumps(source_glob)}",
                f"heavy_tests = {json.dumps(heavy_tests)}",
            ]
        )
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mapping_path


def test_selector_module_exists() -> None:
    assert (REPO_ROOT / "scripts/select_heavy_tests.py").is_file()


def test_parser_enforces_scope_base_protocol() -> None:
    parser = build_parser()

    assert parser.parse_args([]).scope == "unstaged"
    assert parser.parse_args(["--scope", "staged"]).scope == "staged"
    assert parser.parse_args(["--base", "origin/develop"]).base == "origin/develop"
    with pytest.raises(SystemExit):
        parser.parse_args(["--scope", "unstaged", "--base", "origin/develop"])


@pytest.mark.parametrize(
    ("scope", "base", "stdouts", "expected_commands", "expected_files"),
    [
        (
            "unstaged",
            None,
            ["src/app.py\n", "src/new.py\n"],
            [
                ["git", "diff", "--no-renames", "--name-only"],
                ["git", "ls-files", "--others", "--exclude-standard"],
            ],
            ["src/app.py", "src/new.py"],
        ),
        (
            "staged",
            None,
            ["main.py\nmigrations/env.py\n"],
            [["git", "diff", "--cached", "--no-renames", "--name-only"]],
            ["main.py", "migrations/env.py"],
        ),
        (
            None,
            "origin/develop",
            ["tests/integration/test_foo.py\n"],
            [["git", "diff", "--no-renames", "--name-only", "origin/develop...HEAD"]],
            ["tests/integration/test_foo.py"],
        ),
        (
            "unstaged",
            None,
            ["", ""],
            [
                ["git", "diff", "--no-renames", "--name-only"],
                ["git", "ls-files", "--others", "--exclude-standard"],
            ],
            [],
        ),
    ],
)
def test_get_changed_files_uses_expected_git_diff(
    tmp_path: Path,
    scope: str | None,
    base: str | None,
    stdouts: list[str],
    expected_commands: list[list[str]],
    expected_files: list[str],
) -> None:
    runner = Mock(
        side_effect=[
            subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
            for command, stdout in zip(expected_commands, stdouts, strict=True)
        ]
    )

    changed_files = get_changed_files(scope=scope, base=base, repo_root=tmp_path, runner=runner)

    assert changed_files == expected_files
    assert runner.call_args_list == [
        call(command, cwd=tmp_path, check=True, capture_output=True, text=True) for command in expected_commands
    ]


def test_get_changed_files_reports_both_paths_for_staged_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Git hook 会注入当前仓库位置和索引；临时仓库必须使用自己的 Git 状态。
    for variable in ("GIT_COMMON_DIR", "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        monkeypatch.delenv(variable, raising=False)
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run([git, "config", "user.email", "selector@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run([git, "config", "user.name", "HEAVY Selector Test"], cwd=tmp_path, check=True)

    source_path = tmp_path / "src" / "runtime.py"
    source_path.parent.mkdir()
    source_path.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run([git, "add", "src/runtime.py"], cwd=tmp_path, check=True)
    subprocess.run([git, "commit", "-qm", "initial"], cwd=tmp_path, check=True)

    destination_path = tmp_path / "docs" / "runtime.md"
    destination_path.parent.mkdir()
    source_path.rename(destination_path)
    subprocess.run([git, "add", "-A"], cwd=tmp_path, check=True)

    assert get_changed_files(scope="staged", base=None, repo_root=tmp_path) == [
        "docs/runtime.md",
        "src/runtime.py",
    ]


def test_git_changed_paths_preserve_noncanonical_input_for_validation(tmp_path: Path) -> None:
    unsafe_path = "tests/integration/test_x.py\x1f"
    runner = Mock(
        side_effect=[
            subprocess.CompletedProcess(["git", "diff"], 0, stdout=f"{unsafe_path}\n", stderr=""),
            subprocess.CompletedProcess(["git", "ls-files"], 0, stdout="", stderr=""),
        ]
    )

    changed_files = get_changed_files(scope="unstaged", base=None, repo_root=tmp_path, runner=runner)

    assert changed_files == [unsafe_path]
    with pytest.raises(SelectorError, match=r"changed path.*仓库相对路径"):
        select_heavy_tests(changed_files, load_config(_write_mapping(tmp_path)))


def test_direct_heavy_test_selects_itself(tmp_path: Path) -> None:
    config = load_config(_write_mapping(tmp_path))

    assert select_heavy_tests(["tests/integration/test_foo.py"], config) == ["tests/integration/test_foo.py"]


def test_manual_redis_drill_uses_repository_explicit_none_mapping() -> None:
    manual_drill = "scripts/manual/redis_degradation_drill.py"
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([manual_drill], config) == []
    matching_mappings = [mapping for mapping in config[1] if mapping.source_glob == manual_drill]
    assert len(matching_mappings) == 1
    assert matching_mappings[0].heavy_tests == ()


def test_moved_core_heavy_tests_select_themselves() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    changed_tests = [
        BASE_REPOSITORY_HOOKS_HEAVY_TEST,
        COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,
        DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST,
    ]

    assert select_heavy_tests(changed_tests, config) == sorted(changed_tests)


@pytest.mark.parametrize(
    "changed_path",
    [
        "tests/integration/conftest.py",
        "tests/mock/ecs_mock_catalog.py",
        "tests/fixtures/orders.json",
        "tests/conftest.py",
        "tests/runtime/conftest.py",
        "tests/support/runtime_factory.py",
        "main.py",
        "migrations/env.py",
        "alembic.ini",
    ],
)
def test_candidate_assets_use_mapping(tmp_path: Path, changed_path: str) -> None:
    config = load_config(_write_mapping(tmp_path, [(changed_path, [HEAVY_TEST])]))

    assert select_heavy_tests([changed_path], config) == [HEAVY_TEST]


@pytest.mark.parametrize(
    "changed_path",
    [
        "Jenkinsfile",
        ".githooks/pre-commit",
        "tests/runtime/test_service.py",
        "tests/contracts/test_contract.py",
        "tests/api/test_route.py",
    ],
)
def test_ignored_paths_select_nothing(tmp_path: Path, changed_path: str) -> None:
    config = load_config(
        _write_mapping(
            tmp_path,
            ignore_globs=["ignored/**", "tests/**", "Jenkinsfile", ".githooks/**"],
        )
    )

    assert select_heavy_tests([changed_path], config) == []


def test_human_document_candidate_is_excluded_before_heavy_mapping(tmp_path: Path) -> None:
    config = load_config(_write_mapping(tmp_path, ignore_globs=["**/*.md"]))

    assert select_heavy_tests(["tests/integration/architecture-notes.md"], config) == []


def test_human_diagram_is_excluded_from_heavy_selection(tmp_path: Path) -> None:
    config = load_config(_write_mapping(tmp_path))

    assert select_heavy_tests(["docs/system-architecture.eddx"], config) == []


@pytest.mark.parametrize(
    "changed_path",
    [
        ".claude/skills/wes-module-creator-1.0.0/scripts/requirements.txt",
        "src/runtime/contract.txt",
        "scripts/input.txt",
        "tests/integration/fixture.txt",
        "docs/runtime/README.toml",
    ],
)
def test_machine_readable_text_and_config_assets_fail_closed(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    with pytest.raises(SelectorError, match=r"未分类|未配置 mapping/NONE"):
        select_heavy_tests([changed_path], config)


@pytest.mark.parametrize(
    "changed_path",
    [
        "tests/fixtures/workline_contract/rough_sorter/new.json",
        "tests/fixtures/workline_contract/start_admission/new.json",
        "tests/support/smt_sorting_inbound_postgresql.py",
        "tests/support/wms_conveyor_batch_postgresql.py",
        "tests/support/wms_full_box_exchange_postgresql.py",
        "tests/mock/device_simulator.py",
    ],
)
def test_retired_plugin_assets_cannot_reenter_core_as_explicit_none(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    with pytest.raises(SelectorError, match=r"未分类|未配置 mapping/NONE"):
        select_heavy_tests([changed_path], config)


def test_historical_directory_does_not_ignore_future_executable_files() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    with pytest.raises(SelectorError, match="未分类"):
        select_heavy_tests([".superpowers/sdd/tool.py"], config)


def test_unknown_path_fails_closed(tmp_path: Path) -> None:
    config = load_config(_write_mapping(tmp_path))

    with pytest.raises(SelectorError, match="未分类"):
        select_heavy_tests(["tools/release.py"], config)


@pytest.mark.parametrize(
    "changed_path",
    [
        "./tests/integration/test_x.py",
        "tests//integration/test_x.py",
        "tests/integration/./test_x.py",
        "tests/integration/../e2e/test_x.py",
        "tests/integration/test_x.py/",
        "tests/integration/test_\x01.py",
        "tests\\integration\\test_x.py",
        "/tests/integration/test_x.py",
    ],
)
def test_changed_path_validation_rejects_noncanonical_or_unsafe_paths(tmp_path: Path, changed_path: str) -> None:
    config = load_config(_write_mapping(tmp_path))

    with pytest.raises(SelectorError, match=r"changed path.*仓库相对路径"):
        select_heavy_tests([changed_path], config)


@pytest.mark.parametrize(
    "source_glob",
    [
        "./src/**",
        "src//**",
        "src/./**",
        "src/../tests/**",
        "src/\x01/**",
        "src\\**",
        "/src/**",
    ],
)
def test_schema_rejects_noncanonical_source_globs(tmp_path: Path, source_glob: str) -> None:
    mapping_path = _write_mapping(tmp_path, [(source_glob, [HEAVY_TEST])])

    with pytest.raises(SelectorError, match=r"mapping\.source_glob.*仓库相对路径"):
        load_config(mapping_path)


@pytest.mark.parametrize(
    "heavy_test",
    [
        "./tests/integration/test_x.py",
        "tests//integration/test_x.py",
        "tests/integration/./test_x.py",
        "tests/integration/../e2e/test_x.py",
        "tests/integration/test_x.py/",
        "tests/integration/test_\x01.py",
        "tests\\integration\\test_x.py",
        "/tests/integration/test_x.py",
        "tests/integration/test_*.py",
    ],
)
def test_schema_rejects_noncanonical_heavy_test_paths(tmp_path: Path, heavy_test: str) -> None:
    mapping_path = _write_mapping(tmp_path, [("src/**", [heavy_test])])

    with pytest.raises(SelectorError, match=r"mapping\.heavy_tests.*仓库相对路径"):
        load_config(mapping_path)


@pytest.mark.parametrize("source_glob", ["src/*.py", "src/{runtime,worker}.py"])
def test_reviewed_none_rejects_non_exact_source_glob(tmp_path: Path, source_glob: str) -> None:
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text(
        "\n".join(
            [
                'ignore_globs = ["ignored/**"]',
                "",
                "[[mapping]]",
                f"source_glob = {json.dumps(source_glob)}",
                "heavy_tests = []",
                f"reviewed_content_sha256 = {json.dumps('0' * 64)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SelectorError, match=r"reviewed_content_sha256.*精确"):
        load_config(mapping_path)


def test_reviewed_mapping_selects_heavy_tests_only_while_content_matches(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "runtime.py"
    source_path.parent.mkdir()
    source_path.write_text("BEHAVIOR = 'reviewed'\n", encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text(
        "\n".join(
            [
                'ignore_globs = ["ignored/**"]',
                "",
                "[[mapping]]",
                'source_glob = "src/runtime.py"',
                f"heavy_tests = {json.dumps([HEAVY_TEST])}",
                f"reviewed_content_sha256 = {json.dumps(digest)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_config(mapping_path)

    assert select_heavy_tests(["src/runtime.py"], config, repo_root=tmp_path) == [HEAVY_TEST]

    source_path.write_text("BEHAVIOR = 'changed'\n", encoding="utf-8")
    with pytest.raises(SelectorError, match="reviewed mapping 内容指纹不匹配"):
        select_heavy_tests(["src/runtime.py"], config, repo_root=tmp_path)


@pytest.mark.parametrize("digest", ["0" * 63, "G" * 64, "A" * 64])
def test_reviewed_none_rejects_invalid_sha256(tmp_path: Path, digest: str) -> None:
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text(
        "\n".join(
            [
                'ignore_globs = ["ignored/**"]',
                "",
                "[[mapping]]",
                'source_glob = "src/runtime.py"',
                "heavy_tests = []",
                f"reviewed_content_sha256 = {json.dumps(digest)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SelectorError, match=r"reviewed_content_sha256.*SHA-256"):
        load_config(mapping_path)


def test_reviewed_none_requires_matching_current_content(tmp_path: Path) -> None:
    source_path = tmp_path / "src" / "runtime.py"
    source_path.parent.mkdir()
    source_path.write_text('"""Reviewed documentation-only change."""\n', encoding="utf-8")
    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    mapping_path = tmp_path / "heavy-test-impact.toml"
    mapping_path.write_text(
        "\n".join(
            [
                'ignore_globs = ["ignored/**"]',
                "",
                "[[mapping]]",
                'source_glob = "src/runtime.py"',
                "heavy_tests = []",
                f"reviewed_content_sha256 = {json.dumps(digest)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = load_config(mapping_path)

    assert select_heavy_tests(["src/runtime.py"], config, repo_root=tmp_path) == []

    source_path.write_text("BEHAVIOR = 'changed'\n", encoding="utf-8")
    with pytest.raises(SelectorError, match="reviewed mapping 内容指纹不匹配"):
        select_heavy_tests(["src/runtime.py"], config, repo_root=tmp_path)


def test_mapping_union_and_explicit_none(tmp_path: Path) -> None:
    second_heavy_test = "tests/e2e/test_authoritative_runtime.py"
    duplicate_mapping = load_config(
        _write_mapping(
            tmp_path,
            [
                ("src/{app,worker}/**", [HEAVY_TEST, second_heavy_test]),
                ("src/app/**", [second_heavy_test, HEAVY_TEST, HEAVY_TEST]),
            ],
        )
    )
    none_mapping = load_config(_write_mapping(tmp_path, [("pyproject.toml", [])]))

    assert select_heavy_tests(["src/app/service.py"], duplicate_mapping) == [
        second_heavy_test,
        HEAVY_TEST,
    ]
    assert select_heavy_tests(["pyproject.toml"], none_mapping) == []


def test_invalid_heavy_output_fails_closed(tmp_path: Path) -> None:
    mapping_path = _write_mapping(tmp_path, [("src/**", ["tests/unit/test_runtime.py"])])

    with pytest.raises(SelectorError, match="HEAVY 测试路径"):
        load_config(mapping_path)


def test_braces_and_double_star_zero_depth() -> None:
    assert expand_braces("tests/{integration,e2e}/**/test_*.py") == [
        "tests/integration/**/test_*.py",
        "tests/e2e/**/test_*.py",
    ]
    assert matches_glob("tests/integration/test_foo.py", "tests/{integration,e2e}/**/test_*.py")
    assert matches_glob("tests/integration/runtime/test_foo.py", "tests/{integration,e2e}/**/test_*.py")


def test_conflicting_overlapping_mappings_are_rejected(tmp_path: Path) -> None:
    mapping_path = _write_mapping(
        tmp_path,
        [
            ("src/**", ["tests/integration/test_all.py"]),
            ("src/app/**", ["tests/integration/test_app.py"]),
        ],
    )

    with pytest.raises(SelectorError, match="歧义"):
        load_config(mapping_path)

    wildcard_intersection = _write_mapping(
        tmp_path,
        [
            ("src/a*", ["tests/integration/test_prefix.py"]),
            ("src/*b", ["tests/integration/test_suffix.py"]),
        ],
    )
    with pytest.raises(SelectorError, match="歧义"):
        load_config(wildcard_intersection)


def test_caret_character_class_overlap_matches_purepath_semantics(tmp_path: Path) -> None:
    assert matches_glob("src/a", "src/[^a]")
    mapping_path = _write_mapping(
        tmp_path,
        [
            ("src/[^a]", ["tests/integration/test_caret_class.py"]),
            ("src/a", ["tests/integration/test_literal.py"]),
        ],
    )

    with pytest.raises(SelectorError, match="歧义"):
        load_config(mapping_path)


def test_negated_character_class_with_unicode_overlap_fails_closed(tmp_path: Path) -> None:
    assert matches_glob("src/α", "src/[! -~]")
    assert matches_glob("src/α", "src/?")
    mapping_path = _write_mapping(
        tmp_path,
        [
            ("src/[! -~]", ["tests/integration/test_non_ascii.py"]),
            ("src/?", ["tests/integration/test_any_character.py"]),
        ],
    )

    with pytest.raises(SelectorError, match="不支持的 glob 字符类"):
        load_config(mapping_path)


def test_unsupported_character_class_schema_fails_closed(tmp_path: Path) -> None:
    mapping_path = _write_mapping(tmp_path, [("src/[]]", [HEAVY_TEST])])

    with pytest.raises(SelectorError, match="不支持的 glob 字符类"):
        load_config(mapping_path)


def test_git_diff_failure_fails_closed(tmp_path: Path) -> None:
    runner = Mock(side_effect=subprocess.CalledProcessError(128, ["git", "diff"], stderr="bad ref"))

    with pytest.raises(SelectorError, match="git diff 失败"):
        get_changed_files(scope=None, base="origin/missing", repo_root=tmp_path, runner=runner)


def test_get_changed_files_rejects_option_like_base_ref(tmp_path: Path) -> None:
    runner = Mock()

    with pytest.raises(SelectorError, match="base ref"):
        get_changed_files(scope=None, base="--line-prefix=IGNORED", repo_root=tmp_path, runner=runner)

    runner.assert_not_called()


def test_main_prints_one_test_per_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for relative_path in ("tests/integration/test_z.py", "tests/e2e/test_a.py"):
        heavy_test_path = tmp_path / relative_path
        heavy_test_path.parent.mkdir(parents=True, exist_ok=True)
        heavy_test_path.touch()
    mapping_path = _write_mapping(
        tmp_path,
        [("src/**", ["tests/integration/test_z.py", "tests/e2e/test_a.py"])],
    )
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            ["git", "diff"],
            0,
            stdout="src/runtime.py\n",
            stderr="",
        )
    )

    exit_code = main(
        ["--scope", "unstaged"],
        repo_root=tmp_path,
        mapping_path=mapping_path,
        runner=runner,
    )

    assert exit_code == 0
    assert capsys.readouterr().out == "tests/e2e/test_a.py\ntests/integration/test_z.py\n"


def test_repository_mapping_keeps_unaccepted_candidates_unmapped() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    for candidate in (
        "src/app/runtime.py",
        "main.py",
        "migrations/env.py",
        "alembic.ini",
        "docker-compose.yml",
        "tests/integration/conftest.py",
        "tests/fixtures/orders.json",
        "tests/runtime/conftest.py",
        "tests/support/runtime_factory.py",
    ):
        with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
            select_heavy_tests([candidate], config)


def test_repository_mapping_selects_shared_fast_database_fixture_consumers() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["tests/conftest.py"], config) == list(SHARED_FAST_DB_FIXTURE_HEAVY_TESTS)


def test_repository_mapping_declares_required_ignore_globs() -> None:
    ignore_globs, mappings = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert {
        "tests/**",
        "Jenkinsfile.test-deploy",
        ".githooks/**",
        ".github/**",
        ".gitlab-ci.yml",
        ".gitignore",
    }.issubset(ignore_globs)
    assert tuple((mapping.source_glob, mapping.heavy_tests) for mapping in mappings) == (
        ("scripts/select_heavy_tests.py", ()),
        ("scripts/manual/redis_degradation_drill.py", ()),
        ("docs/architecture/heavy-test-impact.toml", ()),
        ("Jenkinsfile.backend-ci", (COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,)),
        (".dockerignore", (COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,)),
        (
            ".env.dev",
            (WMS_FEASIBILITY_HEAVY_TEST, WMS_MOCK_SERVER_HEAVY_TEST, WMS_NORTHBOUND_CONTRACT_HEAVY_TEST),
        ),
        (
            ".env.test",
            (WMS_FEASIBILITY_HEAVY_TEST, WMS_MOCK_SERVER_HEAVY_TEST, WMS_NORTHBOUND_CONTRACT_HEAVY_TEST),
        ),
        (
            ".env.prod",
            (WMS_FEASIBILITY_HEAVY_TEST, WMS_MOCK_SERVER_HEAVY_TEST, WMS_NORTHBOUND_CONTRACT_HEAVY_TEST),
        ),
        ("docker-compose.ci-heavy.yml", (COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,)),
        ("scripts/git-quality-gate.sh", ()),
        ("scripts/markdownlint.sh", ()),
        ("pyproject.toml", ()),
        (
            "scripts/{architecture-guardrails.sh,check_business_legacy_absence_gate.py,check_fast_test_budget.py,generate_legacy_matrix.py,run_selected_heavy_tests.py,test_live_suite.sh,workline_inbox_retirement_guardrail.py}",
            (),
        ),
        ("scripts/check_wms_deployment_attestation.py", (WMS_DEPLOYMENT_HEAVY_TEST,)),
        ("src/app/wms_integration/deployment_attestation.py", (WMS_DEPLOYMENT_HEAVY_TEST,)),
        ("scripts/check_runtime_production_e2e_gate.py", (RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,)),
        ("scripts/run_runtime_benchmarks.py", (RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,)),
        (
            "tests/load/runtime_benchmark_scenarios.py",
            (
                RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,
                RUNTIME_ECS_STATUS_BENCHMARK_HEAVY_TEST,
                RUNTIME_PLANE_SNAPSHOT_BENCHMARK_HEAVY_TEST,
            ),
        ),
        ("tests/load/fixtures/runtime_benchmark_artifact.json", (RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,)),
        ("src/app/runtime/orchestration/p0_e2e_gate.py", (RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,)),
        (
            "src/app/runtime/orchestration/scenario_replay.py",
            (
                RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,
                RUNTIME_INTEGRATION_LAB_HEAVY_TEST,
                RUNTIME_SCENARIO_REPLAY_HEAVY_TEST,
            ),
        ),
        ("src/app/runtime/orchestration/benchmark_gate.py", (RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,)),
        (
            "src/app/runtime/orchestration/effect_state_contract.py",
            (EFFECT_FRESH_IMPORT_HEAVY_TEST, EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST),
        ),
        (
            "src/app/runtime/orchestration/effect_bridges.py",
            (
                EFFECT_FRESH_IMPORT_HEAVY_TEST,
                RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST,
                EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST,
            ),
        ),
        (
            "src/celery_app/async_runtime.py",
            (
                CELERY_ASYNC_RUNTIME_HEAVY_TEST,
                CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST,
                CELERY_PREFORK_HARNESS_CLEANUP_HEAVY_TEST,
            ),
        ),
        ("tests/conftest.py", SHARED_FAST_DB_FIXTURE_HEAVY_TESTS),
        ("src/app/callback/v1/callback.py", (CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST,)),
        (
            "src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py",
            (
                CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST,
                RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST,
                WMS_EVENT_RUNTIME_INBOX_IDEMPOTENCY_HEAVY_TEST,
            ),
        ),
        (
            "src/app/runtime/orchestration/repositories/device_runtime_projection_repository.py",
            (DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST,),
        ),
        (
            "src/app/runtime/orchestration/services/device_runtime_projection_writer_service.py",
            (DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST,),
        ),
        ("src/app/contracts/__init__.py", ()),
        ("src/app/device/models/command.py", (COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,)),
        ("src/app/device/models/device.py", (DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST,)),
        ("src/app/runtime/orchestration/__init__.py", ()),
        ("src/app/runtime/orchestration/execution_correlation.py", ()),
        ("src/app/runtime/orchestration/execution_session.py", ()),
        ("src/app/runtime/orchestration/enums.py", ()),
        ("src/app/runtime/orchestration/models/session.py", (RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,)),
        ("src/app/runtime/orchestration/models/timeline.py", ()),
        (
            "src/app/sys/dispatch_concurrency.py",
            (
                RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_CORE_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_HEAVY_TEST,
                SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
            ),
        ),
        ("src/core/mixins/optimistic_lock.py", (OPTIMISTIC_LOCK_HEAVY_TEST,)),
        (
            "src/app/runtime/orchestration/services/idempotency_guard.py",
            (RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST, RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST),
        ),
        (
            "src/app/runtime/orchestration/repositories/idempotency_key_repository.py",
            (RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST, RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST),
        ),
        (
            "src/app/runtime/orchestration/idempotency_key.py",
            (
                RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST,
                RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST,
                RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,
            ),
        ),
        (
            "src/app/runtime/orchestration/conveyor_queue_membership.py",
            (RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,),
        ),
        (
            "src/app/runtime/orchestration/execution_work_item.py",
            (
                RUNTIME_INBOX_PROCESSING_HEAVY_TEST,
                RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,
                EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
                RUNTIME_INBOX_CRASH_RECOVERY_HEAVY_TEST,
            ),
        ),
        ("src/app/runtime/orchestration/runtime_hold.py", (RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,)),
        ("src/app/runtime/orchestration/runtime_timeline.py", (RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,)),
        (
            "migrations/versions/20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py",
            (RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,),
        ),
        (
            "tests/support/runtime_binding.py",
            (
                COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,
                RUNTIME_INBOX_PROCESSING_HEAVY_TEST,
                RUNTIME_INBOX_SERVICE_INTERNAL_EVENTS_HEAVY_TEST,
                RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST,
                RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,
                SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST,
                EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
            ),
        ),
        ("tests/support/runtime_inbox_processing_postgresql.py", (RUNTIME_INBOX_PROCESSING_HEAVY_TEST,)),
        (
            "tests/support/external_http.py",
            (
                RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,
                SYSTEM_OUTBOX_CANONICAL_PAYLOAD_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_CORE_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_HEAVY_TEST,
                SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
            ),
        ),
        (
            "tests/api/callback_test_support.py",
            (CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST,),
        ),
        (
            "tests/support/sqlmodel_metadata.py",
            (OPTIMISTIC_LOCK_HEAVY_TEST, RUNTIME_REMAINING_ENTITIES_HEAVY_TEST),
        ),
        ("tests/support/test_suite_topology.py", ()),
        (
            "tests/mock/{Dockerfile,ecs_mock_catalog.py,ecs_mock_server.py}",
            (ECS_MOCK_SERVER_HEAVY_TEST, MOCK_DOCKERFILE_HEAVY_TEST),
        ),
        (
            "src/app/wms_integration/ports/document_operations.py",
            (WMS_FEASIBILITY_HEAVY_TEST, WMS_MOCK_SERVER_HEAVY_TEST, WMS_NORTHBOUND_CONTRACT_HEAVY_TEST),
        ),
        (
            "scripts/verify_wms_northbound_feasibility.py",
            (WMS_FEASIBILITY_HEAVY_TEST,),
        ),
        (
            "tests/support/wms_conformance_runner.py",
            (
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_PROVIDER_COLLECTION_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ),
        ),
        (
            "tests/mock/wms_fixture_matrix.py",
            (
                RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST,
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_PROVIDER_COLLECTION_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ),
        ),
        (
            "tests/contracts/wms_integration/provider_profile_support.py",
            (
                WMS_DEPLOYMENT_HEAVY_TEST,
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_PROVIDER_SIMULATOR_HEAVY_TEST,
            ),
        ),
        (
            "tests/mock/wms_mock_server.py",
            (
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ),
        ),
        (
            "tests/mock/wms_northbound_contract.py",
            (
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ),
        ),
        (
            "tests/mock/wms_operation_fixtures.py",
            (
                RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST,
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ),
        ),
        (
            "migrations/versions/20260527_0105_07be7a97f4a6_add_wms_circuit_breaker_state.py",
            (WMS_CIRCUIT_BREAKER_HEAVY_TEST,),
        ),
        ("src/core/outbound_http/**", ()),
    )


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [
        ("scripts/check_wms_deployment_attestation.py", [WMS_DEPLOYMENT_HEAVY_TEST]),
        ("src/app/wms_integration/deployment_attestation.py", [WMS_DEPLOYMENT_HEAVY_TEST]),
        ("scripts/check_runtime_production_e2e_gate.py", [RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST]),
        ("scripts/run_runtime_benchmarks.py", [RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST]),
        (
            "tests/load/runtime_benchmark_scenarios.py",
            [
                RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,
                RUNTIME_ECS_STATUS_BENCHMARK_HEAVY_TEST,
                RUNTIME_PLANE_SNAPSHOT_BENCHMARK_HEAVY_TEST,
            ],
        ),
        ("tests/load/fixtures/runtime_benchmark_artifact.json", [RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST]),
        ("src/app/runtime/orchestration/p0_e2e_gate.py", [RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST]),
        (
            "src/app/runtime/orchestration/scenario_replay.py",
            [
                RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST,
                RUNTIME_INTEGRATION_LAB_HEAVY_TEST,
                RUNTIME_SCENARIO_REPLAY_HEAVY_TEST,
            ],
        ),
        ("src/app/runtime/orchestration/benchmark_gate.py", [RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST]),
        (
            "src/app/runtime/orchestration/effect_state_contract.py",
            [EFFECT_FRESH_IMPORT_HEAVY_TEST, EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST],
        ),
        (
            "src/app/runtime/orchestration/effect_bridges.py",
            [
                EFFECT_FRESH_IMPORT_HEAVY_TEST,
                RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST,
                EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST,
            ],
        ),
    ],
)
def test_repository_mapping_selects_new_core_heavy_tests(changed_path: str, expected: list[str]) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config) == expected


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [
        (
            "src/celery_app/async_runtime.py",
            [
                CELERY_ASYNC_RUNTIME_HEAVY_TEST,
                CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST,
                CELERY_PREFORK_HARNESS_CLEANUP_HEAVY_TEST,
            ],
        ),
        (
            "src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py",
            [
                CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST,
                RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST,
                WMS_EVENT_RUNTIME_INBOX_IDEMPOTENCY_HEAVY_TEST,
            ],
        ),
        (
            "src/app/runtime/orchestration/repositories/device_runtime_projection_repository.py",
            [DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST],
        ),
        (
            "src/app/runtime/orchestration/services/device_runtime_projection_writer_service.py",
            [DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST],
        ),
        (
            "src/app/sys/dispatch_concurrency.py",
            [
                RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_CORE_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_HEAVY_TEST,
                SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
            ],
        ),
        ("src/core/mixins/optimistic_lock.py", [OPTIMISTIC_LOCK_HEAVY_TEST]),
        (
            "src/app/runtime/orchestration/services/idempotency_guard.py",
            [RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST, RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST],
        ),
        (
            "src/app/runtime/orchestration/repositories/idempotency_key_repository.py",
            [RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST, RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST],
        ),
        (
            "src/app/runtime/orchestration/idempotency_key.py",
            [
                RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST,
                RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST,
                RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,
            ],
        ),
        (
            "src/app/runtime/orchestration/conveyor_queue_membership.py",
            [RUNTIME_REMAINING_ENTITIES_HEAVY_TEST],
        ),
        (
            "src/app/runtime/orchestration/execution_work_item.py",
            [
                RUNTIME_INBOX_PROCESSING_HEAVY_TEST,
                RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,
                EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
                RUNTIME_INBOX_CRASH_RECOVERY_HEAVY_TEST,
            ],
        ),
        ("src/app/runtime/orchestration/runtime_hold.py", [RUNTIME_REMAINING_ENTITIES_HEAVY_TEST]),
        ("src/app/runtime/orchestration/runtime_timeline.py", [RUNTIME_REMAINING_ENTITIES_HEAVY_TEST]),
        (
            "migrations/versions/20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py",
            [RUNTIME_REMAINING_ENTITIES_HEAVY_TEST],
        ),
        (
            "tests/support/runtime_binding.py",
            [
                COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,
                RUNTIME_INBOX_PROCESSING_HEAVY_TEST,
                RUNTIME_INBOX_SERVICE_INTERNAL_EVENTS_HEAVY_TEST,
                RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST,
                RUNTIME_REMAINING_ENTITIES_HEAVY_TEST,
                SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST,
                EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
            ],
        ),
        (
            "tests/support/external_http.py",
            [
                RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST,
                SYSTEM_OUTBOX_CANONICAL_PAYLOAD_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_CORE_HEAVY_TEST,
                SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_HEAVY_TEST,
                SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST,
                RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST,
            ],
        ),
        (
            "tests/api/callback_test_support.py",
            [CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST],
        ),
        (
            "tests/support/sqlmodel_metadata.py",
            [OPTIMISTIC_LOCK_HEAVY_TEST, RUNTIME_REMAINING_ENTITIES_HEAVY_TEST],
        ),
    ],
)
def test_repository_mapping_selects_database_runtime_heavy_consumers(
    changed_path: str,
    expected: list[str],
) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config) == expected


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [
        (
            "scripts/verify_wms_northbound_feasibility.py",
            [WMS_FEASIBILITY_HEAVY_TEST],
        ),
        (
            "tests/support/wms_conformance_runner.py",
            [
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_PROVIDER_COLLECTION_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ],
        ),
        (
            "tests/contracts/wms_integration/provider_profile_support.py",
            [
                WMS_DEPLOYMENT_HEAVY_TEST,
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_PROVIDER_SIMULATOR_HEAVY_TEST,
            ],
        ),
        (
            "tests/mock/wms_mock_server.py",
            [
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ],
        ),
        (
            "tests/mock/wms_northbound_contract.py",
            [
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ],
        ),
        (
            "tests/mock/wms_operation_fixtures.py",
            [
                RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST,
                WMS_FEASIBILITY_HEAVY_TEST,
                WMS_POSTGRESQL_HEAVY_TEST,
                WMS_MOCK_SERVER_HEAVY_TEST,
                WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
            ],
        ),
        (
            "migrations/versions/20260527_0105_07be7a97f4a6_add_wms_circuit_breaker_state.py",
            [WMS_CIRCUIT_BREAKER_HEAVY_TEST],
        ),
    ],
)
def test_repository_mapping_selects_wms_heavy_asset_consumers(changed_path: str, expected: list[str]) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config) == expected


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/callback/services/callback_ingress_service.py",
        "src/app/callback/services/callback_orchestration_service.py",
        "src/celery_app/config.py",
        "src/app/runtime/orchestration/services/device_dispatch_policy.py",
        "src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py",
        "src/app/runtime/orchestration/services/conveyor_queue_writer.py",
        "src/app/reconciliation/manager.py",
        "src/app/wms_integration/state_machine.py",
        "src/app/wms_integration/models/circuit_breaker.py",
        "src/app/wms_integration/repositories/circuit_breaker_repository.py",
        "src/app/wms_integration/services/circuit_breaker_service.py",
        "src/app/workline/models/plane.py",
        "src/utils/timezone.py",
        "src/app/wms_integration/operation_registry.py",
        "src/app/sys/canonical_dispatch.py",
        "src/app/sys/services/outbox_engine.py",
    ],
)
def test_repository_mapping_keeps_broad_transitive_dependencies_fail_closed(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
        select_heavy_tests([changed_path], config)


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/celery_app/app.py",
        "src/app/runtime/system_capabilities/wms/provider_catalog.py",
        "src/app/wms_integration/provider_readiness.py",
        "src/app/wms_integration/effect_lane_runtime.py",
        "src/app/wms_integration/effect_preparation_runtime.py",
        "src/app/wms_integration/query_runtime.py",
        "src/core/exceptions.py",
        "src/core/conf.py",
        "src/core/mixins/__init__.py",
        "src/database/base_repository.py",
        "src/database/db.py",
        "src/database/model_factory.py",
        "src/database/redis_client.py",
        "src/database/schema_conf.py",
        "src/database/sqlite_schema.py",
        "src/app/device/services/device_service.py",
        "src/app/sys/repositories/outbox_repository.py",
        "src/app/sys/models/outbox.py",
        "src/app/runtime/orchestration/device_runtime_projection.py",
        "src/app/runtime/orchestration/repositories/runtime_inbox_repository.py",
        "src/app/runtime/orchestration/repositories/runtime_intent_log_repository.py",
        "src/app/runtime/orchestration/runtime_inbox.py",
        "src/app/runtime/orchestration/runtime_intent_log.py",
        "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py",
    ],
)
def test_repository_mapping_keeps_database_runtime_broad_dependencies_fail_closed(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
        select_heavy_tests([changed_path], config)


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [
        ("src/app/callback/v1/callback.py", [CALLBACK_EXTERNAL_PAYLOAD_LIMIT_HEAVY_TEST]),
        ("src/app/contracts/__init__.py", []),
        ("src/app/device/models/command.py", [COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST]),
        ("src/app/device/models/device.py", [DEVICE_RUNTIME_PROJECTION_WRITER_HEAVY_TEST]),
        ("src/app/runtime/orchestration/models/session.py", [RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST]),
    ],
)
def test_repository_mapping_keeps_core_sources_owned_by_core_heavy_tests(
    changed_path: str,
    expected: list[str],
) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config) == expected


def test_repository_mapping_classifies_selector_implementation_as_quality_only() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["scripts/select_heavy_tests.py"], config) == []


def test_repository_mapping_classifies_release_version_as_no_heavy_impact() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["VERSION"], config) == []


def test_repository_mapping_classifies_local_codex_metadata_as_no_heavy_impact() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([".codex/config.toml", ".codex/settings.json"], config) == []


def test_repository_mapping_keeps_top_level_dockerfile_fail_closed() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    with pytest.raises(SelectorError, match="未配置 mapping/NONE"):
        select_heavy_tests(["Dockerfile"], config)


def test_repository_ci_and_quality_gate_run_selector_contracts() -> None:
    jenkinsfile = (REPO_ROOT / "Jenkinsfile.backend-ci").read_text(encoding="utf-8")
    quality_gate = (REPO_ROOT / "scripts/git-quality-gate.sh").read_text(encoding="utf-8")

    assert "./scripts/git-quality-gate.sh --profile quality" in jenkinsfile
    assert "stage('HEAVY Required')" in jenkinsfile
    assert "git config --global --add safe.directory /app" in jenkinsfile
    assert 'uv run --no-sync scripts/select_heavy_tests.py --base "origin/${CI_TARGET_BRANCH}"' in jenkinsfile
    assert "reports/heavy-tests.txt" in jenkinsfile
    assert "uv run --no-sync scripts/run_selected_heavy_tests.py" in jenkinsfile
    assert "run_script_contract_tests" in quality_gate
    assert "run_tool pytest tests/scripts -q" in quality_gate


def test_repository_mapping_selects_minimal_heavy_for_active_backend_ci() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["Jenkinsfile.backend-ci"], config) == [COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST]
    with pytest.raises(SelectorError, match="未分类改动路径"):
        select_heavy_tests(["Jenkinsfile"], config)


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/runtime/orchestration/__init__.py",
        "src/app/runtime/orchestration/execution_correlation.py",
        "src/app/runtime/orchestration/execution_session.py",
        "src/app/runtime/orchestration/enums.py",
        "src/app/runtime/orchestration/models/timeline.py",
    ],
)
def test_repository_mapping_pins_reviewed_none_to_current_runtime_source_content(
    changed_path: str,
) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    mapping = next(mapping for mapping in config[1] if mapping.source_glob == changed_path)

    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []


def test_repository_mapping_selects_minimal_heavy_for_docker_build_context() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([".dockerignore"], config) == [COMMAND_RESULT_CORRELATION_AUTHORITY_HEAVY_TEST]


@pytest.mark.parametrize("changed_path", [".env.dev", ".env.test", ".env.prod"])
def test_repository_mapping_pins_runtime_profiles_to_current_content(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    mapping = next(mapping for mapping in config[1] if mapping.source_glob == changed_path)

    assert mapping.heavy_tests == (
        WMS_FEASIBILITY_HEAVY_TEST,
        WMS_MOCK_SERVER_HEAVY_TEST,
        WMS_NORTHBOUND_CONTRACT_HEAVY_TEST,
    )
    assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == sorted(mapping.heavy_tests)


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    [
        (
            "src/app/wms_integration/ports/document_operations.py",
            [WMS_FEASIBILITY_HEAVY_TEST, WMS_MOCK_SERVER_HEAVY_TEST, WMS_NORTHBOUND_CONTRACT_HEAVY_TEST],
        ),
        (
            "tests/support/runtime_inbox_processing_postgresql.py",
            [RUNTIME_INBOX_PROCESSING_HEAVY_TEST],
        ),
    ],
)
def test_repository_mapping_selects_shared_wms_and_runtime_support_heavy_owners(
    changed_path: str,
    expected: list[str],
) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config) == expected


def test_quality_gate_does_not_advertise_a_duplicate_full_profile() -> None:
    quality_gate = (REPO_ROOT / "scripts/git-quality-gate.sh").read_text(encoding="utf-8")

    assert "run_full_profile" not in quality_gate
    assert "full      Run the quality profile plus the full pytest suite." not in quality_gate
    assert "full)" not in quality_gate
