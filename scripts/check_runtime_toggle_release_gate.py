"""CI entrypoint for Phase 3 runtime toggle release governance."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


PASSED_CHECKS_ENV = "WES_RUNTIME_TOGGLE_PASSED_CHECKS"
RELEASE_DATE_ENV = "WES_RUNTIME_TOGGLE_RELEASE_DATE"


def _split_checks(values: list[str]) -> frozenset[str]:
    checks: list[str] = []
    for value in values:
        checks.extend(check.strip() for check in value.split(",") if check.strip())
    return frozenset(checks)


def _parse_today(raw_value: str | None) -> date:
    if not raw_value:
        return datetime.now(UTC).date()
    return date.fromisoformat(raw_value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check typed runtime toggles before release.")
    parser.add_argument(
        "--today",
        default=os.getenv(RELEASE_DATE_ENV),
        help=f"Release date in YYYY-MM-DD format; defaults to today or ${RELEASE_DATE_ENV}.",
    )
    parser.add_argument(
        "--passed-check",
        action="append",
        default=[],
        help=f"Passed test_matrix check id; can be repeated or comma-separated. ${PASSED_CHECKS_ENV} is also read.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    from src.core.runtime_toggle_catalog import RUNTIME_TOGGLES
    from src.core.runtime_toggle_release_gate import RuntimeToggleReleaseGate

    args = build_parser().parse_args(argv)
    passed_checks = _split_checks([os.getenv(PASSED_CHECKS_ENV, ""), *args.passed_check])
    decision = RuntimeToggleReleaseGate(RUNTIME_TOGGLES).evaluate(
        today=_parse_today(args.today),
        passed_checks=passed_checks,
    )
    if not decision.ready:
        print(f"runtime toggle release gate blocked: {decision.reason}", file=sys.stderr)
        if decision.toggle_name:
            print(f"toggle: {decision.toggle_name}", file=sys.stderr)
        if decision.missing_checks:
            print(f"missing_checks: {','.join(decision.missing_checks)}", file=sys.stderr)
        return 1

    print(f"runtime toggle release gate passed: {len(RUNTIME_TOGGLES)} toggles checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
