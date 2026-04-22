#!/usr/bin/env python
"""
SMT 粗分机完整链路验证脚本

验证完整的请求链路，不依赖 WES Backend 运行状态。
直接测试每个环节，输出详细的日志。

运行方式:
    python tests/e2e/smt_classifier/verify_full_chain.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# 添加项目根目录到 sys.path（用于直接运行脚本）
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class ChainVerifier:
    """完整链路验证器"""

    def __init__(self):
        self.wes_base_url = "http://localhost:8001"
        self.mock_urls = {
            "pipeline": "http://127.0.0.1:8005",
            "arm01": "http://127.0.0.1:8006",
            "arm02": "http://127.0.0.1:8007",
            "allocation": "http://127.0.0.1:8008",
            "agv": "http://127.0.0.1:8009",
        }

    async def check_service(self, name: str, url: str) -> bool:
        """检查服务是否运行"""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                # WES Backend 使用无状态 /health，避免依赖认证与外部组件
                check_url = f"{url}/health" if name == "WES Backend" else f"{url}/"
                response = await client.get(check_url)
                if response.status_code == 200:
                    logger.info(f"✓ {name} 运行中")
                    return True
        except Exception:
            pass
        logger.error(f"✗ {name} 未运行")
        return False

    async def check_all_services(self) -> dict[str, bool]:
        """检查所有服务状态"""
        logger.info("=" * 60)
        logger.info("检查服务状态")
        logger.info("=" * 60)

        results = {}

        # 检查 WES Backend
        wes_ok = await self.check_service("WES Backend", self.wes_base_url)
        results["wes"] = wes_ok

        # 检查 Mock 服务
        for name, url in self.mock_urls.items():
            ok = await self.check_service(f"Mock {name.upper()}", url)
            results[name] = ok

        logger.info("-" * 60)
        return results

    async def test_mock_direct_command(self) -> None:
        """测试直接向 Mock 服务发送命令"""
        logger.info("=" * 60)
        logger.info("测试: 直接向 Mock 服务发送命令")
        logger.info("=" * 60)

        command_code = f"VERIFY-{int(time.time())}"
        command_payload = {
            "command_code": command_code,
            "task_type": "PICK_AND_PUT",
            "priority": 1,
            "timeout": 30,
            "params": {
                "source_loc": "STATION_INPUT1",
                "target_loc": "STATION_PIPELINE1_INPUT1",
                "execution_time": 2,
            },
            "timestamp": int(time.time()),
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info(f"发送命令到 ARM01: {command_code}")
            response = await client.post(
                f"{self.mock_urls['arm01']}/api/v1/device/command",
                json=command_payload,
            )

            if response.status_code != 200:
                raise AssertionError(f"Mock 拒绝命令: {response.status_code}")

            result = response.json()
            logger.info(f"✓ Mock 接受命令: {result.get('message')}")

        # 等待 Mock 执行（命令中设置的 execution_time=2秒）
        logger.info("等待 Mock 执行...")
        await asyncio.sleep(3)

        # 主动验证：查询 WES 数据库检查命令状态
        # 注意：这个测试是直接发给 Mock 的，不会在 WES 数据库中创建记录
        # 所以这里只是提示用户查看日志
        logger.info("✓ Mock 应该已完成执行，请检查 Mock 日志确认:")
        logger.info(f"  - 收到命令: {command_code}")
        logger.info("  - 执行搬运（2秒）")
        logger.info("  - 回调 WES /api/v1/callback/result")

    async def test_wes_callback_api(self) -> None:
        """测试 WES 回调 API（需要 WES 运行）"""
        logger.info("=" * 60)
        logger.info("测试: WES 回调 API")
        logger.info("=" * 60)

        # 检查 WES 是否运行
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                response = await client.get(f"{self.wes_base_url}/health")
                if response.status_code != 200:
                    logger.warning("WES Backend 未运行，跳过此测试")
                    return
            except Exception:
                logger.warning("WES Backend 未运行，跳过此测试")
                return

        # 发送扫描事件
        from tests.mock.smt_classifier.mock_support import build_api_auth_headers

        event_payload = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": "STATION_INPUT1",
                "LotCode": "VERIFY001",
                "DateCode": "20260409",
                "Qty": "100",
            },
        }

        headers = build_api_auth_headers("POST", "/api/v1/callback/event")

        async with httpx.AsyncClient(timeout=10.0) as client:
            logger.info("发送扫描事件到 WES")
            response = await client.post(
                f"{self.wes_base_url}/api/v1/callback/event",
                json=event_payload,
                headers=headers,
            )

            if response.status_code != 200:
                raise AssertionError(f"WES 拒绝事件: {response.status_code}")

            result = response.json()
            logger.info(f"✓ WES 接受事件: {result.get('message')}")

        # 等待插件处理和命令生成
        logger.info("等待插件处理（约 8 秒）...")
        await asyncio.sleep(8)

        # 提示用户验证结果
        logger.info("✓ 测试完成，请验证以下内容:")
        logger.info("")
        logger.info("方法 1: 查询数据库")
        logger.info("  docker exec wes_postgres_dev psql -U wes_user -d wes_db -c \\")
        logger.info(
            '    "SELECT id, command_code, task_type, status FROM wes_biz.device_commands ORDER BY id DESC LIMIT 3;"'
        )
        logger.info("")
        logger.info("方法 2: 检查 Mock 日志")
        logger.info("  应该看到 ARM01/PIPELINE01/ARM02 依次执行命令")
        logger.info("")
        logger.info("方法 3: 检查 Celery 日志")
        logger.info("  docker logs --tail 20 wes_backend-celery_worker-1 | grep 'Inbox 处理完成'")
        logger.info("  应该看到 processed > 0")


async def run_all_tests() -> None:
    """运行所有验证测试"""
    verifier = ChainVerifier()

    separator = "=" * 60
    logger.info(f"\n{separator}")
    logger.info("SMT 粗分机完整链路验证")
    logger.info(f"{separator}\n")

    # 检查服务状态
    results = await verifier.check_all_services()

    # 至少 Mock 服务应该运行
    if not results.get("arm01"):
        logger.error("\n✗ Mock 服务未运行，请先启动 Mock 服务:")
        logger.info("  python tests/mock/smt_classifier/run_all.py")
        return

    try:
        await verifier.test_mock_direct_command()
        await verifier.test_wes_callback_api()
    except AssertionError as e:
        logger.error(f"\n✗ 验证失败: {e}")
        logger.info("\n请确保 Mock 服务已启动:")
        logger.info("  python tests/mock/smt_classifier/run_all.py")
        return

    separator = "=" * 60
    logger.info(f"\n{separator}")
    logger.info("验证完成")
    logger.info(separator)
    logger.info("\n预期看到的日志：")
    logger.info("  Mock: 收到指令 → 执行 → 回调")
    logger.info("  WES: 收到事件 → Celery 处理 → 插件执行 → 生成命令")
    logger.info("  Celery: 处理 Inbox → 触发插件")


def main() -> None:
    """直接运行脚本的入口"""
    asyncio.run(run_all_tests())


if __name__ == "__main__":
    main()
