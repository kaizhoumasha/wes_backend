"""粗分机 deployment 的严格 wire/value 解析与稳定身份 helper。"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any, cast

from src.app.execution.models import InboundEvidenceKind
from src.app.execution.services.fact_builder import FactBuilder
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone
from wes_plugin_sdk import (
    DevicePosition,
    DeviceResultReadyFact,
    EvidenceReadyFact,
    FactReference,
    TransportRackPosition,
    TransportResultReadyFact,
)

from rough_sorter.facts import (
    DeviceStep,
)

if TYPE_CHECKING:
    from src.app.execution.models import InboundEvidence, MaterialExecution


def stable_operation_id(evidence: InboundEvidence, purpose: str) -> str:
    if evidence.id is None:
        raise ValueError("stable operation identity requires persisted evidence")
    timestamp_ms = int(timezone.to_utc(evidence.received_at).timestamp() * 1000)
    entropy = int.from_bytes(hashlib.sha256(f"{evidence.id}:{purpose}".encode()).digest(), "big") >> (256 - 74)
    return new_uuid7(timestamp_ms=timestamp_ms, random_bits=entropy)


def base_fact_for_persisted_evidence(
    evidence: InboundEvidence,
    execution: MaterialExecution,
    fact_id: str,
) -> FactReference:
    common = {
        "fact_id": fact_id,
        "evidence_id": str(evidence.id),
        "fact_version": required_string(evidence.contract_version, "evidence.contract_version"),
        "material_execution_id": execution.execution_code,
    }
    if evidence.kind == InboundEvidenceKind.DEVICE_EVENT:
        return EvidenceReadyFact(**common)
    if evidence.kind == InboundEvidenceKind.DEVICE_RESULT:
        return DeviceResultReadyFact(
            **common,
            command_code=required_string(evidence.command_code, "evidence.command_code"),
            device_code=required_string(evidence.device_code, "evidence.device_code"),
            material_trace_id=execution.material_trace_id,
        )
    if evidence.kind == InboundEvidenceKind.WMS_RESULT:
        from dataclasses import replace

        return replace(FactBuilder().build(evidence, execution), fact_id=fact_id)
    if evidence.kind == InboundEvidenceKind.WMS_EVENT:
        return FactBuilder().build(evidence, execution)
    if evidence.kind == InboundEvidenceKind.TRANSPORT_RESULT:
        return TransportResultReadyFact(
            **common,
            transport_task_id=required_string(evidence.transport_task_id, "evidence.transport_task_id"),
        )
    raise ValueError("当前粗分 FactFactory 不支持该 evidence kind")


def position_binding(snapshot: Any, role: str) -> Any:
    matches = tuple(item for item in snapshot.workline.position_bindings if item.position_role == role)
    if len(matches) != 1:
        raise ValueError(f"WorkLine position role {role} missing or ambiguous")
    return matches[0]


def required_position(value: DevicePosition | None, field_name: str) -> DevicePosition:
    if type(value) is not DevicePosition:
        raise ValueError(f"{field_name} 必须是 DevicePosition")
    return value


def device_binding(snapshot: Any, role: str) -> Any:
    matches = tuple(item for item in snapshot.workline.device_bindings if item.device_role == role)
    if len(matches) != 1:
        raise ValueError(f"WorkLine device role {role} missing or ambiguous")
    return matches[0]


def bound_position(snapshot: Any, role: str, material_trace_id: str) -> DevicePosition:
    binding = position_binding(snapshot, role)
    return DevicePosition(binding.location_id, binding.location_type, material_trace_id)


def wire_position(value: DevicePosition, material_trace_id: str, expected_type: str) -> DevicePosition:
    if not isinstance(value, DevicePosition):
        raise TypeError("WMS position 必须是 typed DevicePosition")
    if value.material_trace_id != material_trace_id or value.location_type != expected_type:
        raise ValueError("WMS position identity/type 不匹配")
    return value


def transport_rack_position(value: object) -> TransportRackPosition:
    data = strict_object(value, {"kind", "location_code"}, "transport rack position")
    if data["kind"] != "RACK_POSITION":
        raise ValueError("transport final_position 必须是 RACK_POSITION")
    return TransportRackPosition(required_string(data["location_code"], "final_position.location_code"))


def device_position(value: object, material_trace_id: str) -> DevicePosition:
    data = strict_object(value, {"location_id", "location_type", "material_trace_id"}, "position")
    if data["material_trace_id"] != material_trace_id:
        raise ValueError("position material_trace_id 不匹配")
    return DevicePosition(
        location_id=required_string(data["location_id"], "position.location_id"),
        location_type=required_string(data["location_type"], "position.location_type"),
        material_trace_id=material_trace_id,
    )


def command_position(value: object, material_trace_id: str) -> DevicePosition:
    data = strict_object(
        value,
        {"location_id", "location_type", "material_trace_id", "rack_id", "rack_slot_code", "bin_code", "bin_cell_id"},
        "DeviceCommand position",
    )
    if data["material_trace_id"] != material_trace_id:
        raise ValueError("DeviceCommand position trace 不匹配")
    for field_name in ("rack_id", "rack_slot_code", "bin_code", "bin_cell_id"):
        item = data[field_name]
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"DeviceCommand position {field_name} 非法")
    return DevicePosition(
        location_id=required_string(data["location_id"], "position.location_id"),
        location_type=required_string(data["location_type"], "position.location_type"),
        material_trace_id=material_trace_id,
        rack_id=cast("str | None", data["rack_id"]),
        rack_slot_code=cast("str | None", data["rack_slot_code"]),
        bin_code=cast("str | None", data["bin_code"]),
        bin_cell_id=cast("str | None", data["bin_cell_id"]),
    )


def device_step(
    task_type: str,
    source: DevicePosition,
    target: DevicePosition,
) -> tuple[Any, str]:
    identity = (task_type, source.location_type, target.location_type)
    try:
        return {
            ("PICK_AND_PUT", "MEASUREMENT_POSITION", "PIPELINE_INLET"): (
                DeviceStep.MEASUREMENT_TO_INLET,
                "MEASUREMENT_DEVICE",
            ),
            ("MOVE_FORWARD", "PIPELINE_INLET", "PIPELINE_OUTLET"): (
                DeviceStep.TRANSFER_TO_OUTLET,
                "TRANSFER_DEVICE",
            ),
            ("PICK_AND_PUT", "PIPELINE_OUTLET", "RACK_CELL"): (DeviceStep.PLACEMENT_TO_CELL, "PLACEMENT_DEVICE"),
            ("PICK_AND_PUT", "MEASUREMENT_POSITION", "NG_POSITION"): (
                DeviceStep.MEASUREMENT_TO_NG,
                "MEASUREMENT_DEVICE",
            ),
            ("PICK_AND_PUT", "PIPELINE_OUTLET", "NG_POSITION"): (DeviceStep.PLACEMENT_TO_NG, "PLACEMENT_DEVICE"),
        }[identity]
    except KeyError as exc:
        raise ValueError("DeviceCommand task/source/target 不属于 rough sorter 拓扑") from exc


def validate_source_evidence_for_step(evidence: InboundEvidence, step: Any) -> None:
    expected_operations = {
        DeviceStep.MEASUREMENT_TO_INLET: {"inbound.material.admission_decide@v1"},
        DeviceStep.TRANSFER_TO_OUTLET: set(),
        DeviceStep.PLACEMENT_TO_CELL: {"inbound.material.target_decide@v1"},
        DeviceStep.MEASUREMENT_TO_NG: {"inbound.material.admission_decide@v1"},
        DeviceStep.PLACEMENT_TO_NG: {"inbound.material.target_decide@v1"},
    }[step]
    if expected_operations:
        if evidence.kind != InboundEvidenceKind.WMS_RESULT or evidence.operation not in expected_operations:
            raise ValueError("DeviceCommand source evidence 不匹配业务步骤")
    elif evidence.kind != InboundEvidenceKind.DEVICE_RESULT:
        raise ValueError("TRANSFER command 必须由前一 device result evidence 创建")


def strict_object(value: object, keys: set[str], field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{field_name} 必须是严格对象")
    return cast("dict[str, Any]", value)


def required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def canonical_evidence_id(value: object, field_name: str) -> int:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
        or len(value) > 19
    ):
        raise ValueError(f"{field_name} 必须是 canonical positive integer string")
    parsed = int(value)
    if parsed > 9_223_372_036_854_775_807:
        raise ValueError(f"{field_name} 超出 int64")
    return parsed


COMMAND_SOURCE_PATTERN = re.compile(r"^evidence:([1-9][0-9]*):execution:([1-9][0-9]*):CREATE_DEVICE_COMMAND:([0-9]+)$")
