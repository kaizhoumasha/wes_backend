"""平台扩展性能预算的默认快速门禁；不访问数据库或外部服务。"""

from __future__ import annotations

import subprocess
import sys
import time

from src.app.runtime.system_capabilities.generated_index import SYSTEM_CAPABILITY_INDEX_DIGEST
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST

COLD_IMPORT_BUDGET_MS = 1_500.0
POSTGRESQL_HEAVY_BUDGETS_MS = {
    "single_runtime_inbox_no_query": 500.0,
    "formal_callback_wms_query": 800.0,
    "outbox_enqueue": 50.0,
    "recorded_replay": 20.0,
}


def test_generated_indexes_cold_import_within_budget() -> None:
    started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import src.app.runtime.system_capabilities.generated_index; "
            "import src.app.runtime.workline_plugins.generated_index",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    measured_ms = (time.perf_counter() - started) * 1_000
    assert measured_ms <= COLD_IMPORT_BUDGET_MS


def test_performance_budget_contract_is_explicit_and_positive() -> None:
    assert len(WORKLINE_PLUGIN_INDEX_DIGEST) == 64
    assert len(SYSTEM_CAPABILITY_INDEX_DIGEST) == 64
    assert set(POSTGRESQL_HEAVY_BUDGETS_MS) == {
        "single_runtime_inbox_no_query",
        "formal_callback_wms_query",
        "outbox_enqueue",
        "recorded_replay",
    }
    assert all(0 < value <= 1_000 for value in POSTGRESQL_HEAVY_BUDGETS_MS.values())
