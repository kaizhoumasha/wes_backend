"""WorkLine 查询 API 回归测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_FRESH_PROCESS_QUERY = r"""
import importlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.rbac as rbac
from src.core.security import require_auth
from src.database.dependencies import _get_cache_service, get_db


async def query_empty_page(
    _self,
    _db,
    _cache,
    limit,
    offset,
    filters,
    sort,
    max_depth,
    include_deleted,
):
    assert (limit, offset, filters, sort, max_depth, include_deleted) == (10, 0, None, None, 1, False)
    return 0, []


importlib.import_module("src.app.workline.services.plane_service")
service_module = importlib.import_module("src.app.workline.services.workline_service")
service_module.WorkLineService.get_list = query_empty_page
workline_routes = importlib.import_module("src.app.workline.v1.workline")


async def get_permissions(*_args, **_kwargs):
    return {"biz:workline:list"}


rbac.get_user_permissions = get_permissions
app = FastAPI()
app.include_router(workline_routes.router, prefix="/api/v1/workline")
app.dependency_overrides[get_db] = lambda: object()
app.dependency_overrides[_get_cache_service] = lambda: None
app.dependency_overrides[require_auth] = lambda: 1

with TestClient(app, raise_server_exceptions=False) as client:
    response = client.post(
        "/api/v1/workline/work_lines/query",
        json={"offset": 0, "limit": 10, "max_depth": 1, "include_deleted": False},
    )

try:
    body = response.json()
except json.JSONDecodeError:
    body = response.text
print(json.dumps({"status_code": response.status_code, "body": body}))
"""


def test_workline_query_fresh_import_routes_to_service_singleton() -> None:
    """首次加载时即使 plane service 先导入，查询路由也必须调用 WorkLineService。"""

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_QUERY],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["status_code"] == 200
    body = payload["body"]
    assert set(body) == {"code", "message", "data", "timestamp"}
    assert body["code"] == "1000"
    assert body["message"] == "操作成功"
    assert isinstance(body["timestamp"], str)
    assert body["data"] == {
        "total": 0,
        "items": [],
        "limit": 10,
        "offset": 0,
    }
