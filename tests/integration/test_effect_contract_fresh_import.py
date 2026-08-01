"""EFFECT 合同模块 fresh interpreter import 的 HEAVY 合同。"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "src.app.runtime.orchestration.effect_state_contract",
        "src.app.runtime.orchestration.effect_bridges",
    ],
)
def test_effect_contract_modules_import_in_fresh_interpreter(module_name: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
