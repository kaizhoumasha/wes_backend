"""菜单持久化删除迁移合同。"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlmodel import SQLModel

import src.app.admin.models

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_FILE = REPO_ROOT / "migrations/versions/20260826_0109_9624cc34fa93_drop_menu_persistence.py"


def _function_body(name: str) -> list[ast.stmt]:
    module = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return function.body[1:] if ast.get_docstring(function) else function.body


def test_upgrade_only_drops_role_menu_links_before_menus() -> None:
    body = _function_body("upgrade")

    assert [ast.unparse(statement) for statement in body] == [
        "op.drop_table('role_menus', schema='wes_sys')",
        "op.drop_table('menus', schema='wes_sys')",
    ]


def test_downgrade_is_explicitly_irreversible() -> None:
    body = _function_body("downgrade")

    assert [ast.unparse(statement) for statement in body] == [
        "raise RuntimeError('menu persistence removal is irreversible')"
    ]


def test_current_sqlmodel_metadata_has_no_menu_persistence_tables() -> None:
    assert "wes_sys.role_menus" not in SQLModel.metadata.tables
    assert "wes_sys.menus" not in SQLModel.metadata.tables
