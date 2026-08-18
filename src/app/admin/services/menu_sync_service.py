"""
菜单同步服务

将前端 router 中的菜单定义解析并同步到后端数据库。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, tuple_

from src.app.admin.models import Menu, Role, role_menu
from src.app.admin.repositories.menu_repository import MenuRepository, menu_repository
from src.utils.frontend_menu_parser import FrontendMenuDefinition, load_frontend_router_menus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession


_MENU_UPDATE_FIELDS = ("title", "path", "component", "icon", "parent_id", "sort_order", "is_hidden")
RoleMenuMatcher = Callable[[Menu], bool]


def _all_visible(menu: Menu) -> bool:
    return not menu.is_hidden


def _admin_visible(menu: Menu) -> bool:
    return not menu.is_hidden and (menu.name.startswith("admin:") or menu.name == "system:dashboard:menu")


def _biz_visible(menu: Menu) -> bool:
    return not menu.is_hidden and (menu.name.startswith("biz:") or menu.name == "system:dashboard:menu")


def _finance_visible(menu: Menu) -> bool:
    return not menu.is_hidden and menu.name in {"admin:audit:menu", "system:dashboard:menu"}


def _user_visible(menu: Menu) -> bool:
    return not menu.is_hidden and menu.name == "system:dashboard:menu"


_ROLE_MENU_RULES: dict[str, RoleMenuMatcher] = {
    "系统管理员": _all_visible,
    "管理员": _admin_visible,
    "运营人员": _biz_visible,
    "财务人员": _finance_visible,
    "普通用户": _user_visible,
}


@dataclass(slots=True)
class MenuSyncResult:
    """菜单同步结果统计"""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def add_error(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.errors.append({"message": message, "data": data})

    def summary(self) -> str:
        parts = [
            f"✅ 创建: {self.created}",
            f"🔄 更新: {self.updated}",
            f"⏭️  跳过: {self.skipped}",
        ]
        if self.errors:
            parts.append(f"❌ 错误: {len(self.errors)}")
        return "\n".join(parts)


@dataclass(slots=True)
class RoleMenuSyncResult:
    """默认角色菜单收敛结果"""

    added: int = 0
    removed: int = 0
    skipped: int = 0
    roles_processed: int = 0


class MenuSyncService:
    """菜单同步服务"""

    def __init__(self, repo: MenuRepository = menu_repository):
        self.repo = repo

    def load_frontend_menu_definitions(
        self,
        frontend_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ) -> list[FrontendMenuDefinition]:
        """从前端 router 加载菜单定义"""

        return load_frontend_router_menus(frontend_path, manifest_path=manifest_path)

    def load_frontend_menu_payloads(
        self,
        frontend_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """将前端菜单定义转换为数据库写入载荷"""

        return [
            definition.to_model_data()
            for definition in self.load_frontend_menu_definitions(frontend_path, manifest_path=manifest_path)
        ]

    async def sync_menus(
        self,
        db: AsyncSession,
        menu_definitions: Sequence[FrontendMenuDefinition],
        dry_run: bool = False,
        auto_commit: bool = True,
    ) -> MenuSyncResult:
        """同步菜单定义到数据库"""

        result = MenuSyncResult()
        if not menu_definitions:
            return result

        existing_menus = await self.repo.list_for_sync(db)
        existing_by_name = {menu.name: menu for menu in existing_menus}
        resolved_ids = {menu.name: menu.id for menu in existing_menus if menu.id is not None}
        next_temp_id = -1

        mutated = False

        for definition in menu_definitions:
            parent_id = None
            if definition.parent_name:
                parent_id = resolved_ids.get(definition.parent_name)
                if parent_id is None:
                    result.add_error(
                        f"未找到父菜单 `{definition.parent_name}`，无法同步 `{definition.name}`",
                        data={"name": definition.name, "path": definition.path},
                    )
                    continue

            payload = definition.to_model_data(parent_id=parent_id)
            existing = existing_by_name.get(definition.name)

            if existing is None:
                if not dry_run:
                    created = await self.repo.create(db, payload)
                    mutated = True
                    if created and created.id is not None:
                        resolved_ids[definition.name] = created.id
                        existing_by_name[definition.name] = created
                else:
                    resolved_ids[definition.name] = next_temp_id
                    next_temp_id -= 1

                result.created += 1
                continue

            update_data = self._build_update_data(existing, payload)
            existing_id = existing.id
            if not update_data:
                result.skipped += 1
                if existing_id is not None:
                    resolved_ids[definition.name] = existing_id
                continue

            if not dry_run and existing_id is not None:
                _ = await self.repo.update(db, existing_id, update_data)
                mutated = True
                await db.refresh(existing)

            result.updated += 1
            if existing_id is not None:
                resolved_ids[definition.name] = existing_id

        if auto_commit and mutated and not dry_run:
            await db.commit()

        return result

    def preview_rows(self, menu_definitions: Sequence[FrontendMenuDefinition]) -> list[str]:
        """生成菜单预览文本行"""

        rows: list[str] = []
        for definition in menu_definitions:
            hidden = "👁️" if definition.is_hidden else "👁️‍🗨️"
            parent = f" → {definition.parent_name}" if definition.parent_name else ""
            icon = f" [{definition.icon}]" if definition.icon else ""
            rows.extend(
                [
                    f"{hidden} [{definition.sort_order:3d}] {definition.title}{icon}",
                    f"      路径: {definition.path}",
                    f"      标识: {definition.name}{parent}",
                    "",
                ]
            )
        return rows

    async def sync_builtin_role_menus(
        self,
        db: AsyncSession,
        dry_run: bool = False,
        auto_commit: bool = True,
        *,
        exact: bool = False,
        managed_menu_names: set[str] | None = None,
    ) -> RoleMenuSyncResult:
        """按内置角色规则同步默认菜单关联；通用入口默认只增不删。"""

        roles = list((await db.execute(select(Role).where(Role.is_deleted.is_(False)))).scalars().all())
        menus = list((await db.execute(select(Menu).where(Menu.is_deleted.is_(False)))).scalars().all())
        if managed_menu_names is not None:
            menus = [menu for menu in menus if menu.name in managed_menu_names]
        existing_links: set[tuple[int, int]] = {
            (int(role_id), int(menu_id))
            for role_id, menu_id in (await db.execute(select(role_menu.c.role_id, role_menu.c.menu_id))).all()
        }
        role_by_name = {role.name: role for role in roles}

        result = RoleMenuSyncResult()
        new_links: list[dict[str, int]] = []
        expected_links: set[tuple[int, int]] = set()

        for role_name, matcher in _ROLE_MENU_RULES.items():
            role = role_by_name.get(role_name)
            if role is None or role.id is None:
                continue

            result.roles_processed += 1

            for menu in menus:
                if menu.id is None or not matcher(menu):
                    continue

                link_key = (role.id, menu.id)
                expected_links.add(link_key)
                if link_key in existing_links:
                    result.skipped += 1
                    continue

                existing_links.add(link_key)
                new_links.append({"role_id": role.id, "menu_id": menu.id})
                result.added += 1

        extra_links: set[tuple[int, int]] = set()
        if exact:
            builtin_role_ids = {
                role.id
                for role_name, role in role_by_name.items()
                if role_name in _ROLE_MENU_RULES and role.id is not None
            }
            extra_links = {
                link for link in existing_links if link[0] in builtin_role_ids and link not in expected_links
            }
        result.removed = len(extra_links)

        if new_links and not dry_run:
            _ = await db.execute(role_menu.insert(), new_links)
        if extra_links and not dry_run:
            _ = await db.execute(
                delete(role_menu).where(tuple_(role_menu.c.role_id, role_menu.c.menu_id).in_(extra_links))
            )
        if auto_commit and not dry_run and (new_links or extra_links):
            await db.commit()

        return result

    @staticmethod
    def _build_update_data(existing: Menu, payload: dict[str, Any]) -> dict[str, Any]:
        update_data: dict[str, Any] = {}
        for attr_name in _MENU_UPDATE_FIELDS:
            if getattr(existing, attr_name, None) != payload.get(attr_name):
                update_data[attr_name] = payload.get(attr_name)
        if update_data and hasattr(existing, "version"):
            update_data["version"] = existing.version
        return update_data


menu_sync_service = MenuSyncService(menu_repository)

__all__ = ["MenuSyncResult", "MenuSyncService", "RoleMenuSyncResult", "menu_sync_service"]
