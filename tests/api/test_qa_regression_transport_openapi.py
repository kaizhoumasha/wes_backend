"""WMS Transport 生产入口的 OpenAPI 回归合同。"""

from fastapi import FastAPI

from src.register import register_routers


def test_wms_transport_openapi_exposes_request_and_actual_response_contracts() -> None:
    # Regression: ISSUE-001 — Swagger 无请求体且只声明 200，无法用于 WMS 联调
    # Found by /qa on 2026-08-12
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-8012-2026-08-12.md
    app = FastAPI()
    register_routers(app)

    operation = app.openapi()["paths"]["/api/v1/wms/events"]["post"]
    request_body = operation["requestBody"]
    request_schema = request_body["content"]["application/json"]["schema"]

    assert request_body["required"] is True
    assert request_schema["type"] == "object"
    assert request_schema["additionalProperties"] is False
    assert request_schema["required"] == ["operation_id", "operation", "timestamp", "data"]
    assert request_schema["properties"]["operation"]["enum"] == [
        "transport.task.member_position_changed@v1",
        "transport.task.resulted@v1",
    ]
    assert set(operation["responses"]) == {"200", "202", "400", "401", "409", "413", "422", "503"}
    assert operation["responses"]["200"]["description"] == "重复 evidence 已确认"
    assert operation["responses"]["202"]["description"] == "evidence 已持久化"
    assert operation["responses"]["409"]["description"] == "operation_id 对应的 payload 冲突"
    assert operation["responses"]["422"]["description"] == "evidence data 不满足对应 operation 的封闭合同"
    ack_schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
    assert ack_schema["properties"]["code"]["enum"] == [
        "RECEIVED",
        "DUPLICATE",
        "CONFLICT",
        "REJECTED",
        "UNAVAILABLE",
    ]
    assert operation["responses"]["503"]["content"]["application/json"]["schema"] == ack_schema
    for status_code in ("400", "401", "413"):
        assert "content" not in operation["responses"][status_code]
