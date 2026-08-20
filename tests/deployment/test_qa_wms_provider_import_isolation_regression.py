"""WMS Provider Mock 的 import 隔离回归。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_wms_provider_mock_import_does_not_load_application_settings() -> None:
    # Regression: ISSUE-002 — provider mock 的纯协议 import 提前装载完整应用 Settings
    # Found by /qa on 2026-08-21
    # Report: .gstack/qa-reports/qa-report-localhost-5173-2026-08-21.md
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from tests.mock.wms_provider_mock_server import app; "
                "assert app; "
                "assert 'src.core.conf' not in sys.modules"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
