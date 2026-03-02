"""
E2E 测试：流水线料盘搬运流程

测试流程：
1. 初始化数据库连接
2. 设置测试数据（设备和用户）
3. 通过摄像头传感器 API 触发料盘到达事件
4. Celery 异步处理事件
5. 创建并下发搬运指令到机械臂
6. 验证数据库状态

运行前提：
- WES 服务运行在 http://localhost:8001
- Redis 运行在 localhost:6379
- Celery Worker 正在运行
- 摄像头 Mock 服务运行在 http://localhost:8003（含传感器模拟）
- 机械臂 Mock 服务运行在 http://localhost:8004

运行方式：
    pytest tests/e2e/test_conveyor_robot_arm.py -v -s
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 加载环境变量
from dotenv import load_dotenv

load_dotenv(".env")

# ============================================
# 测试配置
# ============================================

WES_BASE_URL = os.getenv("WES_BASE_URL", "http://localhost:8001")
MOCK_CAMERA_URL = "http://localhost:8003"
MOCK_ROBOT_ARM_URL = "http://localhost:8004"

POSTGRES_USER = os.getenv("POSTGRES_USER", "wes_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "wes_db")

DATABASE_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# 测试条码
TEST_BARCODE = "PKG-TEST-001"
TEST_LOCATION = "CONVEYOR-STATION-01"


# ============================================
# Pytest Fixtures
# ============================================


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环（整个测试会话共享）"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def db() -> AsyncGenerator[AsyncSession]:
    """创建数据库会话（每个测试函数独立）"""
    # 创建异步引擎
    engine = create_async_engine(DATABASE_URL, echo=False)

    # 创建会话工厂
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 创建会话
    async with async_session_maker() as session:
        # 导入模型（需要在会话创建后导入，避免循环依赖）
        from src.app.device.models.device import Device

        # 确保表存在
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: sync_conn.execute(text("SELECT 1 FROM wes_sys.devices LIMIT 1")))

        yield session

    # 关闭引擎
    await engine.dispose()


# ============================================
# 辅助函数
# ============================================


async def setup_test_data(db: AsyncSession):
    """设置测试数据（设备和用户）"""
    from src.app.device.models.device import Device

    # 1. 删除现有测试数据
    await db.execute(delete(Device).where(Device.device_id.in_(["CAMERA-CONVEYOR-01", "ROBOT-ARM-01"])))

    # 2. 创建摄像头设备
    camera = Device(
        device_id="CAMERA-CONVEYOR-01",
        device_name="流水线识别点摄像头",
        device_type="CAMERA",
        ip_address="127.0.0.1",
        port=8003,
        protocol="HTTP",
        status="IDLE",
        is_online=True,
    )
    db.add(camera)

    # 3. 创建机械臂设备
    robot = Device(
        device_id="ROBOT-ARM-01",
        device_name="搬运机械臂",
        device_type="ROBOTIC_ARM",
        ip_address="127.0.0.1",
        port=8004,
        protocol="HTTP",
        status="IDLE",
        is_online=True,
    )
    db.add(robot)

    await db.commit()
    print("✅ 测试设备已创建")


async def trigger_camera_sensor(barcode: str, location: str) -> dict:
    """通过摄像头传感器 API 触发料盘到达事件"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{MOCK_CAMERA_URL}/api/v1/sensor/trigger",
            json={"barcode": barcode, "location": location},
        )
        response.raise_for_status()
        return response.json()


async def get_latest_event(db: AsyncSession):
    """获取最新的事件记录"""
    result = await db.execute(
        select(text("*")).order_by(text("id DESC")).limit(1).select_from(text("wes_sys.device_events"))
    )
    row = result.first()
    if row:
        return {
            "id": row[2],  # id
            "device_id": row[3],  # device_id
            "event_type": row[4],  # event_type
            "event_data": row[5],  # event_data
        }
    return None


async def get_latest_command(db: AsyncSession):
    """获取最新的指令记录"""
    result = await db.execute(
        select(text("*")).order_by(text("id DESC")).limit(1).select_from(text("wes_sys.device_commands"))
    )
    row = result.first()
    if row:
        return {
            "command_id": row[7],  # command_id
            "device_id": row[8],  # device_id
            "task_type": row[9],  # task_type
            "status": row[13],  # status
        }
    return None


async def get_command_from_db(db: AsyncSession, command_id: str):
    """从数据库获取指令"""
    result = await db.execute(
        text("SELECT * FROM wes_sys.device_commands WHERE command_id = :cmd_id"), {"cmd_id": command_id}
    )
    row = result.first()
    if row:
        return {
            "command_id": row[7],  # command_id
            "status": row[13],  # status
        }
    return None


async def get_device_status_from_db(db: AsyncSession, device_id: str):
    """从数据库获取设备状态"""
    result = await db.execute(
        text("SELECT device_id, status FROM wes_sys.devices WHERE device_id = :device_id"), {"device_id": device_id}
    )
    row = result.first()
    if row:
        return {
            "device_id": row[0],
            "status": row[1],
        }
    return None


# ============================================
# 测试用例
# ============================================


@pytest.mark.asyncio
async def test_full_conveyor_workflow(db):
    """
    完整流程集成测试

    测试步骤：
    1. 设置测试数据
    2. 通过摄像头传感器 API 触发料盘到达事件
    3. 等待事件记录
    4. 等待 Celery 任务创建指令
    5. 等待指令发送到机械臂
    6. 等待机械臂执行完成
    7. 验证最终状态
    """
    print("\n" + "=" * 60)
    print("开始完整流程测试")
    print("=" * 60)

    # Step 1: 设置测试数据
    print("\n[Step 1] 设置测试数据...")
    await setup_test_data(db)

    # Step 2: 通过摄像头传感器 API 触发料盘到达事件
    print("\n[Step 2] 通过摄像头传感器 API 触发料盘到达事件...")
    sensor_response = await trigger_camera_sensor(TEST_BARCODE, TEST_LOCATION)
    assert sensor_response["event_type"] == "MATERIAL_ARRIVED"
    assert sensor_response["barcode"] == TEST_BARCODE
    assert sensor_response["location"] == TEST_LOCATION
    print(f"✅ 传感器已触发: event_id={sensor_response['event_id']}, barcode={TEST_BARCODE}")

    # Step 3: 等待事件记录
    print("\n[Step 3] 等待事件记录...")
    await asyncio.sleep(0.5)
    event = await get_latest_event(db)
    assert event is not None
    assert event["device_id"] == "CAMERA-CONVEYOR-01"
    assert event["event_type"] == "MATERIAL_ARRIVED"
    print(f"✅ 事件已记录: event_id={event['id']}")

    # Step 4: 等待 Celery 任务创建指令
    print("\n[Step 4] 等待 Celery 任务创建指令...")
    command = None
    for _i in range(30):
        await asyncio.sleep(1)
        command = await get_latest_command(db)
        if command is not None:
            break
    assert command is not None
    print(f"✅ 指令已创建: {command['command_id']}")

    # Step 5: 等待指令发送到机械臂
    print("\n[Step 5] 等待指令发送到机械臂...")
    for _i in range(10):
        await asyncio.sleep(1)
        cmd = await get_command_from_db(db, command["command_id"])
        if cmd and cmd["status"] == "ACKED":
            command = cmd
            break
    assert command["status"] == "ACKED"
    print(f"✅ 指令已确认: status={command['status']}")

    # Step 6: 等待机械臂执行完成
    print("\n[Step 6] 等待机械臂执行完成...")
    for _i in range(20):
        await asyncio.sleep(1)
        cmd = await get_command_from_db(db, command["command_id"])
        if cmd and cmd["status"] == "COMPLETED":
            command = cmd
            break
    assert command["status"] == "COMPLETED"
    print("✅ 指令已完成")

    # Step 7: 验证最终状态
    print("\n[Step 7] 验证最终状态...")
    robot = await get_device_status_from_db(db, "ROBOT-ARM-01")
    assert robot is not None
    assert robot["status"] == "IDLE"
    print(f"✅ 机械臂状态已恢复: status={robot['status']}")

    print("\n" + "=" * 60)
    print("完整流程测试通过!")
    print("=" * 60)


@pytest.mark.asyncio
async def test_sensor_auto_trigger(db):
    """
    传感器自动触发测试

    测试步骤：
    1. 设置测试数据
    2. 启动传感器自动触发（3次，间隔5秒）
    3. 等待所有事件处理完成
    4. 验证创建了3条指令
    """
    print("\n" + "=" * 60)
    print("开始传感器自动触发测试")
    print("=" * 60)

    # Step 1: 设置测试数据
    print("\n[Step 1] 设置测试数据...")
    await setup_test_data(db)

    # Step 2: 启动自动触发
    print("\n[Step 2] 启动传感器自动触发...")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{MOCK_CAMERA_URL}/api/v1/sensor/auto/start",
            json={
                "interval_seconds": 5,
                "max_triggers": 3,
                "location": TEST_LOCATION,
            },
        )
        response.raise_for_status()
        start_result = response.json()
        assert start_result["status"] == "started"
        print(f"✅ 自动触发已启动: {start_result['config']}")

    # Step 3: 等待所有事件和指令创建
    print("\n[Step 3] 等待所有事件和指令创建...")
    commands = []
    for i in range(3):
        # 等待每条指令创建（间隔5秒）
        await asyncio.sleep(7)

        # 获取最新指令
        command = await get_latest_command(db)
        if command:
            # 避免重复添加
            if not any(cmd["command_id"] == command["command_id"] for cmd in commands):
                commands.append(command)
                print(f"✅ 指令 {i + 1}/3 已创建: {command['command_id']}")

    assert len(commands) == 3
    print(f"✅ 共创建了 {len(commands)} 条指令")

    # Step 4: 停止自动触发
    print("\n[Step 4] 停止自动触发...")
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{MOCK_CAMERA_URL}/api/v1/sensor/auto/stop")
        response.raise_for_status()
        stop_result = response.json()
        assert stop_result["status"] == "stopped"
        print(f"✅ 自动触发已停止: total_triggers={stop_result['trigger_count']}")

    # Step 5: 验证传感器状态
    print("\n[Step 5] 验证传感器状态...")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MOCK_CAMERA_URL}/api/v1/sensor/status")
        response.raise_for_status()
        status = response.json()
        assert not status["is_auto_triggering"]
        assert status["trigger_count"] >= 3
        print(f"✅ 传感器状态正确: is_auto_triggering={status['is_auto_triggering']}, trigger_count={status['trigger_count']}")

    print("\n" + "=" * 60)
    print("传感器自动触发测试通过!")
    print("=" * 60)


@pytest.mark.asyncio
async def test_sensor_events_history(db):
    """
    传感器事件历史记录测试

    测试步骤：
    1. 触发多个传感器事件
    2. 查询事件历史
    3. 验证事件记录正确
    """
    print("\n" + "=" * 60)
    print("开始传感器事件历史记录测试")
    print("=" * 60)

    # Step 1: 设置测试数据
    print("\n[Step 1] 设置测试数据...")
    await setup_test_data(db)

    # Step 2: 触发多个事件
    print("\n[Step 2] 触发多个传感器事件...")
    barcodes = ["PKG-001", "PKG-002", "PKG-003"]
    for barcode in barcodes:
        await trigger_camera_sensor(barcode, TEST_LOCATION)
        await asyncio.sleep(0.5)  # 避免时间戳重复
    print(f"✅ 已触发 {len(barcodes)} 个事件")

    # Step 3: 查询事件历史
    print("\n[Step 3] 查询传感器事件历史...")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MOCK_CAMERA_URL}/api/v1/sensor/events?limit=10")
        response.raise_for_status()
        events = response.json()
        assert len(events) >= 3
        print(f"✅ 事件历史记录: 共 {len(events)} 条")

    # Step 4: 验证事件内容
    print("\n[Step 4] 验证事件内容...")
    latest_events = events[-3:]
    for i, event in enumerate(latest_events):
        assert event["event_type"] == "MATERIAL_ARRIVED"
        assert event["barcode"] == barcodes[i]
        assert event["location"] == TEST_LOCATION
        print(f"✅ 事件 {i + 1}: barcode={event['barcode']}, event_id={event['event_id']}")

    print("\n" + "=" * 60)
    print("传感器事件历史记录测试通过!")
    print("=" * 60)


# ============================================
# 运行说明
# ============================================

"""
运行测试前需要启动以下服务：

1. 启动基础设施（如果未运行）
   docker-compose up -d db redis

2. 启动 WES 服务
   uv run uvicorn main:app --reload --port 8001

3. 启动 Celery Worker
   uv run celery -A src.celery_app.app worker --loglevel=info --pool=solo

4. 启动 Mock 服务
   uv run python tests/mock/camera_mock_server.py
   uv run python tests/mock/robot_arm_mock_server.py

运行测试：
    pytest tests/e2e/test_conveyor_robot_arm.py -v -s

运行单个测试：
    pytest tests/e2e/test_conveyor_robot_arm.py::test_full_conveyor_workflow -v -s
    pytest tests/e2e/test_conveyor_robot_arm.py::test_sensor_auto_trigger -v -s
    pytest tests/e2e/test_conveyor_robot_arm.py::test_sensor_events_history -v -s
"""
