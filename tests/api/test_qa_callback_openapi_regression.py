"""QA external callback OpenAPI 回归测试。"""

from __future__ import annotations

# Regression: ISSUE-001 — external callback 必须公开 JSON 请求包络
# Found by /qa on 2026-07-24
# Report: .gstack/qa-reports/qa-report-127-0-0-1-8011-2026-07-24.md


def test_external_callback_openapi_declares_request_body_contract() -> None:
    from main import app
    from src.app.callback.contracts.external_callbacks import validate_external_callback_type

    operation = app.openapi()["paths"]["/api/v1/callback/external"]["post"]
    request_body = operation["requestBody"]
    schema = request_body["content"]["application/json"]["schema"]
    public_example = schema["examples"][0]

    assert request_body["required"] is True
    assert "callback_type" in schema["required"]
    assert {"callback_type", "trace_id", "source_event_id", "occurred_at", "dispatch_key", "data"} <= schema[
        "properties"
    ].keys()
    assert public_example["callback_type"] == "WMS_EFFECT_STATUS_HINT"
    assert public_example["occurred_at"].endswith("Z")
    assert validate_external_callback_type(public_example) == "WMS_EFFECT_STATUS_HINT"
