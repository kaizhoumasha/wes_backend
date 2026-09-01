"""唯一 WMS event 生产入口的 OpenAPI 回归合同。"""

import re

from fastapi import FastAPI

from src.register import register_routers


def test_wms_event_openapi_exposes_transport_and_recovery_contracts() -> None:
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
    assert len(request_variants) == 3
    assert all(variant["type"] == "object" for variant in request_variants)
    assert all(variant["additionalProperties"] is False for variant in request_variants)
    assert all(
        variant["required"] == ["operation_id", "operation", "timestamp", "data"] for variant in request_variants
    )
    assert [variant["properties"]["operation"]["enum"] for variant in request_variants] == [
        ["transport.task.member_position_changed@v1"],
        ["transport.task.resulted@v1"],
        ["inbound.execution.recovery_decided@v1"],
    ]
    for variant in request_variants:
        timestamp = variant["properties"]["timestamp"]
        assert timestamp["type"] == "integer"
        assert timestamp["format"] == "int64"
        expected_minimum = (
            1 if variant["properties"]["operation"]["enum"] == ["inbound.execution.recovery_decided@v1"] else 0
        )
        assert timestamp["minimum"] == expected_minimum
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
    assert operation["responses"]["200"]["description"] == "相同 WMS event 已可靠持久化"
    assert operation["responses"]["202"]["description"] == "WMS event 已可靠持久化"
    assert operation["responses"]["409"]["description"] == "WMS event 身份、内容或不可变事实冲突"
    assert operation["responses"]["422"]["description"] == "WMS event 信封或 operation 专属 data 不合法"
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
        if schema.get("description") == "Opaque non-empty face value without NUL; preserve exactly":
            assert not {"enum", "maxLength", "allOf"} & set(schema)
            assert re.search(schema["pattern"], "\x00") is None
            assert re.search(schema["pattern"], " ") is not None
            continue
        if "pattern" in schema:
            assert re.search(schema["pattern"], "   ") is None
            assert re.search(schema["pattern"], "value") is not None
            continue
        forbidden_patterns = [constraint["not"]["pattern"] for constraint in schema["allOf"]]
        assert any(re.search(pattern, "   ") is not None for pattern in forbidden_patterns)
        assert all(re.search(pattern, "value") is None for pattern in forbidden_patterns)

    rack_slots = [
        schema
        for schema in _walk_schemas(request_schema)
        if schema.get("properties", {}).get("kind", {}).get("enum") == ["RACK_BIN_SLOT"]
    ]
    assert rack_slots
    assert all("rack_face" in schema["required"] for schema in rack_slots)
    assert all(
        schema["properties"]["rack_face"]
        == {
            "type": "string",
            "minLength": 1,
            "pattern": "^[^\\u0000]+$",
            "description": "Opaque non-empty face value without NUL; preserve exactly",
        }
        for schema in rack_slots
    )


def _walk_schemas(schema: object):
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _walk_schemas(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_schemas(value)
