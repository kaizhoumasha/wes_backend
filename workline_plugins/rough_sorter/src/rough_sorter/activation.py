"""粗分机业务配置的闭集解析与规范化。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from rough_sorter.handlers._guards import ROLE_CONTRACTS

POSITION_ROLES = (
    "MEASUREMENT_POSITION",
    "PIPELINE_INLET",
    "PIPELINE_OUTLET",
    "NG_POSITION",
)
_STRING_FIELDS = (
    "ecs_version",
    "gateway_version",
    "device_model",
    "firmware_version",
    "time_source",
)
_POSITIVE_INTEGER_FIELDS = (
    "status_max_age_ms",
    "command_timeout_ms",
    "allowed_clock_skew_ms",
    "callback_retry_window_ms",
    "evidence_retention_days",
)
_CONTRACT_FIELDS = frozenset((*_STRING_FIELDS, *_POSITIVE_INTEGER_FIELDS))


class RoughSorterConfigurationError(ValueError):
    """WorkLine.config 的粗分机业务子树不符合获批闭集。"""


@dataclass(frozen=True, slots=True)
class RoughSorterDeviceContract:
    ecs_version: str
    gateway_version: str
    device_model: str
    firmware_version: str
    status_max_age_ms: int
    command_timeout_ms: int
    time_source: str
    allowed_clock_skew_ms: int
    callback_retry_window_ms: int
    evidence_retention_days: int


@dataclass(frozen=True, slots=True)
class RoughSorterActivationConfiguration:
    snapshot: dict[str, object]
    device_contracts: dict[str, RoughSorterDeviceContract]
    position_bindings: dict[str, str]


def _closed_mapping(value: object, expected_keys: set[str] | frozenset[str], *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(cast("Mapping[object, object]", value)) != expected_keys:
        raise RoughSorterConfigurationError(f"{label} 必须且只能包含 {sorted(expected_keys)}")
    return cast("Mapping[str, object]", value)


def _required_string(contract: Mapping[str, object], field: str, *, role: str) -> str:
    value = contract[field]
    if not isinstance(value, str) or not value.strip():
        raise RoughSorterConfigurationError(f"{role}.{field} 必须是非空字符串")
    return value.strip()


def _positive_integer(contract: Mapping[str, object], field: str, *, role: str) -> int:
    value = contract[field]
    if type(value) is not int or value <= 0:
        raise RoughSorterConfigurationError(f"{role}.{field} 必须是严格正整数")
    return value


def _parse_device_contract(value: object, *, role: str) -> RoughSorterDeviceContract:
    contract = _closed_mapping(value, _CONTRACT_FIELDS, label=role)
    return RoughSorterDeviceContract(
        ecs_version=_required_string(contract, "ecs_version", role=role),
        gateway_version=_required_string(contract, "gateway_version", role=role),
        device_model=_required_string(contract, "device_model", role=role),
        firmware_version=_required_string(contract, "firmware_version", role=role),
        status_max_age_ms=_positive_integer(contract, "status_max_age_ms", role=role),
        command_timeout_ms=_positive_integer(contract, "command_timeout_ms", role=role),
        time_source=_required_string(contract, "time_source", role=role),
        allowed_clock_skew_ms=_positive_integer(contract, "allowed_clock_skew_ms", role=role),
        callback_retry_window_ms=_positive_integer(contract, "callback_retry_window_ms", role=role),
        evidence_retention_days=_positive_integer(contract, "evidence_retention_days", role=role),
    )


def _contract_snapshot(contract: RoughSorterDeviceContract) -> dict[str, object]:
    return {field: getattr(contract, field) for field in (*_STRING_FIELDS, *_POSITIVE_INTEGER_FIELDS)}


def parse_activation_configuration(value: object) -> RoughSorterActivationConfiguration:
    """解析并复制完整 canonical `WorkLine.config["rough_sorter"]`。"""

    configuration = _closed_mapping(value, {"device_contracts", "position_bindings"}, label="rough_sorter")
    raw_contracts = _closed_mapping(
        configuration["device_contracts"], set(ROLE_CONTRACTS), label="rough_sorter.device_contracts"
    )
    contracts = {role: _parse_device_contract(raw_contracts[role], role=role) for role in ROLE_CONTRACTS}

    raw_positions = _closed_mapping(
        configuration["position_bindings"], set(POSITION_ROLES), label="rough_sorter.position_bindings"
    )
    positions: dict[str, str] = {}
    for role in POSITION_ROLES:
        location_id = raw_positions[role]
        if not isinstance(location_id, str) or not (normalized_location_id := location_id.strip()):
            raise RoughSorterConfigurationError(f"{role} 必须绑定非空 location_id")
        if len(normalized_location_id) > 120:
            raise RoughSorterConfigurationError(f"{role}.location_id 最长 120 字符")
        positions[role] = normalized_location_id
    if len(set(positions.values())) != len(positions):
        raise RoughSorterConfigurationError("粗分机 position_bindings 的 location_id 不得重复")

    snapshot: dict[str, object] = {
        "device_contracts": {role: _contract_snapshot(contracts[role]) for role in ROLE_CONTRACTS},
        "position_bindings": dict(positions),
    }
    return RoughSorterActivationConfiguration(
        snapshot=snapshot,
        device_contracts=contracts,
        position_bindings=positions,
    )


__all__ = [
    "POSITION_ROLES",
    "RoughSorterActivationConfiguration",
    "RoughSorterConfigurationError",
    "RoughSorterDeviceContract",
    "parse_activation_configuration",
]
