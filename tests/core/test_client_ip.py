from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import Request

from src.core.client_ip import resolve_client_ip
from src.core.conf import Settings, settings


class TestTrustedProxySettings:
    def test_parses_json_array(self) -> None:
        parsed = Settings(TRUSTED_PROXY_IPS='["10.0.0.10", "192.168.0.0/16"]').TRUSTED_PROXY_IPS

        assert parsed == ["10.0.0.10", "192.168.0.0/16"]

    def test_parses_comma_separated_value(self) -> None:
        parsed = Settings(TRUSTED_PROXY_IPS="10.0.0.10, 192.168.0.0/16").TRUSTED_PROXY_IPS

        assert parsed == ["10.0.0.10", "192.168.0.0/16"]


def _request(headers: dict[str, str] | None = None, client_host: str | None = "127.0.0.1") -> Request:
    request = Mock(spec=Request)
    request.headers = headers or {}
    request.client = SimpleNamespace(host=client_host) if client_host is not None else None
    return request


class TestResolveClientIp:
    def test_returns_socket_peer_without_trusted_proxy(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
        request = _request(
            headers={
                "X-Real-IP": "203.0.113.10",
                "X-Forwarded-For": "203.0.113.11",
            },
            client_host="198.51.100.20",
        )

        assert resolve_client_ip(request) == "198.51.100.20"

    def test_uses_x_real_ip_from_trusted_proxy(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
        request = _request(headers={"X-Real-IP": "203.0.113.10"}, client_host="10.0.0.10")

        assert resolve_client_ip(request) == "203.0.113.10"

    def test_ignores_x_real_ip_from_untrusted_peer(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
        request = _request(headers={"X-Real-IP": "203.0.113.10"}, client_host="198.51.100.20")

        assert resolve_client_ip(request) == "198.51.100.20"

    def test_reads_x_forwarded_for_from_right_to_left(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.0/24", "198.51.100.2"])
        request = _request(
            headers={"X-Forwarded-For": "198.51.100.99, 203.0.113.10, 198.51.100.2, 10.0.0.10"},
            client_host="10.0.0.10",
        )

        assert resolve_client_ip(request) == "203.0.113.10"

    def test_falls_back_to_socket_peer_when_forwarded_header_is_invalid(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
        request = _request(headers={"X-Forwarded-For": "bad-ip"}, client_host="10.0.0.10")

        assert resolve_client_ip(request) == "10.0.0.10"

    def test_ignores_malformed_left_forwarded_item_when_proxy_appended_valid_client(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
        request = _request(headers={"X-Forwarded-For": "bad-ip, 203.0.113.10"}, client_host="10.0.0.10")

        assert resolve_client_ip(request) == "203.0.113.10"

    def test_supports_ipv6_proxy_and_client(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["2001:db8:ffff::/48"])
        request = _request(
            headers={"X-Forwarded-For": "2001:db8:1::10, 2001:db8:ffff::1"},
            client_host="2001:db8:ffff::1",
        )

        assert resolve_client_ip(request) == "2001:db8:1::10"

    def test_maps_testclient_to_loopback(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
        request = _request(client_host="testclient")

        assert resolve_client_ip(request) == "127.0.0.1"

    def test_returns_unknown_without_client(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
        request = _request(client_host=None)

        assert resolve_client_ip(request) == "unknown"
