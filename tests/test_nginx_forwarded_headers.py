from pathlib import Path

NGINX_DEFAULT_CONF = Path("nginx/conf.d/default.conf")
BACKEND_LOCATIONS = (
    "location /health",
    "location = /api",
    "location = /api/v1/sys/events/stream",
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
