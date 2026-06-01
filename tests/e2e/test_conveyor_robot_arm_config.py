from tests.e2e import test_conveyor_robot_arm as conveyor_e2e


def test_env_test_declares_compose_mock_ecs_url() -> None:
    env_test = (conveyor_e2e.project_root / ".env.test").read_text()

    assert "MOCK_ECS_URL=http://mock_ecs:8010" in env_test


def test_mock_ecs_device_connection_uses_configured_local_url(monkeypatch) -> None:
    monkeypatch.setattr(conveyor_e2e, "MOCK_ECS_URL", "http://localhost:8010")

    assert conveyor_e2e._mock_ecs_device_connection() == {"host": "localhost", "port": 8010}


def test_mock_ecs_device_connection_supports_compose_service_url(monkeypatch) -> None:
    monkeypatch.setattr(conveyor_e2e, "MOCK_ECS_URL", "http://mock_ecs:8010")

    assert conveyor_e2e._mock_ecs_device_connection() == {"host": "mock_ecs", "port": 8010}
