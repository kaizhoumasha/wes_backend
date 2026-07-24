"""WMS EFFECT callback 仅作为状态查询提示的生产路由合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.services.inbox.wms_typed_effect_callback_router import WmsTypedEffectCallbackRouter
from src.app.runtime.system_capabilities.wms import provider_catalog

WMS_PROVIDER_PROFILE = provider_catalog.WMS_PROVIDER_PROFILE


class _StatusService:
    def __init__(self, *, outcome: str = "SCHEDULED", error: Exception | None = None) -> None:
        self.outcome = outcome
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def request_status_check_hint(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(outcome=self.outcome)


def _hint_payload(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "operation_identity": "wms.inventory.confirm_inbound@v1",
        "idempotency_key": "idem-confirm-001",
        "dispatch_key": "confirm-inbound-001",
    }
    data.update(overrides)
    return {
        "callback_type": "WMS_EFFECT_STATUS_HINT",
        "source_system": "WMS",
        "source_event_id": "wms-hint-001",
        "trace_id": "trace-hint-001",
        "data": data,
    }


def test_effect_profile_can_omit_callback_contract() -> None:
    profile_without_callback = type(WMS_PROVIDER_PROFILE)(
        identity=WMS_PROVIDER_PROFILE.identity,
        bindings=WMS_PROVIDER_PROFILE.bindings,
    )

    assert profile_without_callback.callbacks == ()


def test_provider_registers_one_generic_status_hint_contract() -> None:
    hint_contract = getattr(provider_catalog, "WMS_EFFECT_STATUS_HINT_CALLBACK", None)

    assert WMS_PROVIDER_PROFILE.callbacks == (hint_contract,)
    assert hint_contract.callback_type == "WMS_EFFECT_STATUS_HINT"
    assert set(hint_contract.payload_model.model_fields) == {
        "operation_identity",
        "idempotency_key",
        "dispatch_key",
    }


def test_production_ingress_accepts_generic_effect_status_hint() -> None:
    from src.app.callback.services.callback_ingress_service import _normalize_external_callback_payload

    normalized = _normalize_external_callback_payload(_hint_payload())

    assert normalized["callback_type"] == "WMS_EFFECT_STATUS_HINT"


@pytest.mark.parametrize("missing_field", ["operation_identity", "idempotency_key", "dispatch_key"])
def test_production_ingress_rejects_hint_without_required_correlation_field(missing_field: str) -> None:
    from src.app.callback.services.callback_ingress_service import _normalize_external_callback_payload

    payload = _hint_payload()
    data = payload["data"]
    assert isinstance(data, dict)
    data.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        _normalize_external_callback_payload(payload)


@pytest.mark.asyncio
async def test_generic_hint_routes_only_frozen_correlation_to_status_service() -> None:
    status_service = _StatusService()
    router = WmsTypedEffectCallbackRouter(status_service=status_service)

    handled = await router.route(
        SimpleNamespace(),
        callback_type="WMS_EFFECT_STATUS_HINT",
        payload=_hint_payload(),
    )

    assert handled is True
    assert status_service.calls == [
        {
            "operation_identity": "wms.inventory.confirm_inbound@v1",
            "idempotency_key": "idem-confirm-001",
            "dispatch_key": "confirm-inbound-001",
        }
    ]


@pytest.mark.asyncio
async def test_unknown_operation_is_named_failure_before_status_scheduling() -> None:
    status_service = _StatusService()
    router = WmsTypedEffectCallbackRouter(status_service=status_service)

    with pytest.raises(ValueError, match="WMS_EFFECT_STATUS_HINT_OPERATION_UNKNOWN"):
        await router.route(
            SimpleNamespace(),
            callback_type="WMS_EFFECT_STATUS_HINT",
            payload=_hint_payload(operation_identity="wms.inventory.unknown@v1"),
        )

    assert status_service.calls == []


@pytest.mark.asyncio
async def test_unknown_or_mismatched_correlation_is_propagated_as_named_failure() -> None:
    status_service = _StatusService(error=ValueError("WMS_EFFECT_STATUS_HINT_CORRELATION_MISMATCH"))
    router = WmsTypedEffectCallbackRouter(status_service=status_service)

    with pytest.raises(ValueError, match="WMS_EFFECT_STATUS_HINT_CORRELATION_MISMATCH"):
        await router.route(
            SimpleNamespace(),
            callback_type="WMS_EFFECT_STATUS_HINT",
            payload=_hint_payload(),
        )


@pytest.mark.asyncio
async def test_duplicate_or_late_hint_is_safely_consumed_without_terminal_write() -> None:
    for outcome in ("ALREADY_DUE", "TERMINAL"):
        status_service = _StatusService(outcome=outcome)
        router = WmsTypedEffectCallbackRouter(status_service=status_service)

        handled = await router.route(
            SimpleNamespace(),
            callback_type="WMS_EFFECT_STATUS_HINT",
            payload=_hint_payload(),
        )

        assert handled is True


@pytest.mark.asyncio
async def test_non_hint_callback_keeps_existing_external_callback_path() -> None:
    status_service = _StatusService()
    router = WmsTypedEffectCallbackRouter(status_service=status_service)

    handled = await router.route(
        SimpleNamespace(),
        callback_type="WMS_RACK_ARRIVED",
        payload={"callback_type": "WMS_RACK_ARRIVED", "dispatch_key": "rack-001"},
    )

    assert handled is False
    assert status_service.calls == []
