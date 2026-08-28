from pathlib import Path

NGINX_DEFAULT_CONF = Path("nginx/conf.d/default.conf")
BACKEND_LOCATIONS = (
    "location /health",
    "location = /api",
    "location = /api/v1/sys/events/stream",
    "location = /api/v1/device/evidences/stream",
    "location = /api/v1/transport/evidences/stream",
    "location ^~ /api/",
    "location /docs",
    "location /redoc",
    "location = /openapi.json",
)


def _location_block(config: str, marker: str) -> str:
    start = config.index(marker)
    end = config.index("\n    }\n", start)
    return config[start:end]


def test_backend_proxy_preserves_x_forwarded_for_chain() -> None:
    config = NGINX_DEFAULT_CONF.read_text()

    for marker in BACKEND_LOCATIONS:
        block = _location_block(config, marker)
        assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block
        assert "proxy_set_header X-Forwarded-For $remote_addr;" not in block


def test_device_evidence_stream_disables_buffering_and_keeps_connection_alive() -> None:
    config = NGINX_DEFAULT_CONF.read_text()
    block = _location_block(config, "location = /api/v1/device/evidences/stream")

    for directive in (
        "proxy_buffering off;",
        "proxy_cache off;",
        "gzip off;",
        "proxy_read_timeout 1h;",
        "proxy_send_timeout 1h;",
        "add_header X-Accel-Buffering no;",
    ):
        assert directive in block


def test_transport_evidence_stream_disables_buffering_and_keeps_connection_alive() -> None:
    config = NGINX_DEFAULT_CONF.read_text()
    block = _location_block(config, "location = /api/v1/transport/evidences/stream")

    for directive in (
        "proxy_buffering off;",
        "proxy_cache off;",
        "gzip off;",
        "proxy_read_timeout 1h;",
        "proxy_send_timeout 1h;",
        "add_header X-Accel-Buffering no;",
    ):
        assert directive in block
