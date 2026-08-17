"""粗分机 deployment 的严格 wire/value 解析与稳定身份 helper。"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any, cast

from wes_plugin_sdk import (
    DevicePosition,
    DeviceResultReadyFact,
    EvidenceReadyFact,
    FactReference,
    RackFace,
    TransportRackPosition,
    WmsResultReadyFact,
)

from src.app.execution.models import InboundEvidenceKind
from src.app.execution.services.fact_builder import FactBuilder
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from deployment._rough_sorter_types import RoughSorterTypes
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
        return WmsResultReadyFact(
            **common,
            operation_id=required_string(evidence.operation_id, "evidence.operation_id"),
        )
    if evidence.kind == InboundEvidenceKind.WMS_EVENT:
        return FactBuilder().build(evidence, execution)
    raise ValueError("当前 WMS request resolver 不支持该 evidence kind")


def position_binding(snapshot: Any, role: str) -> Any:
    matches = tuple(item for item in snapshot.epoch.position_bindings if item.position_role == role)
    if len(matches) != 1:
        raise ValueError(f"Epoch position role {role} missing or ambiguous")
    return matches[0]


def required_position(value: DevicePosition | None, field_name: str) -> DevicePosition:
    if type(value) is not DevicePosition:
        raise ValueError(f"{field_name} 必须是 DevicePosition")
    return value


def device_binding(snapshot: Any, role: str) -> Any:
    matches = tuple(item for item in snapshot.epoch.device_bindings if item.device_role == role)
    if len(matches) != 1:
        raise ValueError(f"Epoch device role {role} missing or ambiguous")
    return matches[0]


def bound_position(snapshot: Any, role: str, material_trace_id: str) -> DevicePosition:
    binding = position_binding(snapshot, role)
    return DevicePosition(binding.location_id, binding.location_type, material_trace_id)


def wire_position(value: object, material_trace_id: str, expected_type: str) -> DevicePosition:
    if not isinstance(value, dict):
        raise TypeError("WMS position 必须是对象")
    wire_type = value.get("type")
    if expected_type == "RACK_CELL":
        if wire_type != "ONE_LAYER_BIN_CELL" or set(value) != {
            "type",
            "rack_id",
            "rack_slot_code",
            "bin_id",
            "bin_cell_id",
        }:
            raise ValueError("WMS target_position 必须是严格 ONE_LAYER_BIN_CELL")
        return DevicePosition(
            location_id=required_string(value.get("bin_cell_id"), "bin_cell_id"),
            location_type="RACK_CELL",
            material_trace_id=material_trace_id,
            rack_id=required_string(value.get("rack_id"), "rack_id"),
            rack_slot_code=required_string(value.get("rack_slot_code"), "rack_slot_code"),
            bin_id=required_string(value.get("bin_id"), "bin_id"),
            bin_cell_id=required_string(value.get("bin_cell_id"), "bin_cell_id"),
        )
    expected_wire = "NG_POSITION" if expected_type == "NG_POSITION" else "HANDOFF_POSITION"
    if wire_type != expected_wire or set(value) != {"type", "location_code"}:
        raise ValueError(f"WMS position 必须是严格 {expected_wire}")
    return DevicePosition(
        location_id=required_string(value.get("location_code"), "location_code"),
        location_type=expected_type,
        material_trace_id=material_trace_id,
    )


def wms_position(position: DevicePosition) -> dict[str, str]:
    if position.location_type == "RACK_CELL":
        return {
            "type": "ONE_LAYER_BIN_CELL",
            "rack_id": required_string(position.rack_id, "rack_id"),
            "rack_slot_code": required_string(position.rack_slot_code, "rack_slot_code"),
            "bin_id": required_string(position.bin_id, "bin_id"),
            "bin_cell_id": required_string(position.bin_cell_id, "bin_cell_id"),
        }
    return {
        "type": "NG_POSITION" if position.location_type == "NG_POSITION" else "HANDOFF_POSITION",
        "location_code": position.location_id,
    }


def rack_move_plan(value: object, types: RoughSorterTypes) -> Any:
    data = strict_object(value, {"rack_id", "source", "target", "target_face"}, "rack move plan")
    source = strict_object(data["source"], {"type", "location_code"}, "rack move source")
    target = strict_object(data["target"], {"type", "location_code"}, "rack move target")
    if source["type"] != "RACK_POSITION" or target["type"] != "RACK_POSITION":
        raise ValueError("rack move source/target 必须是 RACK_POSITION")
    return types.RackMoveLegPlan(
        rack_id=required_string(data["rack_id"], "rack_id"),
        source=TransportRackPosition(required_string(source["location_code"], "source.location_code")),
        target=TransportRackPosition(required_string(target["location_code"], "target.location_code")),
        target_face=RackFace(required_string(data["target_face"], "target_face")),
    )


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
        {"location_id", "location_type", "material_trace_id", "rack_id", "rack_slot_code", "bin_id", "bin_cell_id"},
        "DeviceCommand position",
    )
    if data["material_trace_id"] != material_trace_id:
        raise ValueError("DeviceCommand position trace 不匹配")
    for field_name in ("rack_id", "rack_slot_code", "bin_id", "bin_cell_id"):
        item = data[field_name]
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"DeviceCommand position {field_name} 非法")
    return DevicePosition(
        location_id=required_string(data["location_id"], "position.location_id"),
        location_type=required_string(data["location_type"], "position.location_type"),
        material_trace_id=material_trace_id,
        rack_id=cast("str | None", data["rack_id"]),
        rack_slot_code=cast("str | None", data["rack_slot_code"]),
        bin_id=cast("str | None", data["bin_id"]),
        bin_cell_id=cast("str | None", data["bin_cell_id"]),
    )


def device_step(
    task_type: str,
    source: DevicePosition,
    target: DevicePosition,
    types: RoughSorterTypes,
) -> tuple[Any, str]:
    identity = (task_type, source.location_type, target.location_type)
    try:
        return {
            ("PICK_AND_PUT", "MEASUREMENT_POSITION", "PIPELINE_INLET"): (
                types.DeviceStep.MEASUREMENT_TO_INLET,
                "MEASUREMENT_DEVICE",
            ),
            ("MOVE_FORWARD", "PIPELINE_INLET", "PIPELINE_OUTLET"): (
                types.DeviceStep.TRANSFER_TO_OUTLET,
                "TRANSFER_DEVICE",
            ),
            ("PICK_AND_PUT", "PIPELINE_OUTLET", "RACK_CELL"): (types.DeviceStep.PLACEMENT_TO_CELL, "PLACEMENT_DEVICE"),
            ("PICK_AND_PUT", "MEASUREMENT_POSITION", "NG_POSITION"): (
                types.DeviceStep.MEASUREMENT_TO_NG,
                "MEASUREMENT_DEVICE",
            ),
            ("PICK_AND_PUT", "PIPELINE_OUTLET", "NG_POSITION"): (types.DeviceStep.PLACEMENT_TO_NG, "PLACEMENT_DEVICE"),
        }[identity]
    except KeyError as exc:
        raise ValueError("DeviceCommand task/source/target 不属于 rough sorter 拓扑") from exc


def validate_source_evidence_for_step(evidence: InboundEvidence, step: Any, types: RoughSorterTypes) -> None:
    expected_operations = {
        types.DeviceStep.MEASUREMENT_TO_INLET: {"inbound.material.admission_decide@v1"},
        types.DeviceStep.TRANSFER_TO_OUTLET: set(),
        types.DeviceStep.PLACEMENT_TO_CELL: {"inbound.material.target_decide@v1"},
        types.DeviceStep.MEASUREMENT_TO_NG: {"inbound.material.admission_decide@v1"},
        types.DeviceStep.PLACEMENT_TO_NG: {"inbound.material.target_decide@v1"},
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


def positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} 必须是正整数")
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
