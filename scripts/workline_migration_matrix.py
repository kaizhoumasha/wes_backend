#!/usr/bin/env python3
"""聚合跨环境 WorkLine inventory 与 digest-bound 批准证据。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.app.workline.models import (  # noqa: E402
    WorklineInventoryApprovalEvidence,
    WorklineMigrationInventoryReport,
)
from src.app.workline.services import (  # noqa: E402
    WorklineMigrationMatrixInvariantError,
    WorklineMigrationMatrixService,
)
from src.utils.timezone import timezone  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_INVENTORY_BLOCKED = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="聚合跨环境 WorkLine migration matrix")
    parser.add_argument(
        "--inventory-report",
        action="append",
        required=True,
        type=Path,
        help="单环境 inventory JSON；可重复",
    )
    parser.add_argument(
        "--approval",
        action="append",
        default=[],
        type=Path,
        help="绑定 inventory digest 的批准证据 JSON；可重复",
    )
    parser.add_argument(
        "--required-environment",
        action="append",
        required=True,
        help="本次矩阵必须覆盖的环境；可重复",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=f"inventory gate 未通过时退出 {EXIT_INVENTORY_BLOCKED}",
    )
    return parser


def _load_json_models(paths: Sequence[Path], model: type) -> tuple[object, ...]:
    return tuple(model.model_validate_json(path.read_text(encoding="utf-8")) for path in paths)


def run(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] = timezone.now_utc,
) -> int:
    args = _build_parser().parse_args(argv)
    try:
        inventories = _load_json_models(args.inventory_report, WorklineMigrationInventoryReport)
        approvals = _load_json_models(args.approval, WorklineInventoryApprovalEvidence)
        matrix = WorklineMigrationMatrixService(clock=clock).build_matrix(
            inventories=inventories,
            approvals=approvals,
            required_environments=args.required_environment,
        )
    except (OSError, UnicodeError, ValidationError, WorklineMigrationMatrixInvariantError):
        print("migration matrix 生成失败；请检查 inventory、批准证据和文件编码", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    print(matrix.model_dump_json(indent=2))
    if args.check and not matrix.inventory_gate_ready:
        return EXIT_INVENTORY_BLOCKED
    return EXIT_OK


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
