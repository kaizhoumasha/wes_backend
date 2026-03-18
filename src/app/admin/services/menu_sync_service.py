"""
菜单同步服务

将前端 router 中的菜单定义解析并同步到后端数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from src.app.admin.models.menu import Menu
from src.app.admin.repositories.menu_repository import MenuRepository, menu_repository
from src.utils.frontend_menu_parser import FrontendMenuDefinition, load_frontend_router_menus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession


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


class MenuSyncService:
    """菜单同步服务"""

    def __init__(self, repo: MenuRepository = menu_repository):
        self.repo = repo

    def load_frontend_menu_definitions(self, frontend_path: str | Path | None = None) -> list[FrontendMenuDefinition]:
        """从前端 router 加载菜单定义"""

        return load_frontend_router_menus(frontend_path)

    def load_frontend_menu_payloads(self, frontend_path: str | Path | None = None) -> list[dict[str, Any]]:
        """将前端菜单定义转换为数据库写入载荷"""

        return [definition.to_model_data() for definition in self.load_frontend_menu_definitions(frontend_path)]

    async def sync_menus(
        self,
        db: AsyncSession,
        menu_definitions: Sequence[FrontendMenuDefinition],
        dry_run: bool = False,
    ) -> MenuSyncResult:
        """同步菜单定义到数据库"""

        result = MenuSyncResult()
        if not menu_definitions:
            return result

        existing_result = await db.execute(select(Menu).order_by(Menu.sort_order, Menu.id))  # type: ignore[arg-type]
        existing_menus = list(existing_result.scalars().all())
        existing_by_name = {menu.name: menu for menu in existing_menus}
        resolved_ids = {menu.name: menu.id for menu in existing_menus if menu.id is not None}
        next_temp_id = -1

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
                await self.repo.update(db, existing_id, update_data)
                await db.refresh(existing)

            result.updated += 1
            if existing_id is not None:
                resolved_ids[definition.name] = existing_id

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

    @staticmethod
    def _build_update_data(existing: Menu, payload: dict[str, Any]) -> dict[str, Any]:
        update_data: dict[str, Any] = {}
        for attr_name in ("title", "path", "component", "icon", "parent_id", "sort_order", "is_hidden"):
            if getattr(existing, attr_name, None) != payload.get(attr_name):
                update_data[attr_name] = payload.get(attr_name)
        if update_data and hasattr(existing, "version"):
            update_data["version"] = existing.version
        return update_data


menu_sync_service = MenuSyncService(menu_repository)

__all__ = ["MenuSyncResult", "MenuSyncService", "menu_sync_service"]
