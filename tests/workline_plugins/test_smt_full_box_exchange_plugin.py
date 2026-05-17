from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_plugins.smt_full_box_exchange import SmtFullBoxExchangePlugin
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.plugin_sdk.contracts import NormalizedExternalCallback
from src.workline_runtime.runtime_intent import RuntimeIntentKind


def _ctx(
    *,
    config: dict | None = None,
    context: dict | None = None,
    normalized_input: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        logger=MagicMock(),
        next=PluginNext(),
        config=config or {},
        trace_id="trace-full-box-001",
        workline=SimpleNamespace(line_code="WL-SMT-FULL-BOX-EXCHANGE-01"),
        session=SimpleNamespace(id=42, context_json=context or {}, current_wait_type="EXTERNAL_HTTP"),
        normalized_input=normalized_input,
    )


def _inbox(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(id=1, trace_id="trace-full-box-001", payload_json=payload)


def _exchange_session_context() -> dict:
    return {
        "rack_release_id": "release-001",
        "full_box_exchange": {"dispatch_key": "dispatch-001"},
    }


def _release_payload(*, usage_snapshot: float = 0.2, status: str = "IN_USE") -> dict:
    return {
        "message_type": "DEVICE_EVENT",
        "event_type": "SINGLE_LAYER_RACK_RELEASED",
        "canonical_event_type": "SINGLE_LAYER_RACK_RELEASED",
        "data": {
            "rack_release_id": "release-001",
            "single_layer_rack_code": "RACK-SL-001",
            "release_cycle_seq": 1,
            "snapshot_hash": "snapshot-hash-001",
            "bin_snapshots": [
                {
                    "slot_code": f"S{index}",
                    "bin_code": f"BIN-{index:03d}",
                    "bin_type_code": "SMT_BIN",
                    "bin_execution_status": status,
                    "usage_snapshot": usage_snapshot,
                }
                for index in range(1, 5)
            ],
        },
    }


def _spec_release_payload(*, usage: float = 0.2, status: str = "IN_USE") -> dict:
    return {
        "message_type": "DEVICE_EVENT",
        "event_type": "SINGLE_LAYER_RACK_RELEASED",
        "canonical_event_type": "SINGLE_LAYER_RACK_RELEASED",
        "data": {
            "rack_release_id": "release-001",
            "single_layer_rack_id": "RACK-SL-001",
            "source_classifier_line_code": "WL-SMT-CLASSIFIER-01",
            "source_task_batch_id": "batch-001",
            "released_at": "2026-05-16T10:00:00+00:00",
            "moved_out_at": "2026-05-16T10:03:00+00:00",
            "release_cycle_seq": 1,
            "snapshot_hash": "snapshot-hash-001",
            "bins": [
                {
                    "slot_code": f"S{index}",
                    "bin_id": f"BIN-{index:03d}",
                    "bin_type_code": "SMT_BIN",
                    "status": status,
                    "usage": usage,
                }
                for index in range(1, 5)
            ],
        },
    }


def test_smt_full_box_exchange_plugin_is_registered() -> None:
    definition = get_workline_plugin_definition("smt_full_box_exchange")

    assert definition is not None
    assert definition.plugin_class is SmtFullBoxExchangePlugin
    assert definition.manifest.supported_events == frozenset({"SINGLE_LAYER_RACK_RELEASED"})
    assert definition.manifest.resolve_business_key(_release_payload()) == "release-001"


@pytest.mark.asyncio
async def test_release_event_completes_without_exchange_when_policy_not_hit() -> None:
    result = await SmtFullBoxExchangePlugin().on_device_event(
        _ctx(),
        _inbox(_release_payload(usage_snapshot=0.2, status="IN_USE")),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.COMPLETE]
    assert result[0].context_patch["rack_release_id"] == "release-001"
    assert result[0].context_patch["exchange_required"] is False
    assert result[0].context_patch["exchange_policy_version"] == "default"
    assert result[0].context_patch["qualified_bin_count"] == 0
    assert len(result[0].context_patch["evaluated_bins"]) == 4


@pytest.mark.asyncio
async def test_release_event_requests_external_exchange_when_any_bin_is_full() -> None:
    result = await SmtFullBoxExchangePlugin().on_device_event(
        _ctx(
            config={
                "external_endpoints": {
                    "wms_rcs_full_box_exchange_url": "http://wms-rcs/api/full-box-exchange",
                },
                "exchange_area_code": "SMT_FULL_BOX_EXCHANGE_A",
                "callback_url": "http://wes/api/v1/callback/external",
                "timeouts": {"external_exchange_seconds": 1800},
            }
        ),
        _inbox(_release_payload(usage_snapshot=0.91, status="IN_USE")),
    )

    assert [intent.kind for intent in result] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]
    assert result[0].context_patch["exchange_required"] is True
    assert result[1].dispatch_key == "external:smt_full_box_exchange:release-001:FULL_BIN_EXCHANGE"
    assert result[1].target_code == "http://wms-rcs/api/full-box-exchange"
    assert result[1].payload_json["request_code"] == "external:smt_full_box_exchange:release-001:FULL_BIN_EXCHANGE"
    assert result[1].payload_json["exchange_request_code"] == result[1].dispatch_key
    assert result[1].payload_json["trace_id"] == "trace-full-box-001"
    assert result[1].payload_json["rack_release_id"] == "release-001"
    assert result[1].payload_json["source_workline_code"] == "WL-SMT-FULL-BOX-EXCHANGE-01"
    assert result[1].payload_json["exchange_area_code"] == "SMT_FULL_BOX_EXCHANGE_A"
    assert result[1].payload_json["callback_url"] == "http://wes/api/v1/callback/external"
    assert result[1].payload_json["exchange_policy"]["policy_version"] == "default"
    assert result[1].payload_json["exchange_policy"]["expected_bin_count"] == 4
    assert result[0].context_patch["qualified_bin_count"] == 4
    assert len(result[0].context_patch["evaluated_bins"]) == 4
    assert len(result[1].payload_json["bins"]) == 4
    assert len(result[1].payload_json["requested_bins"]) == 4
    assert len(result[1].payload_json["exchange_bins"]) == 4


@pytest.mark.asyncio
async def test_release_event_accepts_spec_bins_contract() -> None:
    result = await SmtFullBoxExchangePlugin().on_device_event(
        _ctx(
            config={
                "external_endpoints": {
                    "wms_rcs_full_box_exchange_url": "http://wms-rcs/api/full-box-exchange",
                },
                "exchange_area_code": "SMT_FULL_BOX_EXCHANGE_A",
                "callback_url": "http://wes/api/v1/callback/external",
                "timeouts": {"external_exchange_seconds": 1800},
            }
        ),
        _inbox(_spec_release_payload(usage=0.91, status="IN_USE")),
    )

    assert [intent.kind for intent in result] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.EXTERNAL_REQUEST,
    ]
    assert result[0].context_patch["single_layer_rack_id"] == "RACK-SL-001"
    assert result[1].payload_json["single_layer_rack_id"] == "RACK-SL-001"
    assert result[1].payload_json["bins"][0]["bin_id"] == "BIN-001"
    assert result[1].payload_json["exchange_bins"][0]["bin_id"] == "BIN-001"


@pytest.mark.asyncio
async def test_release_event_blocks_duplicate_slot_in_spec_bins_contract() -> None:
    payload = _spec_release_payload(usage=0.91, status="IN_USE")
    payload["data"]["bins"][1]["slot_code"] = "S1"

    result = await SmtFullBoxExchangePlugin().on_device_event(_ctx(), _inbox(payload))

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == "PAYLOAD_INVALID"
    assert "重复槽位" in result[0].message


@pytest.mark.parametrize(
    ("bin_patch", "expected_message"),
    [
        ({"bin_id": ""}, "缺少料箱编码"),
        ({"usage": None}, "缺少 usage"),
        ({"usage": 1.2}, "usage 必须在 0 到 1 之间"),
        ({"status": "BROKEN"}, "status 不支持"),
    ],
)
@pytest.mark.asyncio
async def test_release_event_blocks_invalid_bin_fields_in_spec_bins_contract(
    bin_patch: dict,
    expected_message: str,
) -> None:
    payload = _spec_release_payload(usage=0.91, status="IN_USE")
    payload["data"]["bins"][0].update(bin_patch)

    result = await SmtFullBoxExchangePlugin().on_device_event(_ctx(), _inbox(payload))

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == "PAYLOAD_INVALID"
    assert expected_message in result[0].message


@pytest.mark.asyncio
async def test_release_event_blocks_when_exchange_timeout_is_missing() -> None:
    """满箱交换 WorkLine 必须显式配置外部等待超时，避免静默使用隐式默认值。"""

    result = await SmtFullBoxExchangePlugin().on_device_event(
        _ctx(
            config={
                "external_endpoints": {
                    "wms_rcs_full_box_exchange_url": "http://wms-rcs/api/full-box-exchange",
                },
            }
        ),
        _inbox(_release_payload(usage_snapshot=0.91, status="IN_USE")),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == "FULL_BOX_EXCHANGE_TIMEOUT_MISSING"


@pytest.mark.asyncio
async def test_release_event_blocks_when_exchange_target_is_missing_with_suggested_action() -> None:
    result = await SmtFullBoxExchangePlugin().on_device_event(
        _ctx(config={"timeouts": {"external_exchange_seconds": 1800}}),
        _inbox(_release_payload(usage_snapshot=0.91, status="IN_USE")),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == "FULL_BOX_EXCHANGE_TARGET_MISSING"
    assert result[0].suggested_action == "配置 WorkLine external_endpoints.wms_rcs_full_box_exchange_url"


@pytest.mark.asyncio
async def test_external_progress_callback_updates_context_without_completion() -> None:
    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context()),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "trace_id": "trace-full-box-001",
                "rack_release_id": "release-001",
                "dispatch_key": "dispatch-001",
                "exchange_status": "QUEUED",
                "queue_position": 2,
            }
        ),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT]
    assert result[0].context_patch["full_box_exchange"]["exchange_status"] == "QUEUED"
    assert result[0].context_patch["full_box_exchange"]["queue_position"] == 2


@pytest.mark.asyncio
async def test_external_progress_callback_accepts_normalized_input() -> None:
    normalized_input = NormalizedExternalCallback(
        callback_type="WMS_FULL_BOX_EXCHANGE_RESULT",
        trace_id="trace-full-box-001",
        source_system="WMS",
        payload={
            "rack_release_id": "release-001",
            "dispatch_key": "dispatch-001",
            "exchange_status": "QUEUED",
            "queue_position": 2,
        },
    )

    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context(), normalized_input=normalized_input),
        _inbox({}),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT]
    assert result[0].context_patch["full_box_exchange"]["exchange_status"] == "QUEUED"
    assert result[0].context_patch["full_box_exchange"]["queue_position"] == 2


@pytest.mark.parametrize("exchange_status", ["PHYSICAL_COMPLETED", "RESOURCE_PROJECTED"])
@pytest.mark.asyncio
async def test_external_projection_callback_blocks_without_post_exchange_relations(exchange_status: str) -> None:
    """物理完成或资源投影回调缺少交换后关系时，只能进入对账，不能推进 Session。"""

    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context()),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "trace_id": "trace-full-box-001",
                "rack_release_id": "release-001",
                "dispatch_key": "dispatch-001",
                "exchange_status": exchange_status,
            }
        ),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == "EXCHANGE_RECONCILING"
    assert result[0].suggested_action == "要求 WMS/RCS 补传 post_exchange_relations 后重放回调"


@pytest.mark.asyncio
async def test_wms_confirmed_callback_blocks_without_confirmation_with_suggested_action() -> None:
    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context()),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "trace_id": "trace-full-box-001",
                "rack_release_id": "release-001",
                "dispatch_key": "dispatch-001",
                "exchange_status": "WMS_CONFIRMED",
            }
        ),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == "EXCHANGE_WMS_CONFIRMATION_INVALID"
    assert result[0].suggested_action == "要求 WMS 补传 wms_confirmation 后重放回调"


@pytest.mark.asyncio
async def test_business_completed_callback_completes_session() -> None:
    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context()),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "trace_id": "trace-full-box-001",
                "rack_release_id": "release-001",
                "dispatch_key": "dispatch-001",
                "exchange_status": "BUSINESS_COMPLETED",
                "wms_confirmation": {"wms_document_id": "WMS-DOC-001"},
            }
        ),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.COMPLETE]
    assert result[0].context_patch["full_box_exchange"]["exchange_status"] == "BUSINESS_COMPLETED"


@pytest.mark.parametrize(
    ("exchange_status", "expected_reason_code"),
    [
        ("WMS_REJECTED", "EXCHANGE_WMS_REJECTED"),
        ("REJECTED_EXCHANGE_AREA_FULL", "EXCHANGE_RESOURCE_UNAVAILABLE"),
        ("REJECTED_EMPTY_BIN_UNAVAILABLE", "EXCHANGE_RESOURCE_UNAVAILABLE"),
        ("FAILED_AGV", "EXCHANGE_EXECUTION_FAILED"),
        ("FAILED_CTU", "EXCHANGE_EXECUTION_FAILED"),
        ("UNKNOWN", "EXCHANGE_STATUS_UNKNOWN"),
    ],
)
@pytest.mark.asyncio
async def test_external_failure_callback_blocks_with_status_specific_reason(
    exchange_status: str,
    expected_reason_code: str,
) -> None:
    """WMS/RCS 细分失败状态应进入人工阻断，而不是被当作非法 payload。"""

    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context()),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "trace_id": "trace-full-box-001",
                "rack_release_id": "release-001",
                "dispatch_key": "dispatch-001",
                "exchange_status": exchange_status,
            }
        ),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == expected_reason_code
    assert result[0].suggested_action is not None


@pytest.mark.parametrize(
    ("payload_patch", "expected_reason_code"),
    [
        ({"trace_id": "trace-other"}, "EXCHANGE_TRACE_ID_MISMATCH"),
        ({"rack_release_id": "release-other"}, "EXCHANGE_RACK_RELEASE_MISMATCH"),
    ],
)
@pytest.mark.asyncio
async def test_external_callback_blocks_when_session_identity_mismatches(
    payload_patch: dict,
    expected_reason_code: str,
) -> None:
    """WMS/RCS 回调必须归属于当前等待中的 trace 和 rack_release。"""

    payload = {
        "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
        "trace_id": "trace-full-box-001",
        "rack_release_id": "release-001",
        "dispatch_key": "dispatch-001",
        "exchange_status": "QUEUED",
    }
    payload.update(payload_patch)

    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context=_exchange_session_context()),
        _inbox(payload),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == expected_reason_code
