"""
SMT 粗分机完整业务流程 E2E 测试

测试完整的端到端业务场景，包括：
1. 进料 OK 流程（尺寸检测 OK）：扫码 → 进料 → 移料 → 出料
2. 进料 NG 流程（扫码 NG）：扫码 → 直接到 NG 缓存位
3. 进料 NG 流程（尺寸检测/测厚 NG）：扫码 OK → 进料 → 检测失败 → 移到 NG 位

硬件商约定来源：SMT粗分机接口调用说明书 v2.0 (2026-03-21) 第 9.11 节
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import asyncpg
    import httpx

from tests.e2e.smt_classifier.conftest import (
    get_session_commands,
    wait_for_session_completed,
)

logger = logging.getLogger(__name__)


# ==================== 进料 OK 流程（尺寸检测 OK）============================


@pytest.mark.e2e
@pytest.mark.integration
class TestInputOkFlow:
    """进料 OK 流程测试 - 完整业务场景

    流程（硬件说明书 9.11.1）：
    1. 设备上报扫码事件
    2. WES 判断条码 OK
    3. WES 下发进料 OK 命令：串杆位置 -> 流水线进料位置
    4. 设备回传任务结果，携带条码、尺寸、厚度信息
    5. WES 下发移料命令：流水线进料位置 -> 流水线出料位置
    6. 设备回传移料结果
    7. WES 下发出料命令：流水线出料位置 -> 料箱
    8. 设备回传出料结果
    """

    @pytest.mark.asyncio
    async def test_full_ok_flow(
        self,
        wes_client: httpx.AsyncClient,
        arm01_client: httpx.AsyncClient,
        pipeline_client: httpx.AsyncClient,
        arm02_client: httpx.AsyncClient,
        db_conn: asyncpg.Connection,
        clean_mock_state: None,
    ) -> None:
        """测试完整的进料 OK 流程（尺寸检测 OK）"""
        logger.info("=" * 60)
        logger.info("开始测试: 完整进料 OK 流程（尺寸检测 OK）")
        logger.info("=" * 60)

        # 步骤 1: 设备上报扫码事件
        logger.info("步骤 1: ARM01 上报扫码事件")

        scan_event: dict[str, Any] = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": "STATION_INPUT1",
                "LotCode": "LOTOK001",
                "DateCode": "20260409",
                "Qty": "100",
                "ProductNo": "PN001",
            },
        }

        response = await wes_client.post("/api/v1/callback/event", json=scan_event)
        assert response.status_code == 200
        logger.info(f"✓ 扫码事件已接收: {response.json()}")

        # 等待会话完成（自动轮询）
        session = await wait_for_session_completed(db_conn, timeout_seconds=30)
        logger.info(f"会话已完成: id={session['id']}, status={session['status']}")

        # 验证命令执行记录
        session_id = str(session["id"])
        commands = await get_session_commands(db_conn, session_id)

        logger.info(f"命令执行记录 ({len(commands)} 条):")
        for cmd in commands:
            logger.info(f"  - {cmd['device_code']}: {cmd['task_type']} -> {cmd['status']}/{cmd['result']}")

        # 验证结果
        assert session["status"] == "COMPLETED"
        assert len(commands) == 3

        # 验证命令顺序
        assert commands[0]["device_code"] == "ARM01"
        assert commands[0]["task_type"] in ("PICK_AND_PLACE", "PICK_AND_PUT")
        assert commands[0]["status"] == "COMPLETED"
        assert commands[0]["result"] == "SUCCESS"

        assert commands[1]["device_code"] == "PIPELINE01"
        assert commands[1]["task_type"] in ("PROCESS", "MOVE_FORWARD")
        assert commands[1]["status"] == "COMPLETED"
        assert commands[1]["result"] == "SUCCESS"

        assert commands[2]["device_code"] == "ARM02"
        assert commands[2]["task_type"] in ("PICK_AND_PLACE", "PICK_AND_PUT")
        assert commands[2]["status"] == "COMPLETED"
        assert commands[2]["result"] == "SUCCESS"

        logger.info("✓ 完整进料 OK 流程验证通过")


# ==================== 进料 NG 流程（扫码 NG）============================


@pytest.mark.e2e
@pytest.mark.integration
class TestInputNgFlowScanNg:
    """进料 NG 流程测试 - 扫码 NG 场景

    流程（硬件说明书 9.11.2）：
    1. 设备上报扫码事件
    2. WES 判断条码 NG
    3. WES 下发进料 NG 命令：串杆位置 -> NG 缓存位
    4. 设备回传进料 NG 结果
    """

    @pytest.mark.asyncio
    async def test_scan_ng_flow(
        self,
        wes_client: httpx.AsyncClient,
        arm01_client: httpx.AsyncClient,
        db_conn: asyncpg.Connection,
        clean_mock_state: None,
    ) -> None:
        """测试扫码 NG 流程"""
        logger.info("=" * 60)
        logger.info("开始测试: 进料 NG 流程（扫码 NG）")
        logger.info("=" * 60)

        # 步骤 1: 设备上报扫码事件（无效条码）
        logger.info("步骤 1: ARM01 上报扫码事件（无效条码）")

        scan_event: dict[str, Any] = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": "STATION_INPUT1",
                "LotCode": "X",  # 无效条码（长度不足）
            },
        }

        response = await wes_client.post("/api/v1/callback/event", json=scan_event)
        assert response.status_code == 200
        logger.info(f"✓ 扫码事件已接收: {response.json()}")

        # 等待扫码 NG 链路完成（自动轮询）
        deadline = time.time() + 10
        while time.time() < deadline:
            session = await db_conn.fetchrow(
                """
                SELECT id, status, failure_domain, failure_code, failure_message
                FROM wes_biz.workline_sessions
                ORDER BY id DESC
                LIMIT 1
                """
            )
            if session and session["status"] in ("FAILED", "COMPLETED"):
                break
            await asyncio.sleep(0.5)

        assert session is not None
        logger.info(
            f"会话状态: id={session['id']}, status={session['status']}, "
            f"failure={session['failure_domain']}/{session['failure_code']}"
        )

        # 验证结果：按当前运行时文档，扫码 NG 会进入 NG 分流并在设备回调成功后完成
        assert session["status"] == "COMPLETED"
        assert session["failure_domain"] is None
        assert session["failure_code"] is None

        # 验证已生成进料 NG 命令
        session_id = str(session["id"])
        command_count = await db_conn.fetchval(
            """
            SELECT COUNT(*)
            FROM wes_biz.device_commands
            WHERE session_id = $1
            """,
            session_id,
        )

        assert command_count == 1
        logger.info("✓ 扫码 NG 流程验证通过")


# ==================== 进料 NG 流程（尺寸检测/测厚 NG）============================


@pytest.mark.e2e
@pytest.mark.integration
class TestInputNgFlowSizeNg:
    """进料 NG 流程测试 - 尺寸检测/测厚 NG 场景

    流程（硬件说明书 9.11.3）：
    1. 设备上报扫码事件
    2. WES 判断条码 OK
    3. WES 下发进料 OK 命令：串杆位置 -> 流水线进料位置
    4. 设备回传结果，携带检测信息
    5. 若尺寸或厚度 NG，错误码为 1001 或 1002
    6. WES 下发进料 NG 命令：流水线进料位置 -> NG 缓存位
    7. 设备回传进料 NG 结果

    注意：通过 Mock 服务的 error_code 参数模拟硬件错误。
    """

    @pytest.mark.asyncio
    async def test_size_detection_ng_flow(
        self,
        wes_client: httpx.AsyncClient,
        arm01_client: httpx.AsyncClient,
        db_conn: asyncpg.Connection,
        clean_mock_state: None,
    ) -> None:
        """测试尺寸检测 NG 流程（错误码 1001）"""
        logger.info("=" * 60)
        logger.info("开始测试: 进料 NG 流程（尺寸检测 NG）")
        logger.info("=" * 60)

        # 步骤 1: 设备上报扫码事件
        logger.info("步骤 1: ARM01 上报扫码事件")

        scan_event: dict[str, Any] = {
            "device_code": "ARM01",
            "event_type": "SCAN_COMPLETED",
            "timestamp": int(time.time() * 1000),
            "data": {
                "location": "STATION_INPUT1",
                "LotCode": "LOTSIZENG",
                "DateCode": "20260409",
            },
        }

        response = await wes_client.post("/api/v1/callback/event", json=scan_event)
        assert response.status_code == 200
        logger.info("✓ 扫码事件已接收")

        # 等待进料命令执行完成（轮询）
        deadline = time.time() + 30
        while time.time() < deadline:
            session = await db_conn.fetchrow(
                """
                SELECT id, status
                FROM wes_biz.workline_sessions
                ORDER BY id DESC
                LIMIT 1
                """
            )
            if session and session["status"] in ("COMPLETED", "FAILED"):
                break
            await asyncio.sleep(0.5)

        assert session is not None

        # 查询命令执行记录
        session_id = str(session["id"])
        commands = await get_session_commands(db_conn, session_id)

        logger.info(f"命令执行记录 ({len(commands)} 条):")
        for cmd in commands:
            logger.info(f"  - {cmd['device_code']}: {cmd['task_type']} -> {cmd['status']}/{cmd['result']}")

        # 验证1: 进料命令失败（尺寸检测 NG）
        assert len(commands) >= 1
        assert commands[0]["device_code"] == "ARM01"
        assert commands[0]["task_type"] in ("PICK_AND_PLACE", "PICK_AND_PUT")
        assert commands[0]["result"] == "FAILED"

        # 验证2: NG 移料命令生成
        assert len(commands) == 2
        assert commands[1]["device_code"] == "ARM01"
        assert commands[1]["task_type"] in ("PICK_AND_PLACE", "PICK_AND_PUT")

        logger.info(f"NG 移料命令状态: {commands[1]['status']}/{commands[1]['result']}")
        logger.info(f"会话最终状态: {session['status']}")

        logger.info("✓ 尺寸检测 NG 流程验证通过")
