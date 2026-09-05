"""Device Repository 层"""

from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.device import Device
from src.database.base_repository import BaseRepository
from src.utils.device_cache import workline_device_cache


class DeviceRepository(BaseRepository[Device]):
    """设备数据访问层"""

    def __init__(self) -> None:
        """初始化设备仓库"""
        super().__init__(Device)

    async def get_by_device_code(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> Device | None:
        """根据设备编码查询"""
        result = await db.execute(
            select(Device).where(
                Device.device_code == device_code,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
        )
        return result.scalar_one_or_none()

    async def get_by_device_code_for_update(
        self,
        db: AsyncSession,
        device_code: str,
    ) -> Device | None:
        """根据设备编码查询并锁定设备行，用于真实设备派发前的串行化治理。"""

        result = await db.execute(
            select(Device)
            .where(
                Device.device_code == device_code,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_topology_identity(
        self,
        db: AsyncSession,
        device_id: int,
    ) -> tuple[int | None, bool] | None:
        """绕过 ORM identity map 读取最新归属与删除态，供 advisory 锁后并发复核。"""

        columns = cast("Any", Device).__table__.c
        result = await db.execute(select(columns.work_line_id, columns.is_deleted).where(columns.id == device_id))
        row = result.one_or_none()
        if row is None:
            return None
        return row.work_line_id, bool(row.is_deleted)

    async def get_runtime_effect_target_for_update(
        self,
        db: AsyncSession,
        *,
        target_device_id: int | None,
        target_device_code: str | None,
        expected_workline_id: int,
    ) -> Device | None:
        """按固定身份锁定 Runtime 副作用目标；调用方事务负责提交或回滚。"""

        if (target_device_id is None) == (target_device_code is None):
            raise ValueError("runtime device target requires exactly one identity")
        columns = cast("Any", Device).__table__.c
        identity_clause = (
            columns.id == target_device_id
            if target_device_id is not None
            else columns.device_code == target_device_code
        )
        result = await db.execute(
            select(Device)
            .where(
                identity_clause,
                columns.work_line_id == expected_workline_id,
                columns.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_work_line_id(
        self,
        db: AsyncSession,
        work_line_id: int,
    ) -> list[Device]:
        """根据作业线 ID 查询所有设备。"""
        result = await db.execute(
            select(Device)
            .where(
                Device.work_line_id == work_line_id,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
            .order_by(
                Device.sort_order.asc(),  # type: ignore[arg-type]
                Device.role_index.asc(),  # type: ignore[arg-type]
                Device.id.asc(),  # type: ignore[arg-type]
            )
        )
        return list(result.scalars().all())

    async def get_by_work_line_id_for_update(
        self,
        db: AsyncSession,
        work_line_id: int,
    ) -> list[Device]:
        """锁定作业线设备事实，防止运行态版本在决策校验后、事务提交前漂移。"""

        result = await db.execute(
            select(Device)
            .where(
                Device.work_line_id == work_line_id,  # type: ignore[arg-type]
                Device.is_deleted.is_(False),  # type: ignore[arg-type]
            )
            .order_by(
                Device.sort_order.asc(),  # type: ignore[arg-type]
                Device.role_index.asc(),  # type: ignore[arg-type]
                Device.id.asc(),  # type: ignore[arg-type]
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def list_for_workline_configuration_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        device_codes: tuple[str, ...],
    ) -> list[Device]:
        """按主键顺序锁定目标工作线当前设备与提交设备的并集。"""

        columns = cast("Any", Device).__table__.c
        predicates = [columns.work_line_id == workline_id]
        if device_codes:
            predicates.append(columns.device_code.in_(device_codes))
        result = await db.execute(
            select(Device)
            .where(
                columns.is_deleted.is_(False),
                or_(*predicates),
            )
            .order_by(columns.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        return list(result.scalars())

    async def after_device_change(
        self,
        _db: AsyncSession,
        old_work_line_id: int | None,
        new_work_line_id: int | None,
    ) -> None:
        """设备变更后失效缓存

        当设备的 work_line_id 变化（绑定、解绑、更新）时调用，
        失效相关工作线的设备缓存。

        Args:
            _db: 数据库会话（为保持仓储 Hook 签名一致而保留）
            old_work_line_id: 变更前的工作线 ID（可 None）
            new_work_line_id: 变更后的工作线 ID（可 None）
        """
        # 失效旧工作线缓存
        if old_work_line_id is not None:
            workline_device_cache.invalidate(old_work_line_id)

        # 失效新工作线缓存
        if new_work_line_id is not None and new_work_line_id != old_work_line_id:
            workline_device_cache.invalidate(new_work_line_id)


# 创建单例
device_repository = DeviceRepository()
