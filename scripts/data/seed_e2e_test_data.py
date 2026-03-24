"""
E2E 测试数据初始化脚本

仅包含 E2E 测试需要的测试数据：
- 作业线 (WorkLine)
- 设备 (Device)
- API 应用 (APIApplication)
- API 权限 (api:callback:result, api:callback:event)

与系统初始化数据 (seed_initial_data.py) 分离，避免冲突。

运行方式:
    uv run python scripts/data/seed_e2e_test_data.py
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.app.admin.models.perm import Permission
from src.app.api_auth.models.api_application import APIApplication, AppStatus, AppType, ValidityPeriod
from src.app.device.models.device import Device, DeviceProtocol, DeviceStatus, DeviceType
from src.app.workline.models.workline import LineType, WorkLine
from src.core.encryption import encryption_service
from src.database.hooks import HookType


def _disable_audit_hooks(repo) -> None:
    """禁用 Repository 的审计日志 Hook"""
    for hook_type in [HookType.AFTER_CREATE, HookType.AFTER_UPDATE, HookType.AFTER_DELETE]:
        repo.hook_manager.hooks[hook_type] = [
            hook for hook in repo.hook_manager.hooks[hook_type] if hook.priority != 100
        ]


if TYPE_CHECKING:
    from src.core.conf import Settings
else:
    Settings = "Settings"


async def seed_worklines(db: AsyncSession) -> None:
    """初始化 E2E 测试作业线数据"""
    from src.app.workline.repositories.workline_repository import WorkLineRepository

    repo = WorkLineRepository()
    _disable_audit_hooks(repo)

    # 检查是否已存在（查询所有未删除的记录）
    existing_result = await db.execute(
        select(WorkLine).where(
            WorkLine.line_code == "WL-CONVEYOR-01",
            WorkLine.is_deleted.is_(False),
        )
    )
    existing_list = list(existing_result.scalars().all())

    if existing_list:
        print(f"     ℹ️  作业线 WL-CONVEYOR-01 已存在 (共 {len(existing_list)} 条)，跳过创建")
        return

    # 流水线作业线（用于 E2E 测试）
    await repo.create(
        db,
        {
            "line_code": "WL-CONVEYOR-01",
            "line_name": "测试流水线作业线",
            "line_type": LineType.AUTO,
            "zone_name": "测试区域",
            "description": "E2E 测试用流水线作业线，包含识别点摄像头和搬运机械臂",
            "is_active": True,
            "capacity": 1000,
            "sort_order": 1,
        },
    )
    print("     ✅ 创建作业线: WL-CONVEYOR-01")


async def seed_devices(db: AsyncSession) -> None:
    """初始化 E2E 测试设备数据

    根据 Mock 服务器创建对应的设备数据：
    - CAMERA-CONVEYOR-01: 流水线识别点摄像头 (tests/mock/camera_mock_server.py)
    - ROBOT-ARM-01: 搬运机械臂 (tests/mock/robot_arm_mock_server.py)
    """
    from src.app.device.repositories.device_repository import DeviceRepository
    from src.app.workline.repositories.workline_repository import WorkLineRepository

    device_repo = DeviceRepository()
    _disable_audit_hooks(device_repo)

    # 获取作业线（取最新的）
    workline_result = await db.execute(
        select(WorkLine).where(
            WorkLine.line_code == "WL-CONVEYOR-01",
            WorkLine.is_deleted.is_(False),
        ).order_by(WorkLine.id.desc())
    )
    workline = workline_result.scalar_one_or_none()
    if not workline:
        print("     ⚠️  作业线 WL-CONVEYOR-01 不存在，跳过设备创建")
        return

    # 1. 流水线识别点摄像头
    existing_camera_result = await db.execute(
        select(Device).where(
            Device.device_code == "CAMERA-CONVEYOR-01",
            Device.is_deleted.is_(False),
        )
    )
    if not existing_camera_result.scalar_one_or_none():
        await device_repo.create(
            db,
            {
                "device_code": "CAMERA-CONVEYOR-01",
                "device_name": "流水线识别点摄像头",
                "device_type": DeviceType.VISION_CAMERA,
                "work_line_id": workline.id,
                "description": "E2E 测试用识别点摄像头，模拟物料扫码和事件上报",
                "is_active": True,
                "device_role": "SCANNER",
                "role_index": 1,
                "vendor_type": "MOCK",
                "capabilities": ["SCAN", "DETECT"],
                "host": "127.0.0.1",
                "port": 8003,
                "protocol": DeviceProtocol.HTTP,
                "timeout": 10000,
                "device_status": DeviceStatus.IDLE,
                "supported_commands": ["SCAN"],
                "max_concurrent_tasks": 1,
                "idempotency_ttl": 3600,
                "sort_order": 1,
            },
        )
        print("     ✅ 创建设备: CAMERA-CONVEYOR-01")
    else:
        print("     ℹ️  设备 CAMERA-CONVEYOR-01 已存在，跳过")

    # 2. 搬运机械臂
    existing_robot_result = await db.execute(
        select(Device).where(
            Device.device_code == "ROBOT-ARM-01",
            Device.is_deleted.is_(False),
        )
    )
    if not existing_robot_result.scalar_one_or_none():
        await device_repo.create(
            db,
            {
                "device_code": "ROBOT-ARM-01",
                "device_name": "搬运机械臂",
                "device_type": DeviceType.ROBOTIC_ARM,
                "work_line_id": workline.id,
                "description": "E2E 测试用搬运机械臂，模拟物料搬运和结果回调",
                "is_active": True,
                "device_role": "ROBOT_ARM",
                "role_index": 1,
                "upstream_device_id": None,
                "vendor_type": "MOCK",
                "capabilities": ["PICK", "PLACE", "ROTATE"],
                "host": "127.0.0.1",
                "port": 8004,
                "protocol": DeviceProtocol.HTTP,
                "timeout": 10000,
                "device_status": DeviceStatus.IDLE,
                "supported_commands": ["PICK_AND_PLACE", "MOVE", "ROTATE"],
                "max_concurrent_tasks": 1,
                "idempotency_ttl": 3600,
                "sort_order": 2,
            },
        )
        print("     ✅ 创建设备: ROBOT-ARM-01")
    else:
        print("     ℹ️  设备 ROBOT-ARM-01 已存在，跳过")


async def seed_api_applications(db: AsyncSession) -> None:
    """初始化 E2E 测试 API 应用数据

    根据 Mock 服务器使用的 API 凭据创建应用：
    - app_id: app_Gqnvr3dpjGwlrjtO
    - 用于 Mock 服务器调用 WES 回调接口时进行认证
    """
    from src.app.api_auth.models.api_application import APIApplication
    from src.app.api_auth.repositories.app_application_repository import api_app_repository

    repo = api_app_repository
    _disable_audit_hooks(repo)

    # 检查是否已存在（直接查询）
    existing_result = await db.execute(
        select(APIApplication).where(
            APIApplication.app_id == "app_Gqnvr3dpjGwlrjtO",
            APIApplication.is_deleted.is_(False),
        )
    )

    if existing_result.scalar_one_or_none():
        print("     ℹ️  API 应用 app_Gqnvr3dpjGwlrjtO 已存在，跳过创建")
        return

    # 使用与 Mock 服务器相同的 app_id
    app_id = "app_Gqnvr3dpjGwlrjtO"
    app_secret = "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao"  # noqa: S105 - E2E 测试用固定密钥
    app_secret_encrypted = encryption_service.encrypt(app_secret)

    await repo.create(
        db,
        {
            "app_name": "E2E 测试 Mock 设备",
            "app_type": AppType.ThirdParty,
            "app_id": app_id,
            "app_secret_encrypted": app_secret_encrypted,
            "description": "E2E 测试用 Mock 设备应用，用于 camera_mock_server 和 robot_arm_mock_server 调用 WES 回调接口",
            "ip_whitelist": None,  # E2E 测试不限制 IP
            "rate_limit_per_minute": 100,
            "rate_limit_per_hour": 5000,
            "validity_period": ValidityPeriod.NEVER,
            "status": AppStatus.ACTIVE,
            "expires_at": None,
        },
    )
    print("     ✅ 创建 API 应用: app_Gqnvr3dpjGwlrjtO")
    print(f"        📝 app_secret: {app_secret}")


async def seed_api_permissions(db: AsyncSession) -> None:
    """初始化 E2E 测试 API 权限数据

    创建 Mock 服务器调用回调接口需要的权限：
    - api:callback:result: 任务结果回传权限
    - api:callback:event: 设备事件上报权限
    """
    from src.app.admin.repositories.perm_repository import PermissionRepository

    repo = PermissionRepository()
    _disable_audit_hooks(repo)

    permissions_to_create = [
        {
            "name": "api:callback:result",
            "description": "设备任务结果回传权限",
            "type": "app_api",
            "category": "callback",
            "resource": "callback",
            "action": "result",
            "method": "POST",
            "path": "/api/v1/callback/result",
            "sort_order": 1,
        },
        {
            "name": "api:callback:event",
            "description": "设备事件上报权限",
            "type": "app_api",
            "category": "callback",
            "resource": "callback",
            "action": "event",
            "method": "POST",
            "path": "/api/v1/callback/event",
            "sort_order": 2,
        },
    ]

    created_count = 0
    for perm_data in permissions_to_create:
        existing_result = await db.execute(
            select(Permission).where(
                Permission.name == perm_data["name"],
                Permission.is_deleted.is_(False),
            )
        )
        if not existing_result.scalar_one_or_none():
            await repo.create(db, perm_data)
            print(f"     ✅ 创建权限: {perm_data['name']}")
            created_count += 1
        else:
            print(f"     ℹ️  权限 {perm_data['name']} 已存在，跳过")

    if created_count == 0:
        print("     ℹ️  所有 API 权限已存在，跳过创建")


async def seed_api_app_permissions(db: AsyncSession) -> None:
    """为 E2E 测试 API 应用分配权限

    将回调相关权限分配给 E2E 测试应用：
    - api:callback:result
    - api:callback:event
    """
    from src.app.api_auth.models.relationships import api_app_permissions

    # 获取 E2E 测试应用
    app_result = await db.execute(
        select(APIApplication).where(
            APIApplication.app_id == "app_Gqnvr3dpjGwlrjtO",
            APIApplication.is_deleted.is_(False),
        )
    )
    app = app_result.scalar_one_or_none()
    if not app:
        print("     ⚠️  API 应用 app_Gqnvr3dpjGwlrjtO 不存在，跳过权限分配")
        return

    # 获取需要分配的权限
    perm_result = await db.execute(
        select(Permission).where(
            Permission.name.in_(["api:callback:result", "api:callback:event"]),
            Permission.is_deleted.is_(False),
        )
    )
    permissions = list(perm_result.scalars().all())

    if not permissions:
        print("     ⚠️  未找到需要分配的权限，跳过")
        return

    # 检查已存在的权限关联
    existing_result = await db.execute(
        select(api_app_permissions).where(
            api_app_permissions.c.app_id == app.id,
            api_app_permissions.c.permission_id.in_([p.id for p in permissions]),
        )
    )
    existing_pairs = {(row.app_id, row.permission_id) for row in existing_result.all()}

    # 插入新的权限关联
    inserted_count = 0
    for perm in permissions:
        if (app.id, perm.id) not in existing_pairs:
            await db.execute(
                api_app_permissions.insert(),
                {"app_id": app.id, "permission_id": perm.id},
            )
            print(f"     ✅ 分配权限: {perm.name} -> app_Gqnvr3dpjGwlrjtO")
            inserted_count += 1
        else:
            print(f"     ℹ️  权限 {perm.name} 已分配，跳过")

    if inserted_count == 0:
        print("     ℹ️  所有权限已分配，跳过")


async def seed_e2e_test_data(db: AsyncSession) -> None:
    """初始化所有 E2E 测试数据（幂等，可重复运行）"""
    print("🌱 开始初始化 E2E 测试数据...")

    print("  1️⃣ 初始化作业线数据...")
    await seed_worklines(db)
    workline_count_result = await db.execute(select(WorkLine))
    print(f"     📊 作业线数量: {workline_count_result.scalar()}")

    print("  2️⃣ 初始化设备数据...")
    await seed_devices(db)
    device_count_result = await db.execute(select(Device))
    print(f"     📊 设备数量: {device_count_result.scalar()}")

    print("  3️⃣ 初始化 API 应用数据...")
    await seed_api_applications(db)

    print("  4️⃣ 初始化 API 权限数据...")
    await seed_api_permissions(db)

    print("  5️⃣ 分配 API 应用权限...")
    await seed_api_app_permissions(db)

    print("🎉 E2E 测试数据初始化完成！")
    print("\n📦 E2E 测试设备:")
    print("  - WL-CONVEYOR-01: 测试流水线作业线")
    print("  - CAMERA-CONVEYOR-01: 流水线识别点摄像头 (127.0.0.1:8003)")
    print("  - ROBOT-ARM-01: 搬运机械臂 (127.0.0.1:8004)")
    print("\n🔑 API 认证:")
    print("  - app_id: app_Gqnvr3dpjGwlrjtO")
    print("  - app_secret: sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")
    print("\n💡 提示: 此脚本可重复运行，已存在的数据会被跳过")


async def main() -> None:
    """主函数：初始化 E2E 测试数据"""
    from src.core.conf import settings

    # 创建异步引擎
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
    )

    # 创建 Session Maker
    async_session_maker = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    try:
        async with async_session_maker() as db:
            await seed_e2e_test_data(db)
            await db.commit()  # 确保事务提交
    finally:
        await engine.dispose()


if __name__ == "__main__":
    """直接运行此脚本时执行初始化"""
    asyncio.run(main())
