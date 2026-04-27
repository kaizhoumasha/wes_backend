"""
SMT 粗分机 E2E 测试 Fixtures

提供 Mock 服务启动/停止、数据库初始化和 WES 客户端等测试基础设施。

运行方式:
    # 启动 Mock 服务并运行 E2E 测试
    uv run pytest tests/e2e/smt_classifier/ -v

    # 带详细日志
    uv run pytest tests/e2e/smt_classifier/ -v --log-cli-level=INFO

环境变量:
    WES_BASE_URL: WES 后端地址 (默认 http://localhost:8001)
    MOCK_STARTUP_TIMEOUT: Mock 服务启动超时 (默认 30 秒)
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import httpx
import pytest

if TYPE_CHECKING:
    import asyncpg
import pytest_asyncio

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 尝试加载 E2E 测试环境变量文件
_e2e_env_file = Path(__file__).parent / ".env.e2e"
if _e2e_env_file.exists():
    import dotenv

    dotenv.load_dotenv(_e2e_env_file)
    logger = logging.getLogger(__name__)
    logger.info(f"已加载 E2E 环境变量: {_e2e_env_file}")

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from tests.mock.smt_classifier.run_all import MOCK_SERVICES, run_server

logger = logging.getLogger(__name__)

# 配置
WES_BASE_URL = os.getenv("WES_BASE_URL", "http://localhost:8001")
MOCK_STARTUP_TIMEOUT = int(os.getenv("MOCK_STARTUP_TIMEOUT", "30"))

# Mock 服务健康检查 URL（避免重复定义）
MOCK_HEALTH_URLS = [
    ("Pipeline Mock", "http://127.0.0.1:8005/"),
    ("Arm Mock (ARM01)", "http://127.0.0.1:8006/"),
    ("Arm Mock (ARM02)", "http://127.0.0.1:8007/"),
]


# ==================== Mock 服务进程管理 ====================


class MockServiceManager:
    """Mock 服务管理器

    负责启动、监控和停止所有 Mock 服务进程。
    """

    def __init__(self) -> None:
        self._processes: list[multiprocessing.Process] = []
        self._is_running = False
        self._env_vars: dict[str, str] = {}

    def _load_env_from_file(self) -> dict[str, str]:
        """从 .env.e2e 文件加载环境变量"""
        env_vars = {}
        _e2e_env_file = Path(__file__).parent / ".env.e2e"
        if _e2e_env_file.exists():
            content = _e2e_env_file.read_text(encoding="utf-8")
            for raw_line in content.splitlines():
                line = raw_line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        return env_vars

    async def start_all(self) -> None:
        """启动所有 Mock 服务"""
        if self._is_running:
            logger.warning("Mock 服务已经在运行中")
            return

        logger.info("=" * 60)
        logger.info("启动 SMT 粗分机 Mock 服务")
        logger.info("=" * 60)

        # 先检查服务是否已经运行
        if await self._check_services_running():
            logger.info("✓ 检测到 Mock 服务已启动，跳过启动流程")
            self._is_running = True
            return

        # 从 .env.e2e 加载环境变量
        self._env_vars = self._load_env_from_file()

        # 设置回调地址环境变量
        self._env_vars.setdefault("WES_EVENT_CALLBACK_URL", f"{WES_BASE_URL}/api/v1/callback/event")
        self._env_vars.setdefault("WES_RESULT_CALLBACK_URL", f"{WES_BASE_URL}/api/v1/callback/result")

        # 确保 API 凭证已设置
        if "API_APP_ID" not in self._env_vars:
            logger.warning("API_APP_ID 未在 .env.e2e 中设置，使用默认值")
            self._env_vars["API_APP_ID"] = "app_Gqnvr3dpjGwlrjtO"
        if "API_APP_SECRET" not in self._env_vars:
            logger.warning("API_APP_SECRET 未在 .env.e2e 中设置，使用默认值")
            self._env_vars["API_APP_SECRET"] = "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao"

        # 更新当前进程的环境变量
        for key, value in self._env_vars.items():
            os.environ[key] = value

        logger.info(f"API App ID: {self._env_vars.get('API_APP_ID', '未设置')}")
        logger.info(f"WES Event Callback: {self._env_vars.get('WES_EVENT_CALLBACK_URL')}")
        logger.info(f"WES Result Callback: {self._env_vars.get('WES_RESULT_CALLBACK_URL')}")

        for service in MOCK_SERVICES:
            process = multiprocessing.Process(
                target=run_server,
                args=(
                    service["module"],
                    service["app_attr"],
                    service["host"],
                    service["port"],
                    service["device_code"],
                ),
                kwargs={"env_vars": self._env_vars},  # 传递环境变量
                name=service["name"],
            )
            process.start()
            self._processes.append(process)
            logger.info(f"已启动进程: {service['name']} (PID: {process.pid})")

        # 等待服务启动并健康检查
        await self._wait_for_healthy()
        self._is_running = True

        logger.info("-" * 60)
        logger.info(f"所有 Mock 服务已启动，共 {len(self._processes)} 个")
        logger.info("=" * 60)

    async def stop_all(self) -> None:
        """停止所有 Mock 服务"""
        if not self._is_running:
            return

        # 检查是否是我们启动的服务
        if not self._processes:
            # 服务不是我们启动的，不停止
            logger.info("=" * 60)
            logger.info("Mock 服务由外部管理，不停止服务")
            logger.info("=" * 60)
            return

        logger.info("=" * 60)
        logger.info("正在停止所有 Mock 服务...")
        logger.info("-" * 60)

        for process in self._processes:
            if process.is_alive():
                logger.info(f"发送 SIGTERM 到进程 {process.name} (PID: {process.pid})")
                process.terminate()

        # 等待进程结束
        for process in self._processes:
            process.join(timeout=5)
            if process.is_alive():
                logger.warning(f"进程 {process.name} 未响应，强制终止")
                process.kill()
                process.join()
            else:
                logger.info(f"进程 {process.name} 已停止")

        self._processes = []
        self._is_running = False

        logger.info("-" * 60)
        logger.info("所有 Mock 服务已停止")
        logger.info("=" * 60)

    async def _check_services_running(self) -> bool:
        """检查 Mock 服务是否已经在运行"""
        all_running = True
        for name, url in MOCK_HEALTH_URLS:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(url)
                    if response.status_code == 200:
                        logger.info(f"✓ {name} 已运行")
                    else:
                        all_running = False
                        break
            except Exception:
                all_running = False
                break

        return all_running

    async def _wait_for_healthy(self) -> None:
        """等待所有服务健康检查通过"""
        deadline = time.time() + MOCK_STARTUP_TIMEOUT

        for name, url in MOCK_HEALTH_URLS:
            while time.time() < deadline:
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        response = await client.get(url)
                        if response.status_code == 200:
                            logger.info(f"✓ {name} 健康检查通过")
                            break
                except Exception:
                    pass

                await asyncio.sleep(0.5)
            else:
                raise TimeoutError(f"{name} 在 {MOCK_STARTUP_TIMEOUT} 秒内未能启动")


# 全局服务管理器实例
_service_manager: MockServiceManager | None = None


def _get_service_manager() -> MockServiceManager:
    """获取全局服务管理器实例"""
    global _service_manager
    if _service_manager is None:
        _service_manager = MockServiceManager()
    return _service_manager


# ==================== Pytest Fixtures ====================


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环（覆盖默认的 pytest-asyncio）"""
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def mock_services() -> AsyncGenerator[MockServiceManager]:
    """会话级别的 Mock 服务 Fixture

    自动启动所有 Mock 服务，会话结束时自动停止。
    """
    # 设置 multiprocessing 启动方式（macOS 需要）
    multiprocessing.set_start_method("spawn", force=True)

    manager = _get_service_manager()
    await manager.start_all()
    yield manager
    await manager.stop_all()


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def clean_mock_state() -> AsyncGenerator[None]:
    """清理 Mock 服务状态的 Fixture

    每个测试函数执行前调用 Mock 服务的清理接口，确保状态干净。
    """
    # 重置 Pipeline Mock 状态
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # 停止自动触发（如果正在运行）
            try:
                await client.post("http://127.0.0.1:8005/debug/auto/stop")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"清理 Pipeline Mock 状态失败: {e}")

    # 重置 Arm Mock 状态
    for port in [8006, 8007]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # 停止自动执行（如果正在运行）
                try:
                    await client.post(f"http://127.0.0.1:{port}/debug/auto/stop")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"清理 Arm Mock (port {port}) 状态失败: {e}")

    yield


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def wes_client() -> AsyncGenerator[httpx.AsyncClient]:
    """WES API 客户端 Fixture"""
    async with httpx.AsyncClient(
        base_url=WES_BASE_URL,
        timeout=30.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def pipeline_client() -> AsyncGenerator[httpx.AsyncClient]:
    """Pipeline Mock 客户端 Fixture"""
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8005",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def arm01_client() -> AsyncGenerator[httpx.AsyncClient]:
    """ARM01 Mock 客户端 Fixture"""
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8006",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def arm02_client() -> AsyncGenerator[httpx.AsyncClient]:
    """ARM02 Mock 客户端 Fixture"""
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8007",
        timeout=10.0,
        headers={"Content-Type": "application/json"},
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def db_conn() -> AsyncGenerator[asyncpg.Connection]:
    """数据库连接 Fixture"""
    import asyncpg
    import dotenv

    dotenv.load_dotenv()

    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "wes_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "wes_db"),
    )
    yield conn
    await conn.close()


async def wait_for_session_completed(
    conn: asyncpg.Connection,
    timeout_seconds: int = 30,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """轮询等待会话完成

    Args:
        conn: 数据库连接
        timeout_seconds: 超时秒数
        poll_interval: 轮询间隔（秒）

    Returns:
        最新会话记录

    Raises:
        TimeoutError: 超时未完成
    """
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        session = await conn.fetchrow(
            """
            SELECT id, status, step_code, failure_domain, failure_code
            FROM wes_biz.workline_sessions
            ORDER BY id DESC
            LIMIT 1
            """
        )

        if session and session["status"] == "COMPLETED":
            return dict(session)

        await asyncio.sleep(poll_interval)

    raise TimeoutError(f"会话在 {timeout_seconds} 秒内未完成")


async def get_session_commands(
    conn: asyncpg.Connection,
    session_id: str,
) -> list[dict[str, Any]]:
    """获取会话的所有命令记录

    Args:
        conn: 数据库连接
        session_id: 会话ID

    Returns:
        命令记录列表
    """
    commands = await conn.fetch(
        """
        SELECT dc.id, dc.command_code, dc.task_type, d.device_code, dc.status, dc.result
        FROM wes_biz.device_commands dc
        JOIN wes_biz.devices d ON dc.device_id = d.id
        WHERE dc.session_id = $1
        ORDER BY dc.id
        """,
        session_id,
    )
    return [dict(cmd) for cmd in commands]


# ==================== Mock 设备 Fixtures ====================


@pytest.fixture(scope="function")
def mock_input_arm_device() -> MagicMock:
    """模拟进料机械臂设备"""
    device = MagicMock()
    device.id = 1001
    device.device_code = "ARM01"
    device.device_name = "进料机械臂"
    device.device_role = "INPUT_ARM"
    device.device_type = "ROBOTIC_ARM"
    device.ip_address = "127.0.0.1"
    device.port = 8006
    device.is_online = True
    return device


@pytest.fixture(scope="function")
def mock_output_arm_device() -> MagicMock:
    """模拟出料机械臂设备"""
    device = MagicMock()
    device.id = 1002
    device.device_code = "ARM02"
    device.device_name = "出料机械臂"
    device.device_role = "OUTPUT_ARM"
    device.device_type = "ROBOTIC_ARM"
    device.ip_address = "127.0.0.1"
    device.port = 8007
    device.is_online = True
    return device


@pytest.fixture(scope="function")
def mock_pipeline_device() -> MagicMock:
    """模拟流水线设备"""
    device = MagicMock()
    device.id = 1003
    device.device_code = "PIPELINE01"
    device.device_name = "SMT 粗分机流水线"
    device.device_role = "CONVEYOR"
    device.device_type = "PIPELINE"
    device.ip_address = "127.0.0.1"
    device.port = 8005
    device.is_online = True
    return device


@pytest.fixture(scope="function")
def mock_devices(
    mock_input_arm_device: MagicMock,
    mock_output_arm_device: MagicMock,
    mock_pipeline_device: MagicMock,
) -> dict[str, MagicMock]:
    """所有模拟设备字典"""
    return {
        "INPUT_ARM": mock_input_arm_device,
        "OUTPUT_ARM": mock_output_arm_device,
        "CONVEYOR": mock_pipeline_device,
    }


@pytest.fixture(scope="function")
def mock_plugin_context(mock_devices: dict[str, MagicMock]) -> MagicMock:
    """模拟插件上下文"""
    from datetime import datetime

    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.id = 2001
    ctx.session.workline_id = 3001
    ctx.session.status = "NEW"
    ctx.session.context_json = {}
    ctx.session.trace_id = "test-trace-001"

    ctx.workline = MagicMock()
    ctx.workline.id = 3001
    ctx.workline.line_name = "SMT粗分线-左"
    ctx.workline.line_code = "SMT-LEFT-01"
    ctx.workline.config = {}

    ctx.devices_by_role = mock_devices

    def mock_get_device_by_role(role: str, _index: int = 0) -> MagicMock | None:
        return mock_devices.get(role)

    ctx.get_device_by_role = mock_get_device_by_role
    ctx.logger = logging.getLogger("mock_plugin_context")
    ctx.clock = datetime.now
    ctx.trace_id = "test-trace-001"

    return ctx


# ==================== 测试标记 ====================


def pytest_configure(config: pytest.Config) -> None:
    """配置 pytest"""
    config.addinivalue_line(
        "markers",
        "e2e: marks tests as end-to-end tests (deselect with '-m \"not e2e\"')",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )
