from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_nginx_proxy_uses_runtime_docker_dns_resolution() -> None:
    nginx_conf = (BACKEND_ROOT / "nginx/nginx.conf").read_text(encoding="utf-8")
    site_conf = (BACKEND_ROOT / "nginx/conf.d/default.conf").read_text(encoding="utf-8")

    assert "resolver 127.0.0.11" in nginx_conf
    assert "upstream fastapi_backend" not in nginx_conf
    assert "upstream frontend_dev" not in nginx_conf
    assert "set $fastapi_upstream api:8001;" in site_conf
    assert "set $frontend_upstream frontend:5173;" in site_conf
    assert "proxy_pass http://fastapi_backend" not in site_conf
    assert "proxy_pass http://frontend_dev" not in site_conf
    assert "proxy_pass http://$fastapi_upstream" in site_conf
    assert "proxy_pass http://$frontend_upstream" in site_conf
