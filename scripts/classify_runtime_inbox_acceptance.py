#!/usr/bin/env python3
"""从 HEAVY selector manifest 决定 RuntimeInbox PostgreSQL 验收强度。"""

from __future__ import annotations

import argparse
from pathlib import Path

MIGRATION_OWNER = "tests/integration/test_runtime_inbox_migration_postgresql.py"
CORRECTNESS_OWNERS = frozenset(
    {
        "tests/integration/test_runtime_inbox_processing_postgresql.py",
        "tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py",
        "tests/load/test_runtime_inbox_claim_benchmark.py",
    }
)


def classify_runtime_inbox_acceptance(selected_heavy_tests: list[str]) -> str:
    selected = set(selected_heavy_tests)
    if MIGRATION_OWNER in selected:
        return "full"
    if selected & CORRECTNESS_OWNERS:
        return "correctness"
    return "none"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    selected = [line.strip() for line in arguments.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(classify_runtime_inbox_acceptance(selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
