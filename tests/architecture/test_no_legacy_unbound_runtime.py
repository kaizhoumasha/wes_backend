"""Generated-only runtime 静态护栏。"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOVED_PATHS = (
    "src/app/runtime/orchestration/orchestrator_bridge.py",
    "src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_processor_service.py",
    "src/app/runtime/workline_plugins/legacy_compatibility.py",
    "tests/runtime/orchestration/test_legacy_unbound_session_processor.py",
)
REMOVED_MODULES = {
    "src.app.runtime.orchestration.orchestrator_bridge",
    "src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_processor_service",
    "src.app.runtime.workline_plugins.legacy_compatibility",
}
GENERATED_RUNTIME_SOURCES = (
    REPO_ROOT / "src/app/runtime/orchestration/services/runtime_inbox",
    REPO_ROOT / "src/app/runtime/orchestration/runtime_intent_effects.py",
    REPO_ROOT / "src/app/runtime/orchestration/services/intent/smt_inbound_handoff_service.py",
    REPO_ROOT / "src/app/workline/services/write_back_service.py",
)
ACTIVE_RUNTIME_IDENTITY_ROOTS = (
    REPO_ROOT / "src",
    REPO_ROOT / "scripts",
)
BANNED_GENERATED_TOKENS = (
    "OrchestratorResult",
    "RuntimeInboxProcessorService",
    "orch_result",
    "plugin_attempt_runner",
    "SimpleNamespace",
    "legacy_compatibility",
    "runtime_inbox_processor_service",
)


def _python_sources(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py")) if root.is_dir() else [root]


def test_removed_legacy_unbound_runtime_files_do_not_exist() -> None:
    assert [path for path in REMOVED_PATHS if (REPO_ROOT / path).exists()] == []


def test_active_sources_do_not_import_removed_runtime_modules() -> None:
    offenders: list[str] = []
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in REMOVED_MODULES:
                    offenders.append(source.relative_to(REPO_ROOT).as_posix())
                if isinstance(node, ast.Import) and any(alias.name in REMOVED_MODULES for alias in node.names):
                    offenders.append(source.relative_to(REPO_ROOT).as_posix())
    assert sorted(set(offenders)) == []


def test_generated_runtime_has_no_legacy_effect_state_or_untyped_placeholder() -> None:
    offenders: dict[str, list[str]] = {}
    for root in GENERATED_RUNTIME_SOURCES:
        for source in _python_sources(root):
            text = source.read_text(encoding="utf-8")
            hits = [token for token in BANNED_GENERATED_TOKENS if token in text]
            if hits:
                offenders[source.relative_to(REPO_ROOT).as_posix()] = hits
    assert offenders == {}


def test_active_runtime_sources_do_not_reference_retired_smt_identity() -> None:
    retired_identity_tokens = (
        "SMT_SORTING_" + "INBOUND",
        "2026-06-21" + ".p1",
    )
    offenders: dict[str, list[str]] = {}
    for root in ACTIVE_RUNTIME_IDENTITY_ROOTS:
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            source_literals = {
                node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            hits = [token for token in retired_identity_tokens if token in source_literals]
            if hits:
                offenders[source.relative_to(REPO_ROOT).as_posix()] = hits
    assert offenders == {}
