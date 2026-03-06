"""seed initial menus data

Revision ID: b1f9c0e8d2a1
Revises: 20260304_1622
Create Date: 2026-03-06 23:40:00.000000+08:00

"""

from collections.abc import Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1f9c0e8d2a1"
down_revision: Union[str, Sequence[str], None] = "20260304_1622"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MENU_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "dashboard",
        "title": "仪表盘",
        "path": "/dashboard",
        "component": "views/dashboard/Dashboard.vue",
        "icon": "Monitor",
        "sort_order": 10,
        "is_hidden": False,
        "parent_name": None,
    },
    {
        "name": "examples",
        "title": "示例页面",
        "path": "/examples",
        "component": None,
        "icon": "Grid",
        "sort_order": 20,
        "is_hidden": False,
        "parent_name": None,
    },
    {
        "name": "examples:user-form",
        "title": "用户表单",
        "path": "/examples/user-form",
        "component": "views/examples/UserFormExample.vue",
        "icon": "EditPen",
        "sort_order": 10,
        "is_hidden": False,
        "parent_name": "examples",
    },
    {
        "name": "error:403",
        "title": "无权限",
        "path": "/403",
        "component": "views/error/Unauthorized.vue",
        "icon": "Warning",
        "sort_order": 999,
        "is_hidden": True,
        "parent_name": None,
    },
)


def _get_menu_row(conn: sa.engine.Connection, name: str) -> sa.Row | None:
    return conn.execute(
        sa.text(
            """
            SELECT id, tree_path, level
            FROM wes_sys.menus
            WHERE name = :name AND NOT is_deleted
            LIMIT 1
            """
        ),
        {"name": name},
    ).fetchone()


def _upsert_root_menu(conn: sa.engine.Connection, menu: dict[str, Any]) -> int:
    existing = _get_menu_row(conn, menu["name"])
    params = {
        "name": menu["name"],
        "title": menu["title"],
        "path": menu["path"],
        "component": menu["component"],
        "icon": menu["icon"],
        "sort_order": menu["sort_order"],
        "is_hidden": menu["is_hidden"],
    }

    if existing:
        conn.execute(
            sa.text(
                """
                UPDATE wes_sys.menus
                SET title = :title,
                    path = :path,
                    component = :component,
                    icon = :icon,
                    sort_order = :sort_order,
                    is_hidden = :is_hidden,
                    parent_id = NULL,
                    tree_path = '/',
                    level = 1,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {**params, "id": existing.id},
        )
        return int(existing.id)

    conn.execute(
        sa.text(
            """
            INSERT INTO wes_sys.menus (
                name, title, path, component, icon, is_hidden,
                parent_id, tree_path, level, sort_order,
                created_at, updated_at, version, is_deleted
            ) VALUES (
                :name, :title, :path, :component, :icon, :is_hidden,
                NULL, '/', 1, :sort_order,
                NOW(), NOW(), 0, FALSE
            )
            """
        ),
        params,
    )
    created = _get_menu_row(conn, menu["name"])
    if not created:
        raise RuntimeError(f"初始化菜单失败: {menu['name']}")
    return int(created.id)


def _upsert_child_menu(conn: sa.engine.Connection, menu: dict[str, Any], parent_id: int) -> int:
    parent = conn.execute(
        sa.text(
            """
            SELECT tree_path, level
            FROM wes_sys.menus
            WHERE id = :id
            """
        ),
        {"id": parent_id},
    ).fetchone()
    if not parent:
        raise RuntimeError(f"初始化菜单失败，父菜单不存在: {menu['parent_name']}")

    tree_path = f"{parent.tree_path}{parent_id}/"
    level = int(parent.level) + 1
    existing = _get_menu_row(conn, menu["name"])
    params = {
        "name": menu["name"],
        "title": menu["title"],
        "path": menu["path"],
        "component": menu["component"],
        "icon": menu["icon"],
        "sort_order": menu["sort_order"],
        "is_hidden": menu["is_hidden"],
        "parent_id": parent_id,
        "tree_path": tree_path,
        "level": level,
    }

    if existing:
        conn.execute(
            sa.text(
                """
                UPDATE wes_sys.menus
                SET title = :title,
                    path = :path,
                    component = :component,
                    icon = :icon,
                    sort_order = :sort_order,
                    is_hidden = :is_hidden,
                    parent_id = :parent_id,
                    tree_path = :tree_path,
                    level = :level,
                    updated_at = NOW()
                WHERE id = :id
                """
            ),
            {**params, "id": existing.id},
        )
        return int(existing.id)

    conn.execute(
        sa.text(
            """
            INSERT INTO wes_sys.menus (
                name, title, path, component, icon, is_hidden,
                parent_id, tree_path, level, sort_order,
                created_at, updated_at, version, is_deleted
            ) VALUES (
                :name, :title, :path, :component, :icon, :is_hidden,
                :parent_id, :tree_path, :level, :sort_order,
                NOW(), NOW(), 0, FALSE
            )
            """
        ),
        params,
    )
    created = _get_menu_row(conn, menu["name"])
    if not created:
        raise RuntimeError(f"初始化菜单失败: {menu['name']}")
    return int(created.id)


def _bind_all_roles_to_menus(conn: sa.engine.Connection, menu_ids: list[int]) -> None:
    if not menu_ids:
        return

    role_ids = [
        int(row.id)
        for row in conn.execute(
            sa.text(
                """
                SELECT id
                FROM wes_sys.roles
                WHERE NOT is_deleted
                """
            )
        ).fetchall()
    ]

    for role_id in role_ids:
        for menu_id in menu_ids:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO wes_sys.role_menus (role_id, menu_id)
                    SELECT :role_id, :menu_id
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM wes_sys.role_menus
                        WHERE role_id = :role_id AND menu_id = :menu_id
                    )
                    """
                ),
                {"role_id": role_id, "menu_id": menu_id},
            )


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    inserted_or_updated_ids: list[int] = []
    root_ids: dict[str, int] = {}

    for menu in MENU_DEFINITIONS:
        if menu["parent_name"] is None:
            menu_id = _upsert_root_menu(conn, menu)
            root_ids[menu["name"]] = menu_id
            inserted_or_updated_ids.append(menu_id)

    for menu in MENU_DEFINITIONS:
        parent_name = menu["parent_name"]
        if parent_name is None:
            continue
        parent_id = root_ids.get(parent_name)
        if parent_id is None:
            parent_row = _get_menu_row(conn, parent_name)
            if not parent_row:
                raise RuntimeError(f"初始化菜单失败，父菜单不存在: {parent_name}")
            parent_id = int(parent_row.id)
        menu_id = _upsert_child_menu(conn, menu, parent_id)
        inserted_or_updated_ids.append(menu_id)

    _bind_all_roles_to_menus(conn, inserted_or_updated_ids)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    menu_names = [menu["name"] for menu in MENU_DEFINITIONS]

    select_stmt = sa.text(
        """
        SELECT id
        FROM wes_sys.menus
        WHERE name IN :menu_names
        """
    ).bindparams(sa.bindparam("menu_names", expanding=True))
    menu_rows = conn.execute(
        select_stmt,
        {"menu_names": menu_names},
    ).fetchall()

    menu_ids = [int(row.id) for row in menu_rows]
    if menu_ids:
        delete_role_menus_stmt = sa.text(
            """
            DELETE FROM wes_sys.role_menus
            WHERE menu_id IN :menu_ids
            """
        ).bindparams(sa.bindparam("menu_ids", expanding=True))
        conn.execute(
            delete_role_menus_stmt,
            {"menu_ids": menu_ids},
        )
        delete_menus_stmt = sa.text(
            """
            DELETE FROM wes_sys.menus
            WHERE id IN :menu_ids
            """
        ).bindparams(sa.bindparam("menu_ids", expanding=True))
        conn.execute(
            delete_menus_stmt,
            {"menu_ids": menu_ids},
        )
