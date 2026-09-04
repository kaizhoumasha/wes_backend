"""target-state legacy plugin absence guardrail.

target-state runtime capability wiring 的目标是退出旧 plugin runtime/import 框架。归档文档可以
提到旧路径，但生产 `src/` 路径不得再 import 旧模块。
"""

from __future__ import annotations

import ast
import csv
import importlib
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from scripts.generate_legacy_matrix import PHASE10_PRELOCK_SPECS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = PROJECT_ROOT / "src"
MATRIX_PATH = PROJECT_ROOT / "docs/architecture/legacy-cleanup-matrix.csv"
FROZEN_SCHEMA_ONLY_TEST_PATHS = frozenset(
    {
        "tests/architecture/test_cleanup_matrix_guardrail.py",
        "tests/architecture/test_runtime_status_owner_guardrail.py",
    }
)
CURRENT_MIGRATION_REVISION_PATHS = frozenset(
    path.relative_to(PROJECT_ROOT).as_posix() for path in (PROJECT_ROOT / "migrations/versions").glob("*.py")
)
FROZEN_MIGRATION_REVISION_PATHS_SHA256 = "f582056201f53ecbbe9ed0dc4185c3dd27ff9e7da62c997deef05e69b1102fc2"
FROZEN_PHASE10_HISTORICAL_SPEC_COUNT = 317
FROZEN_PHASE10_HISTORICAL_SPEC_SHA256 = "08eb1edc9d92ad9e90a71d156f149aa23d4683a3813ec06c26599c705932fb23"
FROZEN_ALLOWED_MISSING_READS: frozenset[tuple[str, str]] = frozenset()

LEGACY_ROUTE_PATHS = frozenset(
    {
        "/reconciliations/effects/{dispatch_key}/resolve",
        "/reconciliations/sessions/{session_id}/resolve",
        "/replay/inboxes/{inbox_id}",
        "/sandbox/ack",
        "/sandbox/completed",
        "/sandbox/external-callbacks",
        "/sandbox/pending",
        "/sandbox/process",
        "/sandbox/worklines/{workline_id}/simulate-estop",
    }
)
LEGACY_WMS_CONFIG_TOKENS = frozenset(
    {
        "WES_DEV_PROVIDER_PROFILE_FILE",
        "WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES",
        "WMS_EFFECT_ADMISSION_ENABLED",
        "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS",
        "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES",
        "WMS_EFFECT_STATUS_TIMEOUT_SECONDS",
        "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS",
        "WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS",
        "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1",
        "WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V2",
        "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1",
        "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V2",
        "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V1",
        "WMS_MATERIAL_FLOW_STAGING_HMAC_SECRET_V2",
        "WMS_PROVIDER_PROFILE_FILE",
        "WMS_PROVIDER_PROFILE_HOST_FILE",
    }
)
MACHINE_CONFIG_PATHS = (
    ".env.dev",
    ".env.prod",
    ".env.test",
    "Jenkinsfile.backend-ci",
    "Jenkinsfile.test-deploy",
    "docker-compose.deploy.yml",
    "docker-compose.frontend.yml",
    "docker-compose.test-deploy.yml",
    "docker-compose.yml",
    "src/core/conf.py",
)
PHASE11_RETIRED_OPERATIONAL_PATHS = (
    "scripts/check_legacy_drain_readiness.py",
    "src/app/runtime/orchestration/repositories/legacy_drain_readiness_repository.py",
    "src/app/runtime/orchestration/services/query/legacy_drain_readiness_service.py",
)
PHASE11_RETIRED_MODEL_PATHS = (
    "src/app/runtime/orchestration/bin_route_instance.py",
    "src/app/runtime/orchestration/conveyor_queue_membership.py",
    "src/app/runtime/orchestration/execution_correlation.py",
    "src/app/runtime/orchestration/execution_session.py",
    "src/app/runtime/orchestration/execution_work_item.py",
    "src/app/runtime/orchestration/idempotency_key.py",
    "src/app/runtime/orchestration/material_flow_owner.py",
    "src/app/runtime/orchestration/models/bin_cell_reservation.py",
    "src/app/runtime/orchestration/models/diagnostic.py",
    "src/app/runtime/orchestration/models/dispatch_attempt.py",
    "src/app/runtime/orchestration/models/runtime_hold.py",
    "src/app/runtime/orchestration/reconciliation_case.py",
    "src/app/runtime/orchestration/runtime_hold.py",
    "src/app/runtime/orchestration/runtime_inbox.py",
    "src/app/runtime/orchestration/runtime_intent_log.py",
    "src/app/runtime/orchestration/runtime_timeline.py",
    "src/app/runtime/orchestration/wms_rack_demand.py",
    "src/app/sys/models/outbox.py",
    "src/app/wms_integration/models/circuit_breaker.py",
    "src/app/wms_integration/models/evidence.py",
)


def _token(*parts: str) -> str:
    return "".join(parts)


def test_phase11_legacy_drain_operational_chain_is_absent() -> None:
    existing = [path for path in PHASE11_RETIRED_OPERATIONAL_PATHS if (PROJECT_ROOT / path).exists()]

    assert not existing, f"Phase 11 legacy drain operational chain must be retired: {existing}"


def test_phase11_retired_schema_model_sources_are_absent() -> None:
    existing = [path for path in PHASE11_RETIRED_MODEL_PATHS if (PROJECT_ROOT / path).exists()]

    assert not existing, f"Phase 11 retired schema model sources must be absent: {existing}"


_HANDLING_QUEUE_MEMBERSHIP_MODULE = _token("bin", "_", "transit", "_", "membership")
_HANDLING_QUEUE_MEMBERSHIP_TABLE = _token("bin", "_", "transit", "_", "memberships")
FORBIDDEN_MODULES = (
    "src.app.workline.plugins",
    "src.workline_plugin_registry",
    "src.workline_plugins",
    f"src.app.handling.models.{_HANDLING_QUEUE_MEMBERSHIP_MODULE}",
    f"src.app.handling.repositories.{_HANDLING_QUEUE_MEMBERSHIP_MODULE}_repository",
    f"src.app.handling.services.{_HANDLING_QUEUE_MEMBERSHIP_MODULE}_service",
)
FORBIDDEN_IMPORT_TEXT = (
    *FORBIDDEN_MODULES,
    _token("Bin", "Transit", "Membership"),
    _token("Bin", "Transit", "Queue"),
    _HANDLING_QUEUE_MEMBERSHIP_TABLE,
)


def _phase10_entries() -> list[dict[str, str]]:
    with MATRIX_PATH.open(encoding="utf-8", newline="") as stream:
        return [row for row in csv.DictReader(stream) if row["drop_phase"] in {"phase10", "phase11-schema"}]


def _module_name(relative_path: str) -> str:
    module_name = relative_path.removesuffix(".py").replace("/", ".")
    return module_name.removesuffix(".__init__")


def _path_set_sha256(paths: frozenset[str]) -> str:
    return sha256("\n".join(sorted(paths)).encode()).hexdigest()


def _historical_deleted_paths() -> frozenset[str]:
    canonical_specs = "\n".join("\0".join(spec) for spec in PHASE10_PRELOCK_SPECS)
    assert len(PHASE10_PRELOCK_SPECS) == FROZEN_PHASE10_HISTORICAL_SPEC_COUNT
    assert sha256(canonical_specs.encode()).hexdigest() == FROZEN_PHASE10_HISTORICAL_SPEC_SHA256
    return frozenset(spec[1] for spec in PHASE10_PRELOCK_SPECS if spec[5] in {"delete", "switch"})


def _resolved_imports(path: Path, *, repo_root: Path = PROJECT_ROOT) -> list[tuple[str, tuple[str, ...]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    source_module = _module_name(path.relative_to(repo_root).as_posix())
    source_package = source_module if path.name == "__init__.py" else source_module.rpartition(".")[0]
    imports: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, ()) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            package_parts = source_package.split(".") if source_package else []
            keep = len(package_parts) - node.level + 1
            prefix = package_parts[: max(0, keep)]
            module_parts = node.module.split(".") if node.module else []
            imported_module = ".".join((*prefix, *module_parts))
        else:
            imported_module = node.module or ""
        imports.append((imported_module, tuple(alias.name for alias in node.names)))
    return imports


def _local_module_exists(repo_root: Path, module_name: str) -> bool:
    module_path = repo_root / module_name.replace(".", "/")
    return module_path.with_suffix(".py").is_file() or (module_path / "__init__.py").is_file() or module_path.is_dir()


def _local_import_offenders(*, repo_root: Path, roots: tuple[str, ...]) -> list[str]:
    offenders: list[str] = []
    for root_name in roots:
        scan_root = repo_root / root_name
        if not scan_root.is_dir():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            relative_path = path.relative_to(repo_root).as_posix()
            for imported_module, _names in _resolved_imports(path, repo_root=repo_root):
                if imported_module.startswith(("src.", "scripts.", "tests.")) and not _local_module_exists(
                    repo_root, imported_module
                ):
                    offenders.append(f"{relative_path} -> {imported_module}")
    return offenders


def _missing_read_targets(
    *,
    repo_root: Path,
    roots: tuple[str, ...],
    deleted_paths: frozenset[str],
    allowed_missing_reads: frozenset[tuple[str, str]],
) -> list[str]:
    offenders: list[str] = []
    candidate_paths = {
        path for root_name in roots if (scan_root := repo_root / root_name).is_dir() for path in scan_root.rglob("*.py")
    }
    for path in sorted(candidate_paths):
        relative_source = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        root_names = {
            node.targets[0].id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.endswith("ROOT")
        }

        def resolve(node: ast.expr, *, roots: set[str] = root_names) -> Path | None:
            if isinstance(node, ast.Name) and node.id in roots:
                return repo_root
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return Path(node.value)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                left = resolve(node.left)
                right = resolve(node.right)
                return left / right if left is not None and right is not None else None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path" and node.args:
                return resolve(node.args[0])
            return None

        for node in ast.walk(tree):
            target: Path | None = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"open", "read_bytes", "read_text"}:
                    target = resolve(node.func.value)
            elif (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open" and node.args
            ):
                target = resolve(node.args[0])
            if target is None:
                continue
            absolute = target if target.is_absolute() else repo_root / target
            if not absolute.is_relative_to(repo_root):
                continue
            relative_target = absolute.relative_to(repo_root).as_posix()
            if (
                relative_target.startswith(("src/", "scripts/", "tests/"))
                and not absolute.exists()
                and relative_target in deleted_paths
                and (relative_source, relative_target) not in allowed_missing_reads
            ):
                offenders.append(f"{relative_source} -> {relative_target}")
    return sorted(set(offenders))


def _phase10_missing_read_targets(*, repo_root: Path, roots: tuple[str, ...]) -> list[str]:
    return _missing_read_targets(
        repo_root=repo_root,
        roots=roots,
        deleted_paths=_historical_deleted_paths(),
        allowed_missing_reads=FROZEN_ALLOWED_MISSING_READS,
    )


def _schema_deferred_import_offenders(
    *,
    repo_root: Path,
    schema_entries: list[dict[str, str]],
    schema_only_test_paths: frozenset[str],
    migration_revision_paths: frozenset[str],
) -> list[str]:
    schema_paths = {row["relative_path"] for row in schema_entries}
    schema_symbols_by_module: dict[str, set[str]] = {}
    for row in schema_entries:
        schema_symbols_by_module.setdefault(_module_name(row["relative_path"]), set()).add(
            row["symbol_or_route"].split(".", 1)[0]
        )

    allowed_paths = schema_paths | schema_only_test_paths | migration_revision_paths | {"migrations/env.py"}
    candidate_paths = {
        path
        for root_name in ("src", "scripts", "tests", "migrations")
        if (scan_root := repo_root / root_name).is_dir()
        for path in scan_root.rglob("*.py")
    }
    offenders: set[str] = set()
    for path in sorted(candidate_paths):
        relative_path = path.relative_to(repo_root).as_posix()
        if relative_path in allowed_paths:
            continue
        for imported_module, imported_names in _resolved_imports(path, repo_root=repo_root):
            module_hit = any(
                imported_module == module or imported_module.startswith(f"{module}.")
                for module in schema_symbols_by_module
            )
            symbol_hits = sorted(
                symbol
                for module, symbols in schema_symbols_by_module.items()
                if module == imported_module or module.startswith(f"{imported_module}.")
                for symbol in symbols & set(imported_names)
            )
            if module_hit or symbol_hits:
                offenders.add(f"{relative_path} -> {imported_module}:{','.join(symbol_hits)}")
    return sorted(offenders)


def _route_paths(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    paths: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or not node.args:
            continue
        if node.func.attr not in {"delete", "get", "patch", "post", "put"}:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            paths.add(first.value)
    return paths


def _defined_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name for node in tree.body if isinstance(node, ast.AsyncFunctionDef | ast.ClassDef | ast.FunctionDef)}


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


@pytest.mark.parametrize("module_name", FORBIDDEN_MODULES)
def test_legacy_plugin_modules_are_not_importable_from_runtime_path(module_name: str) -> None:
    """旧 plugin runtime 路径必须离开生产 import surface。"""

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_production_source_does_not_reference_legacy_plugin_imports() -> None:
    """生产源码不得继续引用旧 plugin runtime 路径。"""

    offenders: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(forbidden in text for forbidden in FORBIDDEN_IMPORT_TEXT):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_phase10_delete_modules_and_exact_symbols_are_absent() -> None:
    """批准为 DELETE -> NONE 的模块或符号不得残留在 target production tree。"""

    delete_entries = [
        row
        for row in _phase10_entries()
        if row["drop_phase"] == "phase10" and row["strategy"] == "delete" and row["target_capability"] == "NONE"
    ]
    missing_module_paths = sorted(
        {
            row["relative_path"]
            for row in delete_entries
            if row["symbol_or_route"] == "<file>" and (PROJECT_ROOT / row["relative_path"]).exists()
        }
    )
    remaining_symbols: list[str] = []
    for row in delete_entries:
        symbol = row["symbol_or_route"]
        path = PROJECT_ROOT / row["relative_path"]
        if symbol in {"<file>"} or row["entry_type"] == "celery_task" or not path.is_file() or path.suffix != ".py":
            continue
        top_level_symbol = symbol.split(".", 1)[0]
        if top_level_symbol in _defined_symbols(path):
            remaining_symbols.append(f"{row['relative_path']}:{symbol}")

    assert missing_module_paths == []
    assert sorted(remaining_symbols) == []


def test_phase10_delete_modules_have_no_production_importers() -> None:
    """最终 composition 不得继续 import 已批准删除的 module roots。"""

    delete_roots = {
        _module_name(row["relative_path"])
        for row in _phase10_entries()
        if row["drop_phase"] == "phase10"
        and row["strategy"] == "delete"
        and row["target_capability"] == "NONE"
        and row["symbol_or_route"] == "<file>"
        and row["relative_path"].startswith("src/")
        and row["relative_path"].endswith(".py")
    }
    offenders: list[str] = []
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        for imported_module, _names in _resolved_imports(path):
            if any(imported_module == root or imported_module.startswith(f"{root}.") for root in delete_roots):
                offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()} -> {imported_module}")

    assert offenders == []


def test_surviving_executable_imports_and_support_file_reads_resolve() -> None:
    """surviving src/scripts/support 不得 import 或读取已删路径。"""

    assert _local_import_offenders(repo_root=PROJECT_ROOT, roots=("src", "scripts", "tests")) == []
    assert _phase10_missing_read_targets(repo_root=PROJECT_ROOT, roots=("tests",)) == []


def test_surviving_reference_scanner_rejects_dangling_imports_and_support_reads(tmp_path: Path) -> None:
    live_module = tmp_path / "src/live.py"
    live_module.parent.mkdir(parents=True)
    live_module.write_text("VALUE = 1\n", encoding="utf-8")
    support = tmp_path / "tests/support/helper.py"
    support.parent.mkdir(parents=True)
    support.write_text(
        """from pathlib import Path
from src.deleted import VALUE as DELETED_VALUE
from src.live import VALUE

REPO_ROOT = Path(__file__).resolve().parents[2]
(REPO_ROOT / \"src/deleted.py\").read_text(encoding=\"utf-8\")
open(REPO_ROOT / \"scripts/deleted.py\", encoding=\"utf-8\")
""",
        encoding="utf-8",
    )

    assert _local_import_offenders(repo_root=tmp_path, roots=("src", "scripts", "tests/support")) == [
        "tests/support/helper.py -> src.deleted"
    ]
    assert _phase10_missing_read_targets(repo_root=tmp_path, roots=("tests",)) == []


@pytest.mark.parametrize(
    ("expression", "target"),
    (
        (
            '(REPO_ROOT / "src/app/runtime/orchestration/services/wms_effect_status_service.py").read_text()',
            "src/app/runtime/orchestration/services/wms_effect_status_service.py",
        ),
        (
            '(REPO_ROOT / "src/app/wms_integration/operation_registry.py").read_bytes()',
            "src/app/wms_integration/operation_registry.py",
        ),
        (
            'open(REPO_ROOT / "src/app/wms_integration/operation_registry.py")',
            "src/app/wms_integration/operation_registry.py",
        ),
    ),
)
def test_missing_read_scanner_rejects_retired_historical_targets(tmp_path: Path, expression: str, target: str) -> None:
    source = tmp_path / "tests/architecture/test_adversarial_absence.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        f"from pathlib import Path\nREPO_ROOT = Path(__file__).resolve().parents[2]\n{expression}\n",
        encoding="utf-8",
    )

    assert _phase10_missing_read_targets(repo_root=tmp_path, roots=("tests",)) == [
        f"tests/architecture/test_adversarial_absence.py -> {target}"
    ]


def test_missing_read_scanner_has_no_absence_owner_allowlist() -> None:
    assert not FROZEN_ALLOWED_MISSING_READS


def test_phase10_legacy_celery_task_names_are_absent_from_production_wiring() -> None:
    """旧 task identity 不得留在注册、route 或 Beat machine contract。"""

    forbidden_task_names = {
        row["symbol_or_route"]
        for row in _phase10_entries()
        if row["drop_phase"] == "phase10" and row["strategy"] == "delete" and row["entry_type"] == "celery_task"
    }
    occurrences: dict[str, list[str]] = {task_name: [] for task_name in forbidden_task_names}
    for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
        constants = _string_constants(path)
        for task_name in forbidden_task_names & constants:
            occurrences[task_name].append(path.relative_to(PROJECT_ROOT).as_posix())

    assert {name: paths for name, paths in sorted(occurrences.items()) if paths} == {}


def test_phase10_legacy_routes_are_absent_from_application_surface() -> None:
    """target API 只保留具体 owner，不暴露 sandbox/replay/generic reconciliation route。"""

    occurrences: dict[str, list[str]] = {route: [] for route in LEGACY_ROUTE_PATHS}
    for path in sorted((PRODUCTION_ROOT / "app").rglob("*.py")):
        for route in LEGACY_ROUTE_PATHS & _route_paths(path):
            occurrences[route].append(path.relative_to(PROJECT_ROOT).as_posix())

    assert {route: paths for route, paths in sorted(occurrences.items()) if paths} == {}


def test_phase10_legacy_wms_env_keys_and_profile_mount_are_absent() -> None:
    """target deployment 使用最小 WMS settings，不再装配 profile/effect/HMAC/credential lane。"""

    offenders: dict[str, list[str]] = {}
    for relative_path in MACHINE_CONFIG_PATHS:
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        tokens = sorted(token for token in LEGACY_WMS_CONFIG_TOKENS if token in text)
        if "/run/wes/wms-provider.yaml" in text:
            tokens.append("/run/wes/wms-provider.yaml")
        if tokens:
            offenders[relative_path] = tokens

    assert offenders == {}


def test_schema_deferred_models_are_imported_only_by_frozen_schema_owners() -> None:
    """Phase 10 去除 src/scripts/行为测试消费者；schema metadata identity 留给 Phase 11。"""

    schema_entries = [row for row in _phase10_entries() if row["strategy"] == "schema-deferred"]

    assert all((PROJECT_ROOT / relative_path).is_file() for relative_path in FROZEN_SCHEMA_ONLY_TEST_PATHS)
    assert _path_set_sha256(CURRENT_MIGRATION_REVISION_PATHS) == FROZEN_MIGRATION_REVISION_PATHS_SHA256
    assert (
        _schema_deferred_import_offenders(
            repo_root=PROJECT_ROOT,
            schema_entries=schema_entries,
            schema_only_test_paths=FROZEN_SCHEMA_ONLY_TEST_PATHS,
            migration_revision_paths=CURRENT_MIGRATION_REVISION_PATHS,
        )
        == []
    )


def test_schema_deferred_scanner_rejects_scripts_and_behavior_tests_without_rejecting_schema_owners(
    tmp_path: Path,
) -> None:
    schema_entries = [row for row in _phase10_entries() if row["strategy"] == "schema-deferred"]
    model_path = schema_entries[0]["relative_path"]
    model_module = _module_name(model_path)
    model_symbol = schema_entries[0]["symbol_or_route"].split(".", 1)[0]
    schema_import = f"from {model_module} import {model_symbol}\n"
    fixtures = {
        model_path: schema_import,
        "migrations/env.py": schema_import,
        "migrations/versions/frozen_revision.py": schema_import,
        "migrations/versions/extra_revision.py": schema_import,
        "tests/schema/test_frozen_schema_owner.py": schema_import,
        "scripts/behavior_consumer.py": schema_import,
        "tests/api/test_behavior_consumer.py": schema_import,
    }
    for relative_path, content in fixtures.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    assert _schema_deferred_import_offenders(
        repo_root=tmp_path,
        schema_entries=schema_entries,
        schema_only_test_paths=frozenset({"tests/schema/test_frozen_schema_owner.py"}),
        migration_revision_paths=frozenset({"migrations/versions/frozen_revision.py"}),
    ) == [
        f"migrations/versions/extra_revision.py -> {model_module}:{model_symbol}",
        f"scripts/behavior_consumer.py -> {model_module}:{model_symbol}",
        f"tests/api/test_behavior_consumer.py -> {model_module}:{model_symbol}",
    ]
