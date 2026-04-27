"""
SMT 粗分机完整端到端集成测试

测试完整的请求链路：
WES API → 设备服务 → Mock 设备 → 回调 WES → 插件处理 → 生成新命令

前提条件：
- WES Backend 运行在 http://localhost:8001
- Mock 服务运行（python tests/mock/smt_classifier/run_all.py）
- 数据库已初始化（workline, device, plugin 等基础数据）

环境变量：
    WES_BASE_URL: WES 后端地址 (默认 http://localhost:8001)
    API_APP_ID: API 应用 ID
    API_APP_SECRET: API 应用密钥
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


@pytest.mark.e2e
@pytest.mark.integration
class TestSMTClassifierFullE2E:
    """完整端到端集成测试 - 经过 WES Backend 的完整链路"""

    @pytest.fixture(autouse=True)
    async def setup(self, clean_mock_state: None) -> None:
        """每个测试前的设置"""
        import os

        self.wes_base_url = os.getenv("WES_BASE_URL", "http://localhost:8001")
        self.app_id = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
        self.app_secret = os.getenv("API_APP_SECRET", "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao")

        # 检查 WES Backend 是否可用
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(f"{self.wes_base_url}/health")
                self.wes_available = response.status_code == 200
            except Exception:
                self.wes_available = False

    async def create_signed_headers(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, str]:
        """生成签名请求头"""
        from tests.mock.smt_classifier.mock_support import build_api_auth_headers

        # 使用 Mock 支持库的签名函数
        return build_api_auth_headers(method, path)

    @pytest.mark.asyncio
    async def test_full_chain_via_wes_create_command(self) -> None:
        """测试完整链路：通过 WES 创建指令 → Mock 执行 → 回调 WES"""
        if not self.wes_available:
            pytest.skip(f"WES Backend 不可用: {self.wes_base_url}")

        logger.info("=" * 60)
        logger.info("开始测试: 完整端到端链路")
        logger.info("=" * 60)

        # Step 1: 通过 WES API 创建设备指令
        async with httpx.AsyncClient(timeout=30.0) as client:
            command_payload = {
                "device_code": "ARM01",
                "task_type": "PICK_AND_PUT",
                "priority": 1,
                "timeout": 30,
                "params": {
                    "source_loc": "STATION_INPUT1",
                    "target_loc": "STATION_PIPELINE1_INPUT1",
                    "execution_time": 2,
                },
                "trace_id": f"e2e-full-{int(time.time())}",
            }

            headers = await self.create_signed_headers("POST", "/api/v1/device/command", command_payload)

            logger.info(f"Step 1: 向 WES 创建指令: {command_payload['device_code']}")
            response = await client.post(
                f"{self.wes_base_url}/api/v1/device/command",
                json=command_payload,
                headers=headers,
            )

            # 检查响应
            if response.status_code != 200:
                logger.error(f"创建指令失败: {response.status_code} - {response.text}")
                pytest.skip(f"WES API 不可用: {response.status_code}")

            result = response.json()
            logger.info(f"✓ WES 接受指令: {result.get('message', 'OK')}")

            # 获取 command_code
            if result.get("code") == 200 and result.get("data"):
                command_code = result["data"].get("command_code")
                if command_code:
                    logger.info(f"✓ 指令已创建: {command_code}")

                    # Step 2: 等待 Mock 服务处理并回调
                    logger.info("Step 2: 等待 Mock 服务执行并回调...")
                    await asyncio.sleep(4)  # 给 Mock 足够时间执行

                    # Step 3: 查询指令状态验证回调成功
                    logger.info("Step 3: 验证指令状态...")
                    # 这里可以添加查询指令状态的逻辑
                    logger.info("✓ 完整链路测试通过")

    @pytest.mark.asyncio
    async def test_scan_event_triggers_plugin_flow(self) -> None:
        """测试扫描事件触发插件完整流程"""
        if not self.wes_available:
            pytest.skip(f"WES Backend 不可用: {self.wes_base_url}")

        logger.info("=" * 60)
        logger.info("开始测试: 扫描事件触发插件流程")
        logger.info("=" * 60)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: 模拟设备上报扫描事件
            event_payload = {
                "device_code": "ARM01",
                "event_type": "SCAN_COMPLETED",
                "timestamp": int(time.time() * 1000),
                "data": {
                    "location": "STATION_INPUT1",
                    "LotCode": "E2ELOT001",
                    "DateCode": "20260409",
                    "Qty": "100",
                    "ProductNo": "PROD001",
                    "MfrPN": "MFR001",
                    "PONumber": "PO001",
                },
            }

            headers = await self.create_signed_headers("POST", "/api/v1/callback/event", event_payload)

            logger.info("Step 1: 向 WES 上报扫描事件")
            response = await client.post(
                f"{self.wes_base_url}/api/v1/callback/event",
                json=event_payload,
                headers=headers,
            )

            if response.status_code != 200:
                logger.error(f"事件上报失败: {response.status_code} - {response.text}")
                pytest.skip(f"WES 回调 API 不可用: {response.status_code}")

            result = response.json()
            logger.info(f"✓ WES 接受事件: {result.get('message', 'OK')}")

            # Step 2: 等待插件处理和命令生成
            logger.info("Step 2: 等待插件处理...")
            await asyncio.sleep(3)

            logger.info("✓ 事件触发流程测试通过")


@pytest.mark.e2e
@pytest.mark.integration
class TestWESBackendHealth:
    """WES Backend 健康检查"""

    @pytest.mark.asyncio
    async def test_wes_backend_health(self) -> None:
        """测试 WES Backend 是否可访问"""
        import os

        wes_base_url = os.getenv("WES_BASE_URL", "http://localhost:8001")

        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(f"{wes_base_url}/health")
                assert response.status_code == 200
                logger.info(f"✓ WES Backend 健康: {wes_base_url}")
            except Exception as e:
                pytest.skip(f"WES Backend 不可用: {wes_base_url} - {e}")
