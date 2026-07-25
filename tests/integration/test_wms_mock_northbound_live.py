"""显式连接 Docker Compose mock_wms 的北向 live 验收。

此目录不进入默认快速回归；运行者必须显式提供真实 base URL，测试不会用 skip 冒充 PASS。
"""

from __future__ import annotations

import os

import httpx
import pytest

from scripts.verify_wms_northbound_feasibility import run_probe


@pytest.mark.asyncio
async def test_compose_mock_wms_northbound_live_contract() -> None:
    base_url = os.getenv("WMS_NORTHBOUND_LIVE_BASE_URL", "").strip()
    if not base_url:
        pytest.fail("WMS_NORTHBOUND_LIVE_BASE_URL is required for explicit live acceptance")
    timeout_seconds = float(os.getenv("WMS_NORTHBOUND_LIVE_TIMEOUT_SECONDS", "0.25"))

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
        trust_env=False,
    ) as client:
        reset = await client.post("/debug/reset")
        assert reset.status_code == 200
        report = await run_probe(client, request_timeout_seconds=timeout_seconds)

    assert report.passed is True, report.cases
