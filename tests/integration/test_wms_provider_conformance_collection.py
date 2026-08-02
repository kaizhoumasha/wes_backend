"""WMS Provider conformance fixture 的 pytest collection fail-closed 合同。"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_incomplete_fixture_module_aborts_pytest_collection(tmp_path: Path) -> None:
    probe = tmp_path / "test_incomplete_wms_fixture_collection.py"
    probe.write_text(
        """
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from tests.support.wms_conformance_runner import build_operation_fixture_matrix

WMS_OPERATION_FIXTURE_MATRIX = build_operation_fixture_matrix(
    operations=WMS_OPERATIONS,
    request_fixtures=(),
    result_fixtures=(),
    reject_fixtures=(),
    identity_mismatch_fixtures=(),
)
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(probe)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "fixture identities must exactly match the operation registry" in (completed.stdout + completed.stderr)
