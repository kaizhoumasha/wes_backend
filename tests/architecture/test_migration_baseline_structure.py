"""唯一初始 migration 的结构合同。"""

from __future__ import annotations

import ast
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "migrations/versions"


def _revision_assignments(revision_path: Path) -> dict[str, object]:
    assignments: dict[str, object] = {}
    for node in ast.parse(revision_path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name = node.target.id
            value = node.value
        else:
            continue
        if name in {"revision", "down_revision"}:
            assignments[name] = ast.literal_eval(value)
    return assignments


def test_revision_identity_parser_accepts_annotated_assignments(tmp_path: Path) -> None:
    revision_path = tmp_path / "initial.py"
    revision_path.write_text(
        'revision: str = "abc123"\ndown_revision: str | None = None\n',
        encoding="utf-8",
    )

    assert _revision_assignments(revision_path) == {"revision": "abc123", "down_revision": None}


def test_migration_history_is_one_initial_revision_with_one_head() -> None:
    revision_paths = sorted(VERSIONS_DIR.glob("*.py"))

    assert len(revision_paths) == 1, f"expected one initial revision, found {len(revision_paths)}"

    assignments = _revision_assignments(revision_paths[0])
    assert assignments["revision"]
    assert assignments["down_revision"] is None

    config = Config(str(REPO_ROOT / "alembic.ini"))
    assert ScriptDirectory.from_config(config).get_heads() == [assignments["revision"]]
