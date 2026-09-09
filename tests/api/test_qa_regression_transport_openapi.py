"""唯一 WMS event 生产入口的 OpenAPI 回归合同。"""

import re

from fastapi import FastAPI

from src.register import register_routers


def test_swagger_event_examples_are_executable_wire_requests() -> None:
    from src.app.wms_adapter.inbound_material.wire import parse_recovery_event
    from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import parse_manual_bin_completed_event
    from src.app.wms_adapter.outbound_picking.plan_delta_wire import parse_picking_task_plan_delta_event
    from src.app.wms_adapter.outbound_picking.queue_changed_wire import parse_picking_task_queue_changed_event
    from src.app.wms_adapter.outbound_picking.wire import parse_picking_task_issued_event
    from src.app.wms_adapter.transport_wire import validate_callback_envelope

    app = FastAPI()
    register_routers(app)
    media = app.openapi()["paths"]["/api/v1/wms/events"]["post"]["requestBody"]["content"]["application/json"]
    parsers = {
        "outbound.picking_task.issued@v1": parse_picking_task_issued_event,
        "outbound.picking_task.queue_changed@v1": parse_picking_task_queue_changed_event,
        "outbound.picking_task.plan_delta@v1": parse_picking_task_plan_delta_event,
        "outbound.manual_bin.work_completed@v1": parse_manual_bin_completed_event,
    }
    parsers.update(
        {
            "inbound.execution.recovery_decided@v1": parse_recovery_event,
            "transport.task.member_position_changed@v1": validate_callback_envelope,
            "transport.task.resulted@v1": validate_callback_envelope,
        }
    )
    examples = [example["value"] for example in media["examples"].values()]
    assert {example["operation"] for example in examples} == set(parsers)
    assert len({example["operation_id"] for example in examples}) == len(examples)
    assert len({example["data"]["task_id"] for example in examples if "task_id" in example["data"]}) == 1
    assert {v["properties"]["operation"]["enum"][0] for v in media["schema"]["oneOf"]} == set(parsers)
    for example in examples:
        parsed = parsers[example["operation"]](example)
        assert (parsed if isinstance(parsed, dict) else parsed.model_dump(mode="json", exclude_unset=True)) == example


def test_swagger_normal_responses_match_each_request_and_replay_identity() -> None:
    app = FastAPI()
    register_routers(app)
    operation = app.openapi()["paths"]["/api/v1/wms/events"]["post"]
    requests = operation["requestBody"]["content"]["application/json"]["examples"]
    accepted = operation["responses"]["202"]["content"]["application/json"]["examples"]
    replayed = operation["responses"]["200"]["content"]["application/json"]["examples"]
    assert accepted.keys() == replayed.keys() == requests.keys()
    for key, example in requests.items():
        first = accepted[key]["value"]
        assert first["operation_id"] == example["value"]["operation_id"]
        assert first["code"] == "RECEIVED"
        data = example["value"]["data"]
        assert first["data"] == (
            {"transport_task_id": data["transport_task_id"]} if "transport_task_id" in data else {}
        )
        assert isinstance(first["timestamp"], int) and first["timestamp"] > 0
        assert replayed[key]["value"] == first | {"code": "DUPLICATE"}


def test_wms_event_openapi_exposes_transport_recovery_and_picking_task_contracts() -> None:
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
    assert len(request_variants) == 7
    assert all(variant["type"] == "object" for variant in request_variants)
    assert all(variant.get("additionalProperties", True) is True for variant in request_variants)
    assert all(
        variant["required"] == ["operation_id", "operation", "timestamp", "data"] for variant in request_variants
    )
    assert [variant["properties"]["operation"]["enum"] for variant in request_variants] == [
        ["transport.task.member_position_changed@v1"],
        ["transport.task.resulted@v1"],
        ["inbound.execution.recovery_decided@v1"],
        ["outbound.picking_task.issued@v1"],
        ["outbound.picking_task.plan_delta@v1"],
        ["outbound.picking_task.queue_changed@v1"],
        ["outbound.manual_bin.work_completed@v1"],
    ]
    for variant in request_variants:
        timestamp = variant["properties"]["timestamp"]
        assert timestamp["type"] == "integer"
        assert timestamp["format"] == "int64"
        operation_name = variant["properties"]["operation"]["enum"][0]
        expected_minimum = (
            1
            if operation_name
            in {
                "inbound.execution.recovery_decided@v1",
                "outbound.picking_task.issued@v1",
                "outbound.manual_bin.work_completed@v1",
            }
            else 0
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
    picking_task_data = request_variants[3]["properties"]["data"]
    assert picking_task_data["required"] == ["task_id", "task_type", "queue_revision", "dispatch_sequence"]
    assert picking_task_data["properties"]["task_type"]["enum"] == ["MANUAL", "AUTO"]
    assert picking_task_data["properties"]["queue_revision"]["minimum"] == 1
    assert picking_task_data["properties"]["queue_revision"]["maximum"] == 1
    queue_data = request_variants[5]["properties"]["data"]
    assert queue_data["required"] == ["task_id", "queue_revision"]
    assert queue_data.get("additionalProperties", True) is True
    assert set(queue_data["properties"]) == {"task_id", "queue_revision", "dispatch_sequence", "not_before"}
    assert queue_data["properties"]["queue_revision"]["type"] == "integer"
    assert queue_data["properties"]["queue_revision"]["minimum"] == 2
    # FastAPI 的 OpenAPI 模型将 maximum 序列化为 float。
    assert queue_data["properties"]["queue_revision"]["maximum"] == float(2**63 - 1)
    assert queue_data["properties"]["dispatch_sequence"]["minimum"] == 1
    assert queue_data["properties"]["not_before"]["minimum"] == 0
    assert queue_data["anyOf"] == [{"required": ["dispatch_sequence"]}, {"required": ["not_before"]}]
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
        assert ack_schema.get("additionalProperties", True) is True
        data_schema = ack_schema["properties"]["data"]
        data_variants = data_schema.get("anyOf", [data_schema])
        assert all(variant.get("additionalProperties", True) is True for variant in data_variants)
    for status_code in ("400", "401", "413"):
        assert "content" not in operation["responses"][status_code]

    face_schemas = [
        schema["properties"]["rack_face"]
        for schema in _walk_schemas(request_schema)
        if "rack_face" in schema.get("properties", {})
    ]
    constrained_strings = [
        schema
        for schema in _walk_schemas(request_schema)
        if schema.get("type") == "string" and schema.get("minLength") == 1
    ]
    assert constrained_strings
    for schema in constrained_strings:
        if schema in face_schemas:
            assert schema["maxLength"] == 10
            assert not {"enum", "allOf"} & set(schema)
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
            "maxLength": 10,
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


def test_wms_business_response_examples_match_strict_operation_parsers() -> None:
    from src.app.wms_adapter.outbound_picking import (
        arrival_report_wire,
        completion_confirm_wire,
        departure_wire,
        inbound_batch_wire,
        material_decide_wire,
        movement_report_wire,
        return_batch_wire,
        source_empty_wire,
        wire,
        work_plan_wire,
    )
    from src.app.wms_adapter.outbound_picking.openapi import (
        PICKING_TASK_WMS_RESPONSE_DATA,
        PICKING_TASK_WMS_RESPONSE_EXAMPLES,
    )

    parsers = {
        "outbound.picking_task.prepare@v1": wire.parse_picking_task_prepare_response,
        "outbound.return_rack.arrival_report@v1": arrival_report_wire.parse_return_rack_arrival_report_response,
        "outbound.bin.inbound_batch@v1": inbound_batch_wire.parse_bin_inbound_batch_response,
        "outbound.bin.work_plan@v1": work_plan_wire.parse_bin_work_plan_response,
        "outbound.material.decide@v1": material_decide_wire.parse_material_decide_response,
        "outbound.source.empty_decide@v1": source_empty_wire.parse_source_empty_response,
        "outbound.picking_task.completion_confirm@v1": completion_confirm_wire.parse_completion_confirm_response,
        "outbound.material.movement_report@v1": movement_report_wire.parse_material_movement_report_response,
        "outbound.bin.return_batch@v1": return_batch_wire.parse_bin_return_batch_response,
        "outbound.rack.departure_decide@v1": departure_wire.parse_rack_departure_response,
    }
    assert parsers.keys() == PICKING_TASK_WMS_RESPONSE_DATA.keys()
    errors = PICKING_TASK_WMS_RESPONSE_EXAMPLES["通用错误响应（适用于上述 Operation）"]
    for operation, parser in parsers.items():
        for status, response in PICKING_TASK_WMS_RESPONSE_EXAMPLES[operation] + errors:
            parsed = parser(status, response)
            assert parsed.model_dump(mode="json", exclude_unset=True) == response
