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
    filter_deleted_retired_archive_paths,
    get_changed_files,
    is_candidate,
    load_config,
    main,
    matches_glob,
    select_heavy_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY_TEST = "tests/integration/test_authoritative_runtime.py"
BASE_REPOSITORY_HOOKS_HEAVY_TEST = "tests/integration/test_base_repository_hooks.py"
PERMISSION_CATALOG_SYNC_HEAVY_TEST = "tests/integration/test_permission_catalog_sync_postgresql.py"
CELERY_ASYNC_RUNTIME_HEAVY_TEST = "tests/integration/test_celery_async_runtime.py"
CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST = "tests/integration/test_celery_async_runtime_postgresql.py"
CELERY_PREFORK_HARNESS_CLEANUP_HEAVY_TEST = "tests/integration/test_celery_prefork_harness_cleanup.py"
EFFECT_FRESH_IMPORT_HEAVY_TEST = "tests/integration/test_effect_contract_fresh_import.py"
EFFECT_REDUCER_POSTGRESQL_HEAVY_TEST = "tests/integration/workline_capabilities/test_effect_reducer_postgresql.py"
ECS_MOCK_SERVER_HEAVY_TEST = "tests/mock/test_ecs_mock_server.py"
MOCK_DOCKERFILE_HEAVY_TEST = "tests/mock/test_mock_dockerfile.py"
OPTIMISTIC_LOCK_HEAVY_TEST = "tests/integration/test_optimistic_lock.py"
RUNTIME_EXTERNAL_HTTP_EFFECT_CRASH_HEAVY_TEST = "tests/resilience/test_external_http_effect_crash_matrix_postgresql.py"
RUNTIME_EXTERNAL_HTTP_TRANSPORT_HEAVY_TEST = "tests/integration/test_external_http_transport_attempt_postgresql.py"
RUNTIME_INBOX_CONSUMER_SERVICE_HEAVY_TEST = "tests/integration/test_runtime_inbox_consumer_service.py"
RUNTIME_INBOX_MIGRATION_HEAVY_TEST = "tests/integration/test_initial_schema_baseline_postgresql.py"
RUNTIME_INBOX_SERVICE_INTERNAL_EVENTS_HEAVY_TEST = "tests/integration/test_runtime_inbox_service_internal_events.py"
RUNTIME_INTENT_LOG_EFFECT_REPOSITORY_HEAVY_TEST = "tests/integration/test_runtime_intent_log_effect_repository.py"
RUNTIME_INTENT_LOG_IDEMPOTENCY_HEAVY_TEST = "tests/integration/test_runtime_intent_log_idempotency.py"
RUNTIME_REMAINING_ENTITIES_HEAVY_TEST = "tests/integration/test_initial_schema_baseline_postgresql.py"
RUNTIME_PRODUCTION_CLOSURE_HEAVY_TEST = "tests/integration/test_runtime_production_closure_contract.py"
RUNTIME_ECS_STATUS_BENCHMARK_HEAVY_TEST = "tests/load/test_ecs_status_command_benchmark.py"
RUNTIME_INTEGRATION_LAB_HEAVY_TEST = "tests/resilience/test_runtime_integration_lab.py"
RUNTIME_PLANE_SNAPSHOT_BENCHMARK_HEAVY_TEST = "tests/load/test_plane_snapshot_benchmark.py"
RUNTIME_SCENARIO_REPLAY_HEAVY_TEST = "tests/resilience/test_runtime_scenario_replay.py"
WORKLINE_START_POSTGRESQL_HEAVY_TEST = "tests/integration/workline_capabilities/test_workline_start_postgresql.py"
UNFINISHED_EXECUTION_SNAPSHOT_HEAVY_TEST = (
    "tests/integration/workline_capabilities/test_unfinished_execution_snapshot_postgresql.py"
)
SYSTEM_OUTBOX_CANONICAL_PAYLOAD_HEAVY_TEST = "tests/integration/test_system_outbox_canonical_payload_postgresql.py"
SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_CORE_HEAVY_TEST = "tests/integration/test_system_outbox_dispatch_concurrency.py"
SYSTEM_OUTBOX_DISPATCH_CONCURRENCY_HEAVY_TEST = (
    "tests/integration/test_system_outbox_dispatch_concurrency_postgresql.py"
)
SYSTEM_OUTBOX_REPOSITORY_HEAVY_TEST = "tests/integration/test_system_outbox_repository.py"
WMS_DEPLOYMENT_HEAVY_TEST = "tests/integration/test_wms_deployment_attestation.py"
WMS_CIRCUIT_BREAKER_HEAVY_TEST = "tests/resilience/test_wms_circuit_breaker.py"
WMS_FEASIBILITY_HEAVY_TEST = "tests/integration/test_wms_northbound_feasibility_probe.py"
WMS_MOCK_SERVER_HEAVY_TEST = "tests/mock/test_wms_transport_mock_server.py"
WMS_PROVIDER_MOCK_SERVER_HEAVY_TEST = "tests/mock/test_wms_provider_mock_server.py"
WMS_NORTHBOUND_CONTRACT_HEAVY_TEST = "tests/integration/wms_integration/test_northbound_contract.py"
WMS_POSTGRESQL_HEAVY_TEST = "tests/integration/workline_capabilities/test_wms_effect_status_postgresql.py"
WMS_PROVIDER_COLLECTION_HEAVY_TEST = "tests/integration/test_wms_provider_conformance_collection.py"
WMS_PROVIDER_SIMULATOR_HEAVY_TEST = "tests/integration/wms_integration/test_provider_conformance_simulator.py"
TRANSPORT_DARK_LOOP_HEAVY_TEST = "tests/integration/transport/test_dark_transport_loop.py"
TRANSPORT_EVIDENCE_HEAVY_TEST = "tests/integration/transport/test_transport_evidence_transaction.py"
TRANSPORT_CALLBACK_RECEIPT_HEAVY_TEST = "tests/integration/wms_adapter/test_transport_callback_receipts.py"
TRANSPORT_REPOSITORY_HEAVY_TEST = "tests/integration/transport/test_transport_repository.py"
TRANSPORT_SCHEMA_HEAVY_TEST = "tests/integration/transport/test_transport_schema.py"
TRANSPORT_DEBUG_RESET_HEAVY_TEST = "tests/integration/transport/test_transport_debug_reset.py"
RESET_RUNTIME_DATA_HEAVY_TEST = "tests/integration/test_reset_runtime_data_postgresql.py"
INITIAL_SCHEMA_BASELINE_HEAVY_TEST = "tests/integration/test_initial_schema_baseline_postgresql.py"
INITIAL_SCHEMA_REVISION_PATH = "migrations/versions/20260831_1531_f9c7c2e5f501_建立最终初始数据库基线.py"
TRANSPORT_FACE_REVISION_PATH = "migrations/versions/20260901_0642_e0da335c057d_transport_面向扩为字符串_token.py"
TRANSPORT_DEBUG_PROJECTION_REVISION_PATH = (
    "migrations/versions/20260903_0432_ed5ed8eb0c46_增加_transport_联调当前位置投影.py"
)
TRANSPORT_DEBUG_AUTO_RUN_REVISION_PATH = "migrations/versions/20260903_1143_8f3c61e57a90_增加_transport_自动联调轮次.py"
TRANSPORT_DEBUG_AUTO_RUN_HEAVY_TESTS = (
    "tests/integration/test_initial_schema_baseline_postgresql.py",
    "tests/integration/transport/test_transport_debug_auto_run.py",
    "tests/integration/transport/test_transport_debug_run_repository.py",
    "tests/integration/transport/test_transport_debug_run_schema.py",
    "tests/integration/transport/test_transport_debug_run_service.py",
)
TRANSPORT_PRODUCTION_WIRING_E2E_TEST = "tests/e2e/transport/test_transport_production_wiring.py"
TRANSPORT_FASTAPI_LIFESPAN_HEAVY_TEST = "tests/integration/test_transport_fastapi_lifespan.py"
TRANSPORT_BROKER_HARNESS_CLEANUP_HEAVY_TEST = "tests/integration/test_transport_broker_harness_cleanup.py"
TRANSPORT_FULFILLMENT_QUEUE_HEAVY_TEST = "tests/integration/test_transport_fulfillment_queue.py"
DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST = "tests/integration/device_command/test_device_command_constraints.py"
EVENT_COMMAND_BLOCK_MIGRATION_HEAVY_TEST = (
    "tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py"
)
EVENT_COMMAND_BLOCK_RECONCILIATION_HEAVY_TEST = (
    "tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py"
)
DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST = "tests/e2e/device_command/test_device_command_production_wiring.py"
EXECUTION_CONSTRAINTS_HEAVY_TEST = "tests/integration/execution/test_execution_constraints.py"
DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST = "tests/integration/execution/test_decision_processing_postgresql.py"
LINE_RUN_EPOCH_ACTIVATION_POSTGRESQL_HEAVY_TEST = (
    "tests/integration/workline_capabilities/test_line_run_epoch_activation_postgresql.py"
)
WMS_INBOUND_CONFIRMATION_HEAVY_TEST = "tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py"
WMS_RACK_SUPPLY_SCHEMA_HEAVY_TEST = "tests/integration/workline_capabilities/test_wms_rack_supply_schema_postgresql.py"
SHARED_FAST_DB_FIXTURE_HEAVY_TESTS = (
    DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
    "tests/integration/test_base_repository_crud.py",
    "tests/integration/test_base_repository_hooks.py",
    "tests/integration/test_optimistic_lock.py",
    "tests/integration/test_runtime_inbox_claim_repository.py",
    "tests/integration/test_runtime_inbox_consumer_service.py",
    "tests/integration/test_runtime_inbox_repository_consumers.py",
    "tests/integration/test_runtime_inbox_service_internal_events.py",
    "tests/integration/test_runtime_intent_log_effect_repository.py",
    "tests/integration/test_runtime_intent_log_idempotency.py",
    "tests/integration/test_system_outbox_dispatch_concurrency.py",
    "tests/integration/test_system_outbox_repository.py",
    "tests/resilience/test_runtime_inbox_failure_state_machine.py",
    "tests/resilience/test_wms_circuit_breaker.py",
)
PLUGIN_SDK_REVIEWED_NONE_PATHS = (
    "packages/wes_plugin_sdk/pyproject.toml",
    "packages/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py",
    "packages/wes_plugin_sdk/src/wes_plugin_sdk/facts.py",
    "packages/wes_plugin_sdk/src/wes_plugin_sdk/handler.py",
    "packages/wes_plugin_sdk/src/wes_plugin_sdk/protocols.py",
)
RUNTIME_TEXT_REVIEWED_NONE_PATHS = (
    "src/app/runtime/orchestration/services/_text.py",
    "src/app/runtime/orchestration/services/inbox/object_transition_event_service.py",
    "src/app/runtime/orchestration/services/runtime_location_event_service.py",
)
TRACE_REVIEWED_NONE_PATHS = (
    "src/app/callback/contracts/__init__.py",
    "src/app/callback/services/callback_log_service.py",
    "src/app/runtime/orchestration/timeline_generator.py",
    "src/app/runtime/orchestration/trace_context.py",
    "src/app/workline/outbox_dispatch_support.py",
)
VALUE_NORMALIZATION_REVIEWED_NONE_PATHS = (
    "src/app/resource/services/projection_service.py",
    "src/app/resource/services/relation_service.py",
    "src/app/sys/services/audit_service.py",
)
SYSTEM_CAPABILITY_IDENTITY_REVIEWED_NONE_PATHS = (
    "src/app/runtime/extension_identity.py",
    "src/app/runtime/system_capabilities/definition.py",
    "src/app/runtime/system_capabilities/gateway.py",
    "src/app/runtime/system_capabilities/index_builder.py",
)
RETIRED_RUNTIME_MIRROR_PATHS = (
    "src/app/callback/utils.py",
    "src/app/callback/contracts/builder.py",
    "src/app/callback/contracts/codes.py",
    "src/app/callback/contracts/diagnostics.py",
    "src/app/callback/contracts/failure_mapper.py",
    "src/app/callback/contracts/models.py",
    "src/app/callback/contracts/registry.py",
    "src/app/callback/contracts/timeline_generator.py",
    "src/app/callback/contracts/trace_context.py",
    "src/app/workline/trace_context.py",
    "src/app/runtime/orchestration/diagnostics.py",
    "src/app/runtime/orchestration/diagnostics/__init__.py",
    "src/app/runtime/orchestration/diagnostics/builder.py",
    "src/app/runtime/orchestration/diagnostics/codes.py",
    "src/app/runtime/orchestration/diagnostics/failure_mapper.py",
    "src/app/runtime/orchestration/diagnostics/models.py",
    "src/app/runtime/orchestration/diagnostics/registry.py",
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


def test_admin_router_menu_removal_is_exact_reviewed_none() -> None:
    admin_router = "src/app/admin/__init__.py"
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    matching_mappings = [mapping for mapping in config[1] if mapping.source_glob == admin_router]
    assert len(matching_mappings) == 1
    mapping = matching_mappings[0]
    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / admin_router).read_bytes()).hexdigest()
    assert select_heavy_tests([admin_router], config, repo_root=REPO_ROOT) == []


def test_role_schema_validation_is_exact_reviewed_none() -> None:
    role_model = "src/app/admin/models/role.py"
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    matching_mappings = [mapping for mapping in config[1] if mapping.source_glob == role_model]
    assert len(matching_mappings) == 1
    mapping = matching_mappings[0]
    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / role_model).read_bytes()).hexdigest()
    assert select_heavy_tests([role_model], config, repo_root=REPO_ROOT) == []


def test_relationship_metadata_selects_both_postgresql_owners() -> None:
    relationships = "src/app/admin/models/relationships.py"
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([relationships], config, repo_root=REPO_ROOT) == [
        "tests/integration/test_authorization_bootstrap_postgresql.py",
        "tests/integration/test_initial_schema_baseline_postgresql.py",
    ]


@pytest.mark.parametrize(
    ("changed_path", "expected_sha256"),
    [
        (
            "src/app/admin/repositories/__init__.py",
            "c3c4cbc8c92ac34018ad509b05fbd3f107a212fc0367d01a6b7058b0966b9e25",
        ),
        (
            "src/app/auth/models/auth.py",
            "b10d2f7ea5c21d8a7994f1ea8b9d73a98da056c4b0f9cf8ec6662d6971693fea",
        ),
        (
            "src/app/auth/v1/auth.py",
            "1e84e15cb9b031cdcfc76d3f1560cdfc8b86a995ed7f42590cedd5a2a3403fa4",
        ),
    ],
)
def test_menu_runtime_removal_paths_are_exact_reviewed_none(changed_path: str, expected_sha256: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    matching_mappings = [mapping for mapping in config[1] if mapping.source_glob == changed_path]
    assert len(matching_mappings) == 1
    mapping = matching_mappings[0]
    assert mapping.heavy_tests == ()
    assert mapping.reviewed_content_sha256 == expected_sha256
    assert expected_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []


@pytest.mark.parametrize(
    "retired_path",
    [
        "scripts/data/sync_menus.py",
        "scripts/data/sync_menus.sh",
        "src/app/admin/models/menu.py",
        "src/app/admin/repositories/menu_repository.py",
        "src/app/admin/services/menu_service.py",
        "src/app/admin/services/menu_sync_service.py",
        "src/app/admin/v1/menu.py",
        "src/utils/frontend_menu_parser.py",
    ],
)
def test_deleted_menu_runtime_assets_are_retired_only_while_absent(tmp_path: Path, retired_path: str) -> None:
    assert filter_deleted_retired_archive_paths([retired_path], repo_root=tmp_path) == []

    restored = tmp_path / retired_path
    restored.parent.mkdir(parents=True, exist_ok=True)
    restored.touch()
    assert filter_deleted_retired_archive_paths([retired_path], repo_root=tmp_path) == [retired_path]


def test_moved_core_heavy_tests_select_themselves() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    changed_tests = [
        BASE_REPOSITORY_HOOKS_HEAVY_TEST,
        DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
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


@pytest.mark.parametrize(
    "changed_path",
    [
        "workline_plugins/rough_sorter/src/rough_sorter/handler.py",
        "workline_plugins/rough_sorter/tests/test_handler.py",
        "workline_plugins/rough_sorter/tests/e2e/test_inbound_flow.py",
    ],
)
def test_plugin_package_assets_do_not_select_core_heavy_tests(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config) == []


def test_unmapped_plugin_sdk_asset_is_a_core_candidate_and_fails_closed() -> None:
    changed_path = "packages/wes_plugin_sdk/src/wes_plugin_sdk/decision.py"
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert is_candidate(changed_path)
    with pytest.raises(SelectorError, match="候选路径未配置 mapping/NONE"):
        select_heavy_tests([changed_path], config)


def test_deployment_start_composition_is_candidate_with_explicit_heavy_owners() -> None:
    changed_path = "deployment/rough_sorter_composition.py"
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert is_candidate(changed_path)
    assert select_heavy_tests([changed_path], config) == [
        "tests/e2e/transport/test_transport_production_wiring.py",
        "tests/integration/execution/test_decision_processing_postgresql.py",
        "tests/integration/test_celery_async_runtime_postgresql.py",
        WORKLINE_START_POSTGRESQL_HEAVY_TEST,
    ]


def test_retired_development_wms_provider_profile_is_exact_none() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    changed_path = "deployment/dev/wms-provider.yaml"
    matching = [mapping for mapping in config[1] if mapping.source_glob == changed_path]

    assert not (REPO_ROOT / changed_path).exists()
    assert len(matching) == 1
    assert matching[0].heavy_tests == ()
    assert select_heavy_tests([changed_path], config) == []


def test_plugin_sdk_assets_are_exact_reviewed_none_mappings() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    for changed_path in PLUGIN_SDK_REVIEWED_NONE_PATHS:
        matching_mappings = [mapping for mapping in config[1] if mapping.source_glob == changed_path]
        assert len(matching_mappings) == 1
        mapping = matching_mappings[0]
        assert mapping.heavy_tests == ()
        assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
        assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []


def test_plugin_sdk_transport_decisions_select_real_transport_and_execution_owners() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["packages/wes_plugin_sdk/src/wes_plugin_sdk/decisions.py"], config) == [
        TRANSPORT_PRODUCTION_WIRING_E2E_TEST,
        DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST,
    ]
    assert select_heavy_tests(["packages/wes_plugin_sdk/src/wes_plugin_sdk/validation.py"], config) == [
        TRANSPORT_PRODUCTION_WIRING_E2E_TEST,
        DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST,
        WMS_MOCK_SERVER_HEAVY_TEST,
    ]


def test_runtime_text_refactor_is_exact_reviewed_none() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    for changed_path in RUNTIME_TEXT_REVIEWED_NONE_PATHS:
        matching_mappings = [mapping for mapping in config[1] if mapping.source_glob == changed_path]
        assert len(matching_mappings) == 1
        mapping = matching_mappings[0]
        assert mapping.heavy_tests == ()
        assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
        assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []


def test_trace_convergence_is_exact_reviewed_none() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    _, mappings = config

    for changed_path in TRACE_REVIEWED_NONE_PATHS:
        matched = [mapping for mapping in mappings if matches_glob(changed_path, mapping.source_glob)]
        assert len(matched) == 1
        mapping = matched[0]
        assert mapping.source_glob == changed_path
        assert mapping.heavy_tests == ()
        assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()


def test_value_normalization_reuse_is_exact_reviewed_none() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    _, mappings = config

    for changed_path in VALUE_NORMALIZATION_REVIEWED_NONE_PATHS:
        matched = [mapping for mapping in mappings if matches_glob(changed_path, mapping.source_glob)]
        assert len(matched) == 1
        mapping = matched[0]
        assert mapping.source_glob == changed_path
        assert mapping.heavy_tests == ()
        assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()


def test_system_capability_identity_reuse_is_exact_reviewed_none() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    _, mappings = config

    for changed_path in SYSTEM_CAPABILITY_IDENTITY_REVIEWED_NONE_PATHS:
        matched = [mapping for mapping in mappings if matches_glob(changed_path, mapping.source_glob)]
        assert len(matched) == 1
        mapping = matched[0]
        assert mapping.source_glob == changed_path
        assert mapping.heavy_tests == ()
        assert mapping.reviewed_content_sha256 == hashlib.sha256((REPO_ROOT / changed_path).read_bytes()).hexdigest()
        assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == []


def test_retired_runtime_mirror_paths_are_absent_and_classified_none() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(RETIRED_RUNTIME_MIRROR_PATHS, config) == []
    assert all(not (REPO_ROOT / path).exists() for path in RETIRED_RUNTIME_MIRROR_PATHS)


def test_plugin_sdk_reviewed_none_fails_closed_on_content_drift(tmp_path: Path) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    changed_path = PLUGIN_SDK_REVIEWED_NONE_PATHS[0]
    drifted_path = tmp_path / changed_path
    drifted_path.parent.mkdir(parents=True)
    drifted_path.write_bytes((REPO_ROOT / changed_path).read_bytes() + b"\n# drift\n")

    with pytest.raises(SelectorError, match="reviewed mapping 内容指纹不匹配"):
        select_heavy_tests([changed_path], config, repo_root=tmp_path)


def test_core_composition_root_keeps_its_heavy_owners() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["src/app/device/composition.py"], config) == [
        DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST,
        DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
        CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST,
    ]


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/device/endpoint.py",
    ],
)
def test_device_endpoint_paths_select_exact_runtime_and_schema_owners(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    expected = [
        DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST,
        DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
        CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST,
        LINE_RUN_EPOCH_ACTIVATION_POSTGRESQL_HEAVY_TEST,
    ]
    if changed_path.startswith("migrations/versions/"):
        expected = [INITIAL_SCHEMA_BASELINE_HEAVY_TEST]
    assert select_heavy_tests([changed_path], config) == expected


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/device/models/event_command_block.py",
        "src/app/device/repositories/event_command_block_repository.py",
        "src/app/device/models/__init__.py",
        "src/app/device/repositories/__init__.py",
        "migrations/env.py",
    ],
)
def test_event_command_block_schema_paths_select_postgresql_owner(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert EVENT_COMMAND_BLOCK_MIGRATION_HEAVY_TEST in select_heavy_tests([changed_path], config)


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    (
        ("pyproject.toml", [DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST]),
        ("uv.lock", [DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST]),
        (
            "src/app/device/repositories/command_repository.py",
            [
                DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST,
                DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
                DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST,
            ],
        ),
        (
            "src/app/device/repositories/status_observation_repository.py",
            [
                DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST,
                DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
                DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST,
            ],
        ),
        (
            "src/app/execution/repositories/wms_confirmation_repository.py",
            [
                DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST,
                EXECUTION_CONSTRAINTS_HEAVY_TEST,
                WMS_INBOUND_CONFIRMATION_HEAVY_TEST,
            ],
        ),
    ),
)
def test_rough_sorter_runtime_paths_independently_select_concrete_execution_owner(
    changed_path: str,
    expected: list[str],
) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == expected


def test_line_run_epoch_changes_select_role_uniqueness_owner() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["src/app/workline/models/line_run_epoch.py"], config) == [
        DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST,
        DEVICE_COMMAND_CONSTRAINTS_HEAVY_TEST,
        DECISION_PROCESSING_POSTGRESQL_HEAVY_TEST,
        EXECUTION_CONSTRAINTS_HEAVY_TEST,
        LINE_RUN_EPOCH_ACTIVATION_POSTGRESQL_HEAVY_TEST,
        WORKLINE_START_POSTGRESQL_HEAVY_TEST,
    ]


@pytest.mark.parametrize(
    "changed_path",
    [
        "src/app/workline/models/start.py",
        "src/app/workline/services/workline_start_service.py",
        "src/app/runtime/capabilities/material_flow/start_admission_service.py",
    ],
)
def test_workline_start_paths_select_postgresql_owner(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    expected = [WORKLINE_START_POSTGRESQL_HEAVY_TEST]
    if changed_path == "src/app/workline/services/workline_start_service.py":
        expected.insert(0, UNFINISHED_EXECUTION_SNAPSHOT_HEAVY_TEST)
    assert select_heavy_tests([changed_path], config) == expected


def test_execution_celery_task_exports_select_postgresql_runtime_owner() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["src/celery_app/tasks/__init__.py"], config) == [
        CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST
    ]


def test_execution_celery_task_selects_runtime_and_prefork_owners() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["src/celery_app/tasks/execution.py"], config) == [
        DEVICE_COMMAND_PRODUCTION_WIRING_E2E_TEST,
        CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST,
    ]


def test_wms_confirmation_dispatcher_selects_exact_owners() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests(["src/celery_app/tasks/wms_confirmation.py"], config) == [
        CELERY_ASYNC_RUNTIME_POSTGRESQL_HEAVY_TEST,
        WMS_INBOUND_CONFIRMATION_HEAVY_TEST,
    ]


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


def test_nginx_runtime_config_is_a_heavy_candidate() -> None:
    assert is_candidate("nginx/conf.d/default.conf") is True


def test_container_entrypoint_is_a_heavy_candidate() -> None:
    changed_path = "docker/test/celery.entrypoint.sh"

    assert is_candidate(changed_path) is True
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    assert select_heavy_tests([changed_path], config) == ["tests/e2e/transport/test_transport_production_wiring.py"]


@pytest.mark.parametrize(
    "changed_path",
    ["tools/release_checker/release_checker.py", "tools/release_checker/Dockerfile", "Jenkinsfile.release-checker-ci"],
)
def test_independent_release_checker_inputs_are_heavy_candidates(changed_path: str) -> None:
    assert is_candidate(changed_path) is True


def test_release_checker_tests_and_fixtures_are_not_release_mode_candidates() -> None:
    assert is_candidate("tools/release_checker/tests/test_release_checker.py") is False
    assert is_candidate("tools/release_checker/tests/fixtures/consumer-used-operation.json") is False


def test_release_checker_tests_and_fixtures_are_explicitly_ignored_by_heavy_selector() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert (
        select_heavy_tests(
            [
                "tools/release_checker/tests/test_release_checker.py",
                "tools/release_checker/tests/fixtures/consumer-used-operation.json",
            ],
            config,
        )
        == []
    )


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


def test_repository_mapping_only_references_existing_heavy_owners() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    missing = sorted(
        heavy_test
        for mapping in config[1]
        for heavy_test in mapping.heavy_tests
        if not (REPO_ROOT / heavy_test).is_file()
    )

    assert missing == []


def test_initial_schema_revision_mapping_is_exact_after_tombstone_cleanup() -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    revision_mappings = [mapping for mapping in config[1] if mapping.source_glob.startswith("migrations/versions/")]

    assert [mapping.source_glob for mapping in revision_mappings] == [
        INITIAL_SCHEMA_REVISION_PATH,
        TRANSPORT_FACE_REVISION_PATH,
        TRANSPORT_DEBUG_PROJECTION_REVISION_PATH,
        TRANSPORT_DEBUG_AUTO_RUN_REVISION_PATH,
    ]
    assert revision_mappings[0].heavy_tests == (INITIAL_SCHEMA_BASELINE_HEAVY_TEST,)
    assert revision_mappings[1].heavy_tests == (
        EXECUTION_CONSTRAINTS_HEAVY_TEST,
        INITIAL_SCHEMA_BASELINE_HEAVY_TEST,
        TRANSPORT_EVIDENCE_HEAVY_TEST,
        TRANSPORT_SCHEMA_HEAVY_TEST,
    )
    assert revision_mappings[2].heavy_tests == (
        INITIAL_SCHEMA_BASELINE_HEAVY_TEST,
        TRANSPORT_DEBUG_RESET_HEAVY_TEST,
        TRANSPORT_EVIDENCE_HEAVY_TEST,
        TRANSPORT_SCHEMA_HEAVY_TEST,
    )
    assert revision_mappings[3].heavy_tests == TRANSPORT_DEBUG_AUTO_RUN_HEAVY_TESTS
    assert select_heavy_tests([INITIAL_SCHEMA_REVISION_PATH], config, repo_root=REPO_ROOT) == [
        INITIAL_SCHEMA_BASELINE_HEAVY_TEST
    ]
    assert select_heavy_tests([TRANSPORT_DEBUG_PROJECTION_REVISION_PATH], config, repo_root=REPO_ROOT) == [
        INITIAL_SCHEMA_BASELINE_HEAVY_TEST,
        TRANSPORT_DEBUG_RESET_HEAVY_TEST,
        TRANSPORT_EVIDENCE_HEAVY_TEST,
        TRANSPORT_SCHEMA_HEAVY_TEST,
    ]
    assert select_heavy_tests([TRANSPORT_DEBUG_AUTO_RUN_REVISION_PATH], config, repo_root=REPO_ROOT) == list(
        TRANSPORT_DEBUG_AUTO_RUN_HEAVY_TESTS
    )


@pytest.mark.parametrize(
    "changed_path",
    (
        "tests/contracts/wms_integration/provider_profile_support.py",
        "tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json",
        "tests/resilience/fixtures/runtime_integration_lab_fixture.json",
        "tests/resilience/fixtures/runtime_replay_fixture.json",
        "tests/resilience/fixtures/runtime_simulator_replay_fixture.json",
        "tests/support/wms_conformance_runner.py",
        "tests/support/wms_integration/scripted_provider.py",
        "tests/support/wms_provider_replay.py",
        "tests/support/wms_query_runtime.py",
    ),
)
def test_phase10_retired_heavy_assets_are_exact_none(changed_path: str) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")
    matching = [mapping for mapping in config[1] if mapping.source_glob == changed_path]

    assert len(matching) == 1
    assert matching[0].heavy_tests == ()
    assert not (REPO_ROOT / changed_path).exists()


@pytest.mark.parametrize(
    ("changed_path", "expected"),
    (
        (
            "src/app/transport/composition.py",
            ["tests/e2e/transport/test_transport_production_wiring.py"],
        ),
        (
            "src/app/wms_adapter/inbound_auth.py",
            ["tests/e2e/transport/test_transport_production_wiring.py"],
        ),
        (
            "src/celery_app/tasks/wms_confirmation.py",
            [
                "tests/integration/test_celery_async_runtime_postgresql.py",
                "tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py",
            ],
        ),
        (
            "tests/support/ecs_uniform_wire.py",
            ["tests/e2e/device_command/test_device_command_production_wiring.py"],
        ),
    ),
)
def test_phase10_target_paths_select_surviving_heavy_owners(changed_path: str, expected: list[str]) -> None:
    config = load_config(REPO_ROOT / "docs/architecture/heavy-test-impact.toml")

    assert select_heavy_tests([changed_path], config, repo_root=REPO_ROOT) == expected


def test_quality_gate_does_not_advertise_a_duplicate_full_profile() -> None:
    quality_gate = (REPO_ROOT / "scripts/git-quality-gate.sh").read_text(encoding="utf-8")

    assert "run_full_profile" not in quality_gate
    assert "full      Run the quality profile plus the full pytest suite." not in quality_gate
    assert "full)" not in quality_gate
