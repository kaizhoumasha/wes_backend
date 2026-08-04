from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_selected_heavy_tests.py"


def _load_runner_module() -> ModuleType:
    assert RUNNER_PATH.is_file(), "缺少 selected HEAVY test runner"
    spec = importlib.util.spec_from_file_location("run_selected_heavy_tests", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_selected_test(repo_root: Path, *, body: str) -> Path:
    test_path = repo_root / "tests" / "integration" / "test_selected.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(body, encoding="utf-8")
    manifest_path = repo_root / "selected-tests.txt"
    manifest_path.write_text("tests/integration/test_selected.py\n", encoding="utf-8")
    return manifest_path


def test_selected_heavy_runner_rejects_skipped_tests(tmp_path: Path) -> None:
    module = _load_runner_module()
    manifest_path = _write_selected_test(
        tmp_path,
        body="import pytest\n\ndef test_selected():\n    pytest.skip('manual only')\n",
    )

    status = module.run_selected_heavy_tests(
        manifest_path=manifest_path,
        junit_path=tmp_path / "selected-tests.xml",
        repo_root=tmp_path,
    )

    assert status == 2


def test_selected_heavy_runner_accepts_executed_tests(tmp_path: Path) -> None:
    module = _load_runner_module()
    manifest_path = _write_selected_test(tmp_path, body="def test_selected():\n    assert True\n")

    status = module.run_selected_heavy_tests(
        manifest_path=manifest_path,
        junit_path=tmp_path / "selected-tests.xml",
        repo_root=tmp_path,
    )

    assert status == 0


def test_selected_heavy_runner_ignores_deselecting_pytest_addopts(tmp_path: Path, monkeypatch) -> None:
    manifest_path = _write_selected_test(
        tmp_path,
        body=("def test_kept():\n    assert True\n\ndef test_must_also_run():\n    assert False\n"),
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_kept")

    status = _load_runner_module().run_selected_heavy_tests(
        manifest_path=manifest_path,
        junit_path=tmp_path / "selected-tests.xml",
        repo_root=tmp_path,
    )

    assert status != 0
