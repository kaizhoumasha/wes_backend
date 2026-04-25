"""第二 WORKLINE 插件薄 spike 契约测试。

这些测试不是完整插件实现，而是 PR2 之后 manifest / topology /
business_key_resolver / sandbox 能力的压力用例。
"""

from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.workline_runtime.plugin_manifest import DeviceRoleRequirement, WorklinePluginManifest

EVENT_ENVELOPE_FIELDS = frozenset({"device_code", "event_type", "timestamp", "data"})
RESULT_ENVELOPE_FIELDS = frozenset({"command_code", "device_code", "result", "finish_time", "data", "error_detail"})
COMMAND_ENVELOPE_FIELDS = frozenset(
    {"device_code", "command_code", "task_type", "priority", "timeout", "timestamp", "params"}
)


class SpikeEventEnvelope(BaseModel):
    """白皮书事件包络：业务字段只能在 data 中。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str
    event_type: str
    timestamp: int | None = None
    data: dict[str, Any] | None = None


class SpikeResultEnvelope(BaseModel):
    """白皮书结果包络：业务字段只能在 data 中。"""

    model_config = ConfigDict(extra="forbid")

    command_code: str
    device_code: str
    result: Literal["SUCCESS", "FAILED"]
    finish_time: int | None = None
    data: dict[str, Any] | None = None
    error_detail: dict[str, Any] | None = None


class SpikeCommandEnvelope(BaseModel):
    """WES 下发命令包络：业务字段只能在 params 中。"""

    model_config = ConfigDict(extra="forbid")

    device_code: str
    command_code: str
    task_type: str
    priority: int | None = None
    timeout: int | None = None
    timestamp: int | None = None
    params: dict[str, Any]


class ToteArrivedData(BaseModel):
    """料箱到位事件 data 草案。"""

    model_config = ConfigDict(extra="forbid")

    tote_id: str
    station_code: str
    expected_weight_kg: float = Field(gt=0)
    tolerance_kg: float = Field(gt=0)


class WeighToteParams(BaseModel):
    """称重命令 params 草案。"""

    model_config = ConfigDict(extra="forbid")

    tote_id: str
    station_code: str


class DivertToteParams(BaseModel):
    """分流命令 params 草案。"""

    model_config = ConfigDict(extra="forbid")

    tote_id: str
    destination_lane: Literal["PASS_LANE", "HOLD_LANE"]
    reason_code: str


class WeighCompletedData(BaseModel):
    """称重结果 data 草案。"""

    model_config = ConfigDict(extra="forbid")

    tote_id: str
    actual_weight_kg: float = Field(gt=0)


def _resolve_tote_business_key(payload: dict[str, Any]) -> str:
    """第二插件业务键只来自 event.data.tote_id。"""

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("TOTE_ARRIVED.data is required")

    tote_id = data.get("tote_id")
    if not isinstance(tote_id, str) or not tote_id:
        raise ValueError("TOTE_ARRIVED.data.tote_id is required")
    return tote_id


SECOND_PLUGIN_SPIKE = WorklinePluginManifest(
    plugin_key="inbound_tote_qc",
    contract_version="spike-2026.04",
    business_key_resolver=_resolve_tote_business_key,
    required_device_roles=(
        DeviceRoleRequirement("ENTRY_SCANNER", 1, 1, frozenset({"scan_tote"})),
        DeviceRoleRequirement("WEIGH_SCALE", 1, 1, frozenset({"measure_weight"})),
        DeviceRoleRequirement("DIVERT_CONVEYOR", 1, 1, frozenset({"divert_lane"})),
    ),
    event_source_roles={"TOTE_ARRIVED": "ENTRY_SCANNER"},
    command_target_roles={"WEIGH_TOTE": "WEIGH_SCALE", "DIVERT_TOTE": "DIVERT_CONVEYOR"},
)
SECOND_PLUGIN_SPIKE_BOUNDARY = {
    "waits_for_callbacks": ("WEIGH_TOTE",),
    "runtime_private_branch_allowed": False,
    "sandbox_happy_path": {
        "run_mode": "SIMULATION",
        "dispatch_channel": "sandbox.device_commands",
        "manual_callback": "WEIGH_TOTE result SUCCESS",
        "payload_flag_required": False,
    },
}


TOTE_ARRIVED_EVENT = {
    "device_code": "SCAN01",
    "event_type": "TOTE_ARRIVED",
    "timestamp": 1777046400000,
    "data": {
        "tote_id": "TOTE-20260425-001",
        "station_code": "INBOUND_QC_01",
        "expected_weight_kg": 12.5,
        "tolerance_kg": 0.2,
    },
}

WEIGH_TOTE_COMMAND = {
    "device_code": "SCALE01",
    "command_code": "CMD-WEIGH-001",
    "task_type": "WEIGH_TOTE",
    "priority": 5,
    "timeout": 30000,
    "timestamp": 1777046400100,
    "params": {
        "tote_id": "TOTE-20260425-001",
        "station_code": "INBOUND_QC_01",
    },
}

WEIGH_COMPLETED_RESULT = {
    "command_code": "CMD-WEIGH-001",
    "device_code": "SCALE01",
    "result": "SUCCESS",
    "finish_time": 1777046405000,
    "data": {
        "tote_id": "TOTE-20260425-001",
        "actual_weight_kg": 12.58,
    },
}

BUSINESS_NG_DECISION = {
    "classification": "business_decision",
    "reason_code": "WEIGHT_OUT_OF_TOLERANCE",
    "message": "料箱重量超出允差，分流到异常线",
    "business_key": "TOTE-20260425-001",
    "evidence": {
        "expected_weight_kg": 12.5,
        "actual_weight_kg": 13.1,
        "tolerance_kg": 0.2,
    },
}

SYSTEM_EXCEPTION = {
    "classification": "hardware_failure",
    "reason_code": "WEIGH_SCALE_FAILED",
    "message": "称重设备执行失败",
    "business_key": "TOTE-20260425-001",
    "error_detail": {
        "code": "SCALE_TIMEOUT",
        "msg": "Scale did not complete within timeout",
    },
}


def _assert_only_allowed_top_level(payload: dict[str, Any], allowed_fields: frozenset[str]) -> None:
    extra_fields = set(payload) - allowed_fields
    assert extra_fields == set()


def test_second_spike_declares_platform_primitives_without_runtime_private_branch() -> None:
    """第二场景必须覆盖 manifest、拓扑、等待回调和 sandbox 的最小压力点。"""

    assert SECOND_PLUGIN_SPIKE.plugin_key == "inbound_tote_qc"
    assert SECOND_PLUGIN_SPIKE.contract_version == "spike-2026.04"
    assert SECOND_PLUGIN_SPIKE_BOUNDARY["runtime_private_branch_allowed"] is False

    roles_by_name = {item.role: item for item in SECOND_PLUGIN_SPIKE.required_device_roles}
    assert roles_by_name["ENTRY_SCANNER"].capabilities == frozenset({"scan_tote"})
    assert roles_by_name["WEIGH_SCALE"].min_count == 1
    assert roles_by_name["WEIGH_SCALE"].max_count == 1
    assert roles_by_name["DIVERT_CONVEYOR"].capabilities == frozenset({"divert_lane"})

    assert SECOND_PLUGIN_SPIKE.event_source_roles == {"TOTE_ARRIVED": ("ENTRY_SCANNER",)}
    assert SECOND_PLUGIN_SPIKE.command_target_roles == {
        "WEIGH_TOTE": ("WEIGH_SCALE",),
        "DIVERT_TOTE": ("DIVERT_CONVEYOR",),
    }
    assert SECOND_PLUGIN_SPIKE_BOUNDARY["waits_for_callbacks"] == ("WEIGH_TOTE",)
    assert SECOND_PLUGIN_SPIKE_BOUNDARY["sandbox_happy_path"]["run_mode"] == "SIMULATION"
    assert SECOND_PLUGIN_SPIKE_BOUNDARY["sandbox_happy_path"]["payload_flag_required"] is False


def test_second_spike_business_key_resolver_reads_event_data() -> None:
    """非 SMT 业务键应由插件 contract/manifest 提供解析器，而不是 runtime 私有分支。"""

    assert SECOND_PLUGIN_SPIKE.business_key_resolver(TOTE_ARRIVED_EVENT) == "TOTE-20260425-001"

    invalid_event = {
        "device_code": "SCAN01",
        "event_type": "TOTE_ARRIVED",
        "timestamp": 1777046400000,
        "data": {
            "station_code": "INBOUND_QC_01",
            "expected_weight_kg": 12.5,
            "tolerance_kg": 0.2,
        },
    }
    with pytest.raises(ValueError, match="tote_id is required"):
        SECOND_PLUGIN_SPIKE.business_key_resolver(invalid_event)


def test_second_spike_event_and_result_fixtures_use_data_boundary() -> None:
    """事件和结果回调的业务字段只能出现在 data 中。"""

    _assert_only_allowed_top_level(TOTE_ARRIVED_EVENT, EVENT_ENVELOPE_FIELDS)
    event_envelope = SpikeEventEnvelope.model_validate(TOTE_ARRIVED_EVENT)
    event_data = ToteArrivedData.model_validate(event_envelope.data)
    assert event_data.tote_id == "TOTE-20260425-001"

    flattened_event = {**TOTE_ARRIVED_EVENT, "tote_id": "TOTE-20260425-001"}
    with pytest.raises(ValidationError):
        SpikeEventEnvelope.model_validate(flattened_event)

    _assert_only_allowed_top_level(WEIGH_COMPLETED_RESULT, RESULT_ENVELOPE_FIELDS)
    result_envelope = SpikeResultEnvelope.model_validate(WEIGH_COMPLETED_RESULT)
    result_data = WeighCompletedData.model_validate(result_envelope.data)
    assert result_data.actual_weight_kg == 12.58

    flattened_result = {**WEIGH_COMPLETED_RESULT, "actual_weight_kg": 12.58}
    with pytest.raises(ValidationError):
        SpikeResultEnvelope.model_validate(flattened_result)


def test_second_spike_command_fixture_uses_params_boundary() -> None:
    """WES 下发命令的业务字段只能出现在 params 中。"""

    _assert_only_allowed_top_level(WEIGH_TOTE_COMMAND, COMMAND_ENVELOPE_FIELDS)
    command_envelope = SpikeCommandEnvelope.model_validate(WEIGH_TOTE_COMMAND)
    params = WeighToteParams.model_validate(command_envelope.params)
    assert params.tote_id == "TOTE-20260425-001"

    flattened_command = {**WEIGH_TOTE_COMMAND, "tote_id": "TOTE-20260425-001"}
    with pytest.raises(ValidationError):
        SpikeCommandEnvelope.model_validate(flattened_command)

    divert_params = DivertToteParams.model_validate(
        {
            "tote_id": "TOTE-20260425-001",
            "destination_lane": "PASS_LANE",
            "reason_code": "WEIGHT_OK",
        }
    )
    assert divert_params.destination_lane == "PASS_LANE"


def test_second_spike_separates_business_ng_from_system_exception() -> None:
    """业务 NG 是业务决策；设备失败和执行异常是 failure。"""

    assert BUSINESS_NG_DECISION["classification"] == "business_decision"
    assert BUSINESS_NG_DECISION["reason_code"] == "WEIGHT_OUT_OF_TOLERANCE"
    assert "error_detail" not in BUSINESS_NG_DECISION
    assert BUSINESS_NG_DECISION["evidence"]["actual_weight_kg"] == 13.1

    assert SYSTEM_EXCEPTION["classification"] == "hardware_failure"
    assert SYSTEM_EXCEPTION["reason_code"] == "WEIGH_SCALE_FAILED"
    assert SYSTEM_EXCEPTION["error_detail"]["code"] == "SCALE_TIMEOUT"
