from __future__ import annotations

import asyncio
import logging
import time

import pytest

logger = logging.getLogger(__name__)


@pytest.mark.e2e
@pytest.mark.integration
class TestPipelineMaterialArrivedE2E:
    """SMT 粗分机真实多组件链路测试。"""

    @pytest.mark.asyncio
    async def test_pipeline_material_arrived_event(
        self,
        wes_client,
        db_conn,
        clean_mock_state: None,
    ) -> None:
        """测试 Pipeline 上报物料到达事件后，WES 会异步消费并完成 Inbox。"""

        logger.info("=" * 60)
        logger.info("开始测试: Pipeline 上报物料到达事件")
        logger.info("=" * 60)

        event_timestamp = int(time.time() * 1000)
        event_payload = {
            "device_code": "PIPELINE01",
            "event_type": "MATERIAL_ARRIVED",
            "timestamp": event_timestamp,
            "data": {
                "event_id": f"PIPELINE-EVT-{event_timestamp}",
                "location": "STATION_INPUT1",
            },
        }

        response = await wes_client.post(
            "/api/v1/callback/event",
            json=event_payload,
        )

        assert response.status_code == 200
        logger.info("✓ Pipeline 上报物料到达事件成功")

        result = response.json()
        assert result.get("code") == 200 or result.get("message") == "Event received"
        logger.info(f"  WES 响应: {result}")

        logger.info("✓ 测试通过: 符合白皮书 3.3.1 传感器触发模式规范")

        deadline = asyncio.get_running_loop().time() + 10
        inbox_row = None
        while asyncio.get_running_loop().time() < deadline:
            inbox_row = await db_conn.fetchrow(
                """
                SELECT id, status, error_message, session_id
                FROM wes_biz.workline_inbox
                WHERE payload_json->>'event_type' = $1
                  AND payload_json->>'device_code' = $2
                  AND payload_json->>'timestamp' = $3
                ORDER BY id DESC
                LIMIT 1
                """,
                "MATERIAL_ARRIVED",
                "PIPELINE01",
                str(event_timestamp),
            )
            if inbox_row and inbox_row["status"] in {"PROCESSED", "FAILED"}:
                break
            await asyncio.sleep(0.5)

        assert inbox_row is not None
        assert inbox_row["status"] == "PROCESSED"
        assert inbox_row["error_message"] in (None, "")
        assert inbox_row["session_id"] is not None
