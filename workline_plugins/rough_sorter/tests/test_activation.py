"""粗分机 START 配置解析合同。"""

from __future__ import annotations

from copy import deepcopy
from typing import cast

import pytest

from rough_sorter.activation import RoughSorterConfigurationError, parse_activation_configuration


def _configuration() -> dict[str, object]:
    contract: dict[str, object] = {
        "ecs_version": " ecs-1 ",
        "gateway_version": " gateway-1 ",
        "device_model": " model-1 ",
        "firmware_version": " firmware-1 ",
        "status_max_age_ms": 600_000,
        "command_timeout_ms": 30_000,
        "time_source": " plc ",
        "allowed_clock_skew_ms": 1_000,
        "callback_retry_window_ms": 60_000,
        "evidence_retention_days": 30,
    }
    return {
        "device_contracts": {
            "MEASUREMENT_DEVICE": deepcopy(contract),
            "TRANSFER_DEVICE": deepcopy(contract),
            "PLACEMENT_DEVICE": deepcopy(contract),
        },
        "position_bindings": {
            "MEASUREMENT_POSITION": " measurement-1 ",
            "PIPELINE_INLET": " inlet-1 ",
            "PIPELINE_OUTLET": " outlet-1 ",
            "NG_POSITION": " ng-1 ",
        },
    }


def test_parse_activation_configuration_returns_complete_normalized_snapshot() -> None:
    source = _configuration()
    expected = _configuration()
    expected_contracts = cast("dict[str, dict[str, object]]", expected["device_contracts"])
    expected_positions = cast("dict[str, str]", expected["position_bindings"])
    for contract in expected_contracts.values():
        for field in ("ecs_version", "gateway_version", "device_model", "firmware_version", "time_source"):
            value = contract[field]
            assert isinstance(value, str)
            contract[field] = value.strip()
    for role, location_id in expected_positions.items():
        assert isinstance(location_id, str)
        expected_positions[role] = location_id.strip()

    parsed = parse_activation_configuration(source)
    source_contracts = cast("dict[str, dict[str, object]]", source["device_contracts"])
    source_positions = cast("dict[str, str]", source["position_bindings"])
    measurement = source_contracts["MEASUREMENT_DEVICE"]
    measurement["ecs_version"] = "changed-after-parse"
    source_positions["NG_POSITION"] = "changed-after-parse"

    assert parsed.snapshot == expected
    assert parsed.device_contracts["MEASUREMENT_DEVICE"].ecs_version == "ecs-1"
    assert parsed.device_contracts["MEASUREMENT_DEVICE"].status_max_age_ms == 600_000


def test_parse_activation_configuration_enforces_position_location_id_length_boundary() -> None:
    accepted = _configuration()
    accepted_positions = cast("dict[str, str]", accepted["position_bindings"])
    accepted_positions["NG_POSITION"] = "x" * 120

    assert parse_activation_configuration(accepted).position_bindings["NG_POSITION"] == "x" * 120

    rejected = _configuration()
    rejected_positions = cast("dict[str, str]", rejected["position_bindings"])
    rejected_positions["NG_POSITION"] = "x" * 121

    with pytest.raises(RoughSorterConfigurationError):
        _ = parse_activation_configuration(rejected)


def _mutate(configuration: dict[str, object], case: str) -> None:  # noqa: PLR0912 - 闭集反例矩阵
    device_contracts = cast("dict[str, dict[str, object]]", configuration["device_contracts"])
    position_bindings = cast("dict[str, str]", configuration["position_bindings"])
    measurement = device_contracts["MEASUREMENT_DEVICE"]
    match case:
        case "missing_top_level":
            _ = configuration.pop("position_bindings")
        case "extra_top_level":
            configuration["endpoint"] = "http://must-not-be-here"
        case "missing_role":
            _ = device_contracts.pop("TRANSFER_DEVICE")
        case "extra_role":
            device_contracts["LOGICAL_DEVICE"] = deepcopy(measurement)
        case "missing_contract_field":
            _ = measurement.pop("ecs_version")
        case "extra_contract_field":
            measurement["supplier_path"] = "/private"
        case "blank_evidence":
            measurement["firmware_version"] = " "
        case "boolean_integer":
            measurement["status_max_age_ms"] = True
        case "non_positive_integer":
            measurement["command_timeout_ms"] = 0
        case "missing_position":
            _ = position_bindings.pop("NG_POSITION")
        case "extra_position":
            position_bindings["BUFFER_POSITION"] = "buffer-1"
        case "blank_position":
            position_bindings["NG_POSITION"] = " "
        case "duplicate_position":
            position_bindings["NG_POSITION"] = position_bindings["PIPELINE_OUTLET"]
        case _:
            raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "missing_top_level",
        "extra_top_level",
        "missing_role",
        "extra_role",
        "missing_contract_field",
        "extra_contract_field",
        "blank_evidence",
        "boolean_integer",
        "non_positive_integer",
        "missing_position",
        "extra_position",
        "blank_position",
        "duplicate_position",
    ],
)
def test_parse_activation_configuration_rejects_incomplete_or_open_configuration(case: str) -> None:
    configuration = _configuration()
    _mutate(configuration, case)

    with pytest.raises(RoughSorterConfigurationError):
        _ = parse_activation_configuration(configuration)
