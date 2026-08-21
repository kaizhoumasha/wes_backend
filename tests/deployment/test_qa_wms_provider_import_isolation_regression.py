"""WMS Provider Mock 的 import 隔离回归。"""

from __future__ import annotations

import os
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
        env=os.environ | {"WMS_PROVIDER_PROFILE_FILE": ""},
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_transport_package_keeps_lazy_public_exports_compatible() -> None:
    """Lazy package loading must preserve the existing public import surface."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "import src.app.transport as transport\n"
                "assert 'src.app.transport.contracts' not in sys.modules\n"
                "assert 'src.app.transport.composition' not in sys.modules\n"
                "assert transport.TransportCaller.__module__ == 'src.app.transport.contracts'\n"
                "assert 'src.app.transport.contracts' in sys.modules\n"
                "assert 'src.app.transport.composition' not in sys.modules\n"
                "assert transport.TransportRuntime.__module__ == 'src.app.transport.composition'\n"
                "assert 'src.app.transport.composition' in sys.modules\n"
                "try:\n"
                "    transport.DOES_NOT_EXIST\n"
                "except AttributeError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('unknown exports must raise AttributeError')"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
