"""QA Callback 已退役权威 OpenAPI 回归合同。"""

import json

from src.app.callback.models import CallbackEventRequest


def test_callback_event_openapi_does_not_publish_retired_plugin_authority() -> None:
    # Regression: ISSUE-001 — Callback OpenAPI 仍把已退役 plugin contract 声明为事件权威
    # Found by /qa on 2026-08-11
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-8011-2026-08-11.md
    schema = CallbackEventRequest.model_json_schema()
    contract_text = json.dumps(schema, ensure_ascii=False)

    assert "plugin" not in contract_text.lower()
    assert "插件" not in contract_text
    assert "统一回调事件合同" in schema["properties"]["event_type"]["description"]
