"""WMS Transport 生产入口的 OpenAPI 回归合同。"""

import re

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
    request_variants = request_schema["oneOf"]
    assert len(request_variants) == 2
    assert all(variant["type"] == "object" for variant in request_variants)
    assert all(variant["additionalProperties"] is False for variant in request_variants)
    assert all(
        variant["required"] == ["operation_id", "operation", "timestamp", "data"] for variant in request_variants
    )
    assert [variant["properties"]["operation"]["enum"] for variant in request_variants] == [
        ["transport.task.member_position_changed@v1"],
        ["transport.task.resulted@v1"],
    ]
    for variant in request_variants:
        timestamp = variant["properties"]["timestamp"]
        assert timestamp["type"] == "integer"
        assert timestamp["format"] == "int64"
        assert timestamp["minimum"] == 0
        assert timestamp["maximum"] >= 2**63 - 1
        assert timestamp["description"] == "Unix 毫秒时间戳"
    position_data = request_variants[0]["properties"]["data"]
    assert {variant["properties"]["milestone"]["enum"][0] for variant in position_data["oneOf"]} == {
        "SOURCE_PICKED",
        "TARGET_PLACED",
        "POSITION_UNKNOWN",
    }
    result_data = request_variants[1]["properties"]["data"]
    assert all("outcome_revision" in variant["required"] for variant in result_data["oneOf"])
    assert all(variant["properties"]["outcome_revision"]["minimum"] == 1 for variant in result_data["oneOf"])
    assert {kind for variant in result_data["oneOf"] for kind in variant["properties"]["kind"]["enum"]} == {
        "RACK_MOVE",
        "RACK_ROTATE",
        "BIN_MOVE",
        "BIN_EXCHANGE",
    }
    assert set(operation["responses"]) == {"200", "202", "400", "401", "409", "413", "422", "503"}
    assert operation["responses"]["200"]["description"] == "重复 evidence 已确认"
    assert operation["responses"]["202"]["description"] == "evidence 已持久化"
    assert operation["responses"]["409"]["description"] == "operation_id 或 outcome_revision 身份冲突"
    assert operation["responses"]["422"]["description"] == "evidence data 不满足对应 operation 的封闭合同"
    for status_code, expected_code in {
        "200": "DUPLICATE",
        "202": "RECEIVED",
        "409": "CONFLICT",
        "422": "REJECTED",
        "503": "UNAVAILABLE",
    }.items():
        ack_schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
        assert ack_schema.get("properties", {}).get("code", {}).get("enum") == [expected_code]
        assert ack_schema["additionalProperties"] is False
        data_schema = ack_schema["properties"]["data"]
        data_variants = data_schema.get("oneOf", [data_schema])
        assert all(variant["additionalProperties"] is False for variant in data_variants)
    for status_code in ("400", "401", "413"):
        assert "content" not in operation["responses"][status_code]

    constrained_strings = [
        schema
        for schema in _walk_schemas(request_schema)
        if schema.get("type") == "string" and schema.get("minLength") == 1
    ]
    assert constrained_strings
    for schema in constrained_strings:
        assert "pattern" in schema
        assert re.search(schema["pattern"], "   ") is None
        assert re.search(schema["pattern"], "value") is not None

    rack_slots = [
        schema
        for schema in _walk_schemas(request_schema)
        if schema.get("properties", {}).get("kind", {}).get("enum") == ["RACK_BIN_SLOT"]
    ]
    assert rack_slots
    assert all("rack_face" in schema["required"] for schema in rack_slots)
    assert all(schema["properties"]["rack_face"]["enum"] == ["A", "B"] for schema in rack_slots)


def _walk_schemas(schema: object):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _walk_schemas(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_schemas(value)
