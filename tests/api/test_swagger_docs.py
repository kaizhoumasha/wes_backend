"""Swagger 页面引用的资源必须由同一应用提供。"""

import re

from fastapi.testclient import TestClient

from src.core.conf import settings
from src.register import create_app


def test_swagger_references_reachable_local_assets_and_oauth_redirect() -> None:
    # 不进入 lifespan；页面与静态资源验证不依赖数据库或 Redis。
    client = TestClient(create_app())
    response = client.get(settings.DOCS_URL)
    assert response.status_code == 200
    redirect = re.search(r"oauth2RedirectUrl: window.location.origin \+ '([^']+)'", response.text)
    assert redirect is not None
    assert client.get(redirect[1]).status_code == 200
    for asset in re.findall(r'(?:src|href)="([^"#]+)"', response.text):
        assert asset.startswith("/"), asset
        assert client.get(asset).status_code == 200
    assert '"validatorUrl": null' in response.text
