"""共享 WMS Event OpenAPI 分组的 QA 回归。"""

from fastapi import FastAPI

from src.register import register_routers


def test_shared_wms_event_openapi_uses_business_neutral_tag() -> None:
    # Regression: ISSUE-001 — 共享 WMS Event 被错误归类为 WMS Transport
    # Found by /qa on 2026-09-01
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-2026-09-01.md
    app = FastAPI()
    register_routers(app)

    operation = app.openapi()["paths"]["/api/v1/wms/events"]["post"]

    assert operation["tags"] == ["WMS Events"]
