"""Device 静态 Endpoint 合同。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.device.endpoint import validate_device_endpoint_base_url
from src.app.device.models.device import DeviceCreate, DeviceResponse, DeviceUpdate


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://192.168.1.20:8080/", "http://192.168.1.20:8080"),
        ("HTTP://MOCK_ECS:80/", "http://mock_ecs"),
        ("HTTP://ECS.LOCAL:8080/", "http://ecs.local:8080"),
        ("http://device.factory.lan", "http://device.factory.lan"),
        ("http://[FD00:0:0::20]:8080", "http://[fd00::20]:8080"),
        ("http://127.0.0.1", "http://127.0.0.1"),
    ],
)
def test_device_endpoint_returns_canonical_lan_http_origin(value: str, expected: str) -> None:
    assert validate_device_endpoint_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        " http://192.168.1.20",
        "https://192.168.1.20",
        "http://user:pass@192.168.1.20",
        "http://192.168.1.20/path",
        "http://192.168.1.20?query=1",
        "http://192.168.1.20#fragment",
        "http://192.168.1.20:not-a-port",
        "http://192.168.1.20:",
        "http://192.168.1.20:0",
        "http://192.168.1.20:65536",
        "http://bad host",
        "http://bad!host",
        "http://bad..host",
        "http://-bad.host",
        "http://bad-.host",
        f"http://{'a' * 64}.lan",
        f"http://{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 63}",
        "http://999.999.999.999",
        "http://-bad",
        "http://bad-",
        "http://服务",
        f"http://{'a' * 64}",
        "http://134744072",
        "http://0x08080808",
        "http://0x8.0x8.0x8",
        "http://0xA9.0xFE.0xA9.0xFE",
        "http://mock_\tecs",
        "http://mock_\necs",
        "\x00http://mock_ecs",
        "http://0.0.0.0",
        "http://[::]",
        "http://224.0.0.1",
        "http://[ff02::1]",
        "http://240.0.0.1",
        "http://[100::1]",
        "http://255.255.255.255",
        "http://169.254.169.254",
        "http://[fe80::1]",
    ],
)
def test_device_endpoint_rejects_non_origin_or_non_lan_values(value: str) -> None:
    with pytest.raises(ValueError):
        validate_device_endpoint_base_url(value)


def _device_payload(endpoint_base_url: str | None) -> dict[str, object]:
    return {
        "device_code": "D-001",
        "device_name": "Device 1",
        "device_role": "TRANSFER_DEVICE",
        "endpoint_base_url": endpoint_base_url,
    }


@pytest.mark.parametrize(("schema", "extra"), [(DeviceCreate, {}), (DeviceUpdate, {"version": 1})])
def test_device_write_schemas_share_optional_endpoint_canonicalization(schema: type, extra: dict[str, int]) -> None:
    assert schema.model_validate({**_device_payload(None), **extra}).endpoint_base_url is None
    assert (
        schema.model_validate({**_device_payload("HTTP://MOCK_ECS:80/"), **extra}).endpoint_base_url
        == "http://mock_ecs"
    )
    with pytest.raises(ValidationError):
        schema.model_validate({**_device_payload("http://bad..host"), **extra})


def test_device_response_uses_the_same_endpoint_contract() -> None:
    response = DeviceResponse.model_validate(
        {
            **_device_payload("http://[FD00::20]:8080/"),
            "id": 1,
            "version": 1,
        }
    )

    assert response.endpoint_base_url == "http://[fd00::20]:8080"
