from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api_auth.models import APIApplication
from src.database.base_repository import BaseRepository


class APIAppRepository(BaseRepository[APIApplication]):
    """API 应用仓库类"""

    async def get_by_app_id(self, db: AsyncSession, app_id: str) -> APIApplication | None:
        """
        根据 app_id 获取应用（排除已删除）

        Args:
            db: 数据库会话
            app_id: 应用 ID

        Returns:
            应用对象或 None
        """
        result = await db.execute(
            select(APIApplication)
            .where(APIApplication.app_id == app_id)
            .where(APIApplication.is_deleted.is_(False))  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def assign_permissions(
        self,
        db: AsyncSession,
        app_id: int,
        permission_ids: list[int],
    ) -> None:
        """
        分配权限给应用

        Args:
            db: 数据库会话
            app_id: 应用 ID
            permission_ids: 权限 ID 列表
        """
        from src.app.api_auth.models.relationships import api_app_permissions

        # 删除旧的权限关联
        await db.execute(
            api_app_permissions.delete().where(api_app_permissions.c.app_id == app_id)
        )

        # 插入新的权限关联
        if permission_ids:
            await db.execute(
                api_app_permissions.insert(),
                [{"app_id": app_id, "permission_id": pid} for pid in permission_ids],
            )

        await db.commit()


api_app_repository = APIAppRepository(APIApplication)
