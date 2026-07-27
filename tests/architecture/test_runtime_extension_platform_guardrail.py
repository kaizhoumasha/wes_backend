"""Workline Plugin / System Capability 扩展平台架构门禁。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
ALLOWLIST = REPO_ROOT / "scripts" / "architecture-guardrails.allowlist"


def _run_fixture(tmp_path: Path, relative_path: str, source: str) -> subprocess.CompletedProcess[str]:
    fixture_file = tmp_path / relative_path
    fixture_file.parent.mkdir(parents=True, exist_ok=True)
    fixture_file.write_text(source, encoding="utf-8")
    env = os.environ.copy()
    env["RUNTIME_EXTENSION_GUARDRAIL_FIXTURE_ROOT"] = str(tmp_path)
    env["RUNTIME_EXTENSION_GUARDRAIL_FIXTURE_ONLY"] = "1"
    return subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced", "--allowlist", str(ALLOWLIST)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("relative_path", "source", "rule_id"),
    [
        (
            "workline_plugins/example/handler.py",
            "from sqlalchemy import select\n",
            "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY",
        ),
        (
            "system_capabilities/example/handler.py",
            "await db.commit()\n",
            "SYSTEM_CAPABILITY_DEPENDENCY_BOUNDARY",
        ),
        (
            "workline_plugins/generated_index.py",
            "import importlib\nimportlib.import_module('example')\n",
            "RUNTIME_GENERATED_INDEX_STATICITY",
        ),
        (
            "orchestration/orchestrator_bridge.py",
            "if event_type == 'SCAN_COMPLETED':\n    pass\n",
            "RUNTIME_EXTENSION_GENERIC_ORCHESTRATION",
        ),
        (
            "consumer.py",
            "from src.app.runtime.capability_catalog import get_workline_capability_definition\n",
            "LEGACY_CAPABILITY_ROUTING_IMPORT",
        ),
    ],
)
def test_runtime_extension_guardrail_reports_rule_file_and_line(
    tmp_path: Path,
    relative_path: str,
    source: str,
    rule_id: str,
) -> None:
    result = _run_fixture(tmp_path, relative_path, source)

    assert result.returncode == 1
    assert f"[{rule_id}]" in result.stderr
    assert f"file: {tmp_path / relative_path}:1" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "from src.app.runtime import capability_catalog as catalog\n",
        "from src.app.runtime import runtime_capability_catalog as catalog\n",
        "from src.app.runtime import capability_dispatcher as dispatcher\n",
        "from . import capability_catalog as catalog\n",
        "from .runtime_capability_catalog import get_definition as get_def\n",
        "import src.app.runtime.capability_dispatcher as dispatcher\n",
    ],
)
def test_legacy_capability_import_variants_report_rule_file_and_line(tmp_path: Path, source: str) -> None:
    result = _run_fixture(tmp_path, "consumer.py", source)

    assert result.returncode == 1
    assert "[LEGACY_CAPABILITY_ROUTING_IMPORT]" in result.stderr
    assert f"file: {tmp_path / 'consumer.py'}:1" in result.stderr


def test_legacy_capability_import_names_in_comments_and_strings_are_ignored(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        "consumer.py",
        '"from src.app.runtime import capability_catalog"\n# import src.app.runtime.capability_dispatcher\n',
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("relative_path", "source", "rule_id"),
    [
        (
            "workline_plugins/example/handler.py",
            "from .repositories.deep import ExampleRepository\n",
            "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY",
        ),
        (
            "system_capabilities/example/handler.py",
            "from src.app.inventory.repositories.deep import InventoryRepository\n",
            "SYSTEM_CAPABILITY_DEPENDENCY_BOUNDARY",
        ),
        (
            "workline_plugins/generated_index.py",
            "from os import walk as traverse; traverse('.')\n",
            "RUNTIME_GENERATED_INDEX_STATICITY",
        ),
        (
            "system_capabilities/generated_index.py",
            "import importlib as loader\nloader.import_module('example')\n",
            "RUNTIME_GENERATED_INDEX_STATICITY",
        ),
        (
            "system_capabilities/generated_index.py",
            "from pathlib import Path as Tree; Tree('.').rglob('*.py')\n",
            "RUNTIME_GENERATED_INDEX_STATICITY",
        ),
        (
            "workline_plugins/example/handler.py",
            "from ... import capability_catalog as catalog\n",
            "LEGACY_CAPABILITY_ROUTING_IMPORT",
        ),
        (
            "consumer.py",
            "from src.app.runtime.capability_catalog.compat import lookup\n",
            "LEGACY_CAPABILITY_ROUTING_IMPORT",
        ),
    ],
)
def test_runtime_extension_guardrail_closes_normalized_import_bypasses(
    tmp_path: Path,
    relative_path: str,
    source: str,
    rule_id: str,
) -> None:
    result = _run_fixture(tmp_path, relative_path, source)

    assert result.returncode == 1
    assert f"[{rule_id}]" in result.stderr
    assert f"file: {tmp_path / relative_path}:1" in result.stderr


def test_system_capability_does_not_treat_unrelated_commit_receiver_as_transaction(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        "system_capabilities/example/handler.py",
        "release.commit()\n",
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "conn.commit()\n",
        "ctx.conn.rollback()\n",
        "tx.commit()\n",
        "ctx.tx.rollback()\n",
        "unit_of_work.commit()\n",
        "db_session.commit()\n",
        "ctx.db_session.commit()\n",
        "await get_session().rollback()\n",
        "session_factory().commit()\n",
        "uow.commit()\n",
    ],
)
def test_system_capability_transaction_receiver_uses_terminal_snake_case_tokens(
    tmp_path: Path,
    source: str,
) -> None:
    result = _run_fixture(tmp_path, "system_capabilities/example/handler.py", source)

    assert result.returncode == 1
    assert "[SYSTEM_CAPABILITY_DEPENDENCY_BOUNDARY]" in result.stderr


def test_system_capability_transaction_receiver_ignores_non_token_substrings(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        "system_capabilities/example/handler.py",
        "release_sessionless.commit()\n",
    )

    assert result.returncode == 0, result.stderr


def test_generated_index_does_not_treat_unrelated_walk_method_as_filesystem_scan(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        "system_capabilities/generated_index.py",
        "release.walk()\n",
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "import os\ntraverse = os.walk\ntraverse('.')\n",
        "import os\ntraverse = os.walk\nscan = traverse\nscan('.')\n",
        "from pathlib import Path as ImportedPath\nscan = ImportedPath('.').glob\nscan('*.py')\n",
    ],
)
def test_generated_index_tracks_import_provenance_through_simple_alias_assignments(
    tmp_path: Path,
    source: str,
) -> None:
    result = _run_fixture(tmp_path, "workline_plugins/generated_index.py", source)

    assert result.returncode == 1
    assert "[RUNTIME_GENERATED_INDEX_STATICITY]" in result.stderr


@pytest.mark.parametrize(
    "source",
    [
        "class Path:\n    def glob(self, pattern): return ()\nPath().glob('*.py')\n",
        "Path('.').glob('*.py')\n",
        "import os\nos = release\nos.walk('.')\n",
        "from os import walk as traverse\ntraverse = release.walk\ntraverse('.')\n",
        "from os import walk as traverse\ndef traverse(path): return ()\ntraverse('.')\n",
    ],
)
def test_generated_index_invalidates_provenance_after_rebinding_and_ignores_local_path(
    tmp_path: Path,
    source: str,
) -> None:
    result = _run_fixture(tmp_path, "workline_plugins/generated_index.py", source)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("relative_path", "rule_id"),
    [
        ("workline_plugins/example/handler.py", "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY"),
        ("system_capabilities/example/handler.py", "SYSTEM_CAPABILITY_DEPENDENCY_BOUNDARY"),
        ("workline_plugins/generated_index.py", "RUNTIME_GENERATED_INDEX_STATICITY"),
    ],
)
def test_syntax_error_uses_current_scanner_rule(tmp_path: Path, relative_path: str, rule_id: str) -> None:
    result = _run_fixture(tmp_path, relative_path, "def broken(:\n")

    assert result.returncode == 1
    assert f"[{rule_id}]" in result.stderr


@pytest.mark.parametrize(
    "relative_path",
    [
        "workline_plugins/future_plugin/handler.py",
        "system_capabilities/future_capability/handler.py",
    ],
)
def test_future_active_extension_path_legacy_import_is_always_caught(tmp_path: Path, relative_path: str) -> None:
    result = _run_fixture(
        tmp_path,
        relative_path,
        "from src.app.runtime.capability_dispatcher import dispatch\n",
    )

    assert result.returncode == 1
    assert "[LEGACY_CAPABILITY_ROUTING_IMPORT]" in result.stderr


def test_runtime_extension_platform_has_no_unallowlisted_violation() -> None:
    result = subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced", "--allowlist", str(ALLOWLIST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_new_extension_platform_directories_cannot_be_allowlisted() -> None:
    rows = [row for row in ALLOWLIST.read_text(encoding="utf-8").splitlines() if row and not row.startswith("#")]

    assert all("|src/app/runtime/workline_plugins/" not in row for row in rows)
    assert all("|src/app/runtime/system_capabilities/" not in row for row in rows)


def test_destructive_switch_removes_legacy_runtime_catalog_sources() -> None:
    legacy_sources = (
        "src/app/runtime/capability_dispatcher.py",
        "src/app/runtime/runtime_capability_catalog.py",
        "src/app/runtime/capability_catalog.py",
        "src/app/workline/domain/plugin_manifest.py",
        "src/app/workline/domain/contracts/manifests/rough_sorter.yaml",
        "src/app/workline/domain/contracts/manifests/smt_sorting_inbound.yaml",
    )

    assert all(not (REPO_ROOT / relative_path).exists() for relative_path in legacy_sources)


def test_active_sources_have_zero_legacy_runtime_catalog_references() -> None:
    legacy_tokens = (
        "src.app.runtime.capability_catalog",
        "src.app.runtime.runtime_capability_catalog",
        "src.app.runtime.capability_dispatcher",
        "WorklinePluginManifest",
        "plugin_manifest",
        "contracts/manifests",
    )
    active_roots = (REPO_ROOT / "src", REPO_ROOT / "scripts")
    detector_sources = {
        REPO_ROOT / "scripts/architecture-guardrails.sh",
        REPO_ROOT / "scripts/generate_legacy_matrix.py",
    }
    violations: list[str] = []
    for active_root in active_roots:
        for path in active_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sh"}:
                continue
            if path in detector_sources:
                continue
            source = path.read_text(encoding="utf-8")
            if any(token in source for token in legacy_tokens):
                violations.append(str(path.relative_to(REPO_ROOT)))

    assert violations == []


def test_orchestrator_and_effect_applier_have_no_workline_specific_routing() -> None:
    removed_orchestrator = REPO_ROOT / "src/app/runtime/orchestration/orchestrator_bridge.py"
    routing_sources = (
        REPO_ROOT / "src/app/runtime/orchestration/runtime_intent_effects.py",
        REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_orchestrator_bridge.py",
    )
    forbidden_tokens = (
        "ROUGH_SORTER_PLUGIN_KEY",
        "SMT_SORTING_INBOUND_PLUGIN_KEY",
        "EVENT_SCAN_COMPLETED",
        "EVENT_SOURCE_PICK_REQUESTED",
        "ACTION_PICK_AND_PUT",
        "ACTION_MOVE_TO_NG",
        "SORTING_SOURCE_PICK_REQUESTED",
        "handoff_source_item_id",
        "BUSINESS_TIMEOUT",
    )

    violations = {
        str(path.relative_to(REPO_ROOT)): [
            token for token in forbidden_tokens if token in path.read_text(encoding="utf-8")
        ]
        for path in routing_sources
    }
    assert not removed_orchestrator.exists()
    assert {path: tokens for path, tokens in violations.items() if tokens} == {}


def test_runtime_routing_has_one_generated_workline_plugin_dispatcher() -> None:
    dispatcher_source = (REPO_ROOT / "src/app/runtime/workline_plugins/dispatcher.py").read_text(encoding="utf-8")
    runtime_sources = tuple((REPO_ROOT / "src/app/runtime").rglob("*.py"))

    assert "from src.app.runtime.workline_plugins.generated_index import" in dispatcher_source
    assert sum("class WorklinePluginDispatcher" in path.read_text(encoding="utf-8") for path in runtime_sources) == 1
    assert sum("class RuntimeCapabilityDispatcher" in path.read_text(encoding="utf-8") for path in runtime_sources) == 0
