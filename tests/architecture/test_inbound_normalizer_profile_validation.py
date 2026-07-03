"""InboundNormalizerProfile Pydantic model_validator 测试 (Phase 1 CEO-009 / Packet D)。

主计划 §3.5.1 + H2 黑名单: InboundNormalizerProfile 必须拒绝不合规输入,
防止业务 capability 错误注入 inbound normalizer (R-I3a/R-I3b/R-I3c)。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.contracts.external_contract_profile import ExternalContractProfile, InboundNormalizerProfile


def test_inbound_normalizer_profile_accepts_valid_wms():
    """合规 WMS 入站 normalizer profile 通过校验。"""
    profile = InboundNormalizerProfile(
        normalizer_name="wms_grn_received",
        source_provider="wms",
        event_type="WMS_GRN_RECEIVED",
        correlation_resolution="manual",
    )
    assert profile.normalizer_name == "wms_grn_received"
    assert profile.correlation_resolution == "manual"


def test_inbound_normalizer_profile_accepts_valid_ecs():
    """合规 ECS 入站 normalizer profile 通过校验。"""
    profile = InboundNormalizerProfile(
        normalizer_name="ecs_pallet_arrived",
        source_provider="ecs",
        event_type="ECS_PALLET_ARRIVED",
        correlation_resolution="auto",
    )
    assert profile.normalizer_name == "ecs_pallet_arrived"


def test_inbound_normalizer_profile_accepts_valid_device():
    """合规 DEVICE 入站 normalizer profile 通过校验。"""
    profile = InboundNormalizerProfile(
        normalizer_name="device_command_result",
        source_provider="device",
        event_type="DEVICE_COMMAND_RESULT",
        correlation_resolution="hybrid",
    )
    assert profile.normalizer_name == "device_command_result"


def test_inbound_normalizer_profile_rejects_unknown_event_type_prefix():
    """event_type 必须以 WMS_/ECS_/DEVICE_ 之一开头。"""
    with pytest.raises(ValidationError) as exc_info:
        InboundNormalizerProfile(
            normalizer_name="bad_normalizer",
            source_provider="wms",
            event_type="FOO_BAR",
            correlation_resolution="manual",
        )
    assert "event_type 必须以" in str(exc_info.value)


def test_inbound_normalizer_profile_rejects_source_provider_event_type_mismatch():
    """source_provider 与 event_type 前缀必须一致 (wms→WMS_, ecs→ECS_, device→DEVICE_)。"""
    with pytest.raises(ValidationError) as exc_info:
        InboundNormalizerProfile(
            normalizer_name="mismatch",
            source_provider="wms",
            event_type="ECS_GRN_RECEIVED",
            correlation_resolution="manual",
        )
    assert "source_provider" in str(exc_info.value)
    assert "前缀不一致" in str(exc_info.value)


def test_inbound_normalizer_profile_rejects_invalid_correlation_resolution():
    """correlation_resolution 必为 manual/auto/hybrid 之一。"""
    with pytest.raises(ValidationError) as exc_info:
        InboundNormalizerProfile(
            normalizer_name="bad_correlation",
            source_provider="wms",
            event_type="WMS_GRN_RECEIVED",
            correlation_resolution="foo",
        )
    assert "correlation_resolution 必为" in str(exc_info.value)


def _profile_with_declared_normalizers() -> ExternalContractProfile:
    return ExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-06-25",
        environment="sandbox",
        runtime_capabilities_query=["WmsMasterDataPort.get_material"],
        runtime_capabilities_effect=["WmsFulfillmentPort.request_transport"],
        inbound_normalizers_event=["WMS_GRN_RECEIVED"],
        inbound_normalizers_result=["WMS_RACK_TASK_RESULT"],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/default",
        fixture_set_required_cases=["success"],
    )


def test_external_contract_profile_rejects_undeclared_event_normalizer():
    """provider profile 未声明的 event normalizer 不得进入 callback API admission。"""

    profile = _profile_with_declared_normalizers()

    with pytest.raises(PermissionError, match="未声明 event normalizer"):
        profile.ensure_inbound_normalizer_declared("WMS_RACK_ARRIVED", direction="event")


def test_external_contract_profile_rejects_undeclared_result_normalizer():
    """provider profile 未声明的 result normalizer 不得进入 callback API admission。"""

    profile = _profile_with_declared_normalizers()

    with pytest.raises(PermissionError, match="未声明 result normalizer"):
        profile.ensure_inbound_normalizer_declared("WMS_FULL_BOX_EXCHANGE_RESULT", direction="result")


def test_external_contract_profile_runtime_capability_admission():
    """runtime query/effect capability admission 使用精确 Port.method 合同。"""

    profile = _profile_with_declared_normalizers()

    profile.ensure_runtime_capability_declared("WmsMasterDataPort.get_material", direction="query")
    profile.ensure_runtime_capability_declared("WmsFulfillmentPort.request_transport", direction="effect")
    with pytest.raises(PermissionError, match="未声明 query capability"):
        profile.ensure_runtime_capability_declared("WmsMasterDataPort.get_warehouse", direction="query")
    with pytest.raises(PermissionError, match="未声明 effect capability"):
        profile.ensure_runtime_capability_declared("WmsFulfillmentPort.notify_pkg_binding", direction="effect")


def test_external_contract_profile_rejects_port_only_effect_capability():
    """effect capability 也必须使用 Port.method, 不能只声明 port 名。"""

    with pytest.raises(ValidationError, match="effect 元素必须为"):
        ExternalContractProfile(
            provider_code="WMS",
            contract_version="2026-06-25",
            environment="sandbox",
            runtime_capabilities_query=["WmsMasterDataPort.get_material"],
            runtime_capabilities_effect=["WmsFulfillmentPort"],
            timeout_retry_query_timeout_seconds=10,
            timeout_retry_effect_timeout_seconds=30,
            timeout_retry_retry_backoff_seconds=[1, 2, 4],
            fixture_set_path="tests/fixtures/external_contracts/wms/default",
            fixture_set_required_cases=["success"],
        )
