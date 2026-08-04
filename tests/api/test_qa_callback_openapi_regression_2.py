"""QA callback ingress OpenAPI 请求体回归测试。"""

from __future__ import annotations

import pytest

# Regression: ISSUE-001 — result/event callback 丢失 OpenAPI JSON 请求体合同
# Found by /qa on 2026-08-04
# Report: .gstack/qa-reports/qa-report-localhost-8001-2026-08-04.md


@pytest.mark.parametrize(
    ("path", "required_fields", "result_values"),
    [
        (
            "/api/v1/callback/result",
            {"command_code", "device_code", "result", "finish_time", "source_event_id"},
            {"SUCCESS", "FAILED"},
        ),
        ("/api/v1/callback/event", {"device_code", "event_type"}, None),
    ],
)
def test_callback_ingress_openapi_declares_self_contained_required_json_body(
    path: str,
    required_fields: set[str],
    result_values: set[str] | None,
) -> None:
    from main import app

    operation = app.openapi()["paths"][path]["post"]
    request_body = operation["requestBody"]
    schema = request_body["content"]["application/json"]["schema"]

    assert request_body["required"] is True
    assert required_fields <= set(schema["required"])
    assert required_fields <= schema["properties"].keys()
    assert "$defs" not in schema
    if result_values is not None:
        assert set(schema["properties"]["result"]["enum"]) == result_values
