"""最小 WMS typed-operation conformance simulator。"""

from __future__ import annotations

import httpx
import pytest

from tests.support.wms_integration.scripted_provider import ScriptedWmsQueryInventoryProvider
from tests.support.wms_provider_conformance import QUERY_INVENTORY_SCRIPT_FIXTURE


@pytest.mark.asyncio
async def test_simulator_is_deterministic_without_starting_a_service() -> None:
    case = next(item for item in QUERY_INVENTORY_SCRIPT_FIXTURE.cases if item.case_id == "success")
    provider = ScriptedWmsQueryInventoryProvider(case)
    request = httpx.Request("GET", "https://in-process.invalid/inventory/query?material_id=MAT-001")

    first = await provider.handle(request)
    second = await provider.handle(request)

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert not hasattr(provider, "base_url")
    assert not hasattr(provider, "credential_provider")
    assert not hasattr(provider, "start")
    assert not hasattr(provider, "stop")


@pytest.mark.asyncio
async def test_simulator_faults_are_named_and_do_not_sleep_or_poll() -> None:
    cases = {case.case_id: case for case in QUERY_INVENTORY_SCRIPT_FIXTURE.cases}
    request = httpx.Request("GET", "https://in-process.invalid/inventory/query?material_id=MAT-001")

    with pytest.raises(httpx.ReadTimeout):
        await ScriptedWmsQueryInventoryProvider(cases["timeout"]).handle(request)
    rate_limited = await ScriptedWmsQueryInventoryProvider(cases["rate_limit"]).handle(request)
    unavailable = await ScriptedWmsQueryInventoryProvider(cases["unavailable"]).handle(request)

    assert rate_limited.status_code == 429
    assert unavailable.status_code == 503
