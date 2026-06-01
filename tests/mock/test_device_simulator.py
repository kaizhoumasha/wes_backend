import pytest

from tests.mock.device_simulator import create_simulator


@pytest.mark.asyncio
async def test_create_simulator_defaults_to_ecs_mock_port() -> None:
    simulator = await create_simulator()
    try:
        assert simulator.base_url == "http://localhost:8010"
    finally:
        await simulator.close()
