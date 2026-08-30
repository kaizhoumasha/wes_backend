"""Transport package import 隔离回归。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


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
