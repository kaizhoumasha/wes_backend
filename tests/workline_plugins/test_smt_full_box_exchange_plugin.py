from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.workline_plugin_registry import get_workline_plugin_definition
from src.workline_plugins.smt_full_box_exchange import SmtFullBoxExchangePlugin
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import RuntimeIntentKind


def _ctx(*, config: dict | None = None, context: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        logger=MagicMock(),
        next=PluginNext(),
        config=config or {},
        trace_id="trace-full-box-001",
        workline=SimpleNamespace(line_code="WL-SMT-FULL-BOX-EXCHANGE-01"),
        session=SimpleNamespace(id=42, context_json=context or {}, current_wait_type="EXTERNAL_HTTP"),
    )


def _inbox(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(id=1, trace_id="trace-full-box-001", payload_json=payload)


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
    assert result[1].dispatch_key == "external:smt_full_box_exchange:release-001:FULL_BOX_EXCHANGE"
    assert result[1].target_code == "http://wms-rcs/api/full-box-exchange"
    assert result[1].payload_json["request_code"] == "FBE-release-001"
    assert result[1].payload_json["trace_id"] == "trace-full-box-001"
    assert result[1].payload_json["rack_release_id"] == "release-001"
    assert result[1].payload_json["source_workline_code"] == "WL-SMT-FULL-BOX-EXCHANGE-01"
    assert result[1].payload_json["exchange_area_code"] == "SMT_FULL_BOX_EXCHANGE_A"
    assert result[1].payload_json["callback_url"] == "http://wes/api/v1/callback/external"
    assert len(result[1].payload_json["bins"]) == 4
    assert len(result[1].payload_json["requested_bins"]) == 4
    assert len(result[1].payload_json["exchange_bins"]) == 4


@pytest.mark.asyncio
async def test_external_progress_callback_updates_context_without_completion() -> None:
    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context={"full_box_exchange": {"dispatch_key": "dispatch-001"}}),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
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
async def test_business_completed_callback_completes_session() -> None:
    result = await SmtFullBoxExchangePlugin().on_external_http(
        _ctx(context={"full_box_exchange": {"dispatch_key": "dispatch-001"}}),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
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
        _ctx(context={"full_box_exchange": {"dispatch_key": "dispatch-001"}}),
        _inbox(
            {
                "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
                "dispatch_key": "dispatch-001",
                "exchange_status": exchange_status,
            }
        ),
    )

    assert [intent.kind for intent in result] == [RuntimeIntentKind.BLOCK]
    assert result[0].reason_code == expected_reason_code
