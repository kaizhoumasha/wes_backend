"""Current PositionProjection writes must stay behind the execution service."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = (REPO_ROOT / "workline_plugins/manual-picking/src/manual_picking/application/scan_flow.py",)
ALLOWED_FILES = {
    REPO_ROOT / "src/app/execution/services/position_projection_service.py",
}
MUTATED_FIELDS = {
    "position_json",
    "position_unknown",
    "arrival_face",
    "source_operation_id",
    "source_transport_task_id",
}


def test_position_projection_mutations_use_the_service_owner() -> None:
    violations: list[str] = []
    for path in PRODUCTION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in MUTATED_FIELDS and path not in ALLOWED_FILES:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{target.lineno}:{target.attr}")
    assert violations == []
