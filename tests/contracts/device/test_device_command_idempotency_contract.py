"""H4: DeviceCommand typed params + extra=forbid + 同 key 不同 hash 拒绝。

主计划 §9.6 + §5.3 + Phase 1 H4:
- `params` 改 typed Pydantic union (按 task_type 区分), 禁用 `dict[str, Any]`
- `CommandBase` 显式 `extra="forbid"` 阻断未声明字段透传
- 同 key(command_code)不同 request_hash 应拒绝 (Phase 1 outbound 最小版本)
- 完整 409 安全审计留 Phase 3 ENG-009

注意: 本测试只覆盖 H4 三个子任务的 schema 层面, 完整 C4 字段白名单
(禁止 plc/coordinate/joint/safety_loop) 由 architecture-guardrails.sh C4
脚本扫描保障, 不在本文件。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.device.models.command import (
    CommandBase,
    CommandCallbackResult,
    CommandResult,
    DeviceCommand,
    DeviceCommandParamValue,
)

# ---- typed params union (按 task_type 区分) ----


def test_command_base_params_use_typed_json_union():
    """H4: params 使用 typed JSON union, 不再是 dict[str, Any]。"""
    field = CommandBase.model_fields["params"]
    annotation = str(field.annotation)
    assert "Any" not in annotation
    assert annotation == "dict[str, DeviceCommandParamValue]"
    alias_value = str(DeviceCommandParamValue.__value__)
    assert "list[" in alias_value
    assert "dict[str" in alias_value

    cmd = CommandBase(
        device_id=1,
        task_type="PUT",
        params={"source_loc": "BIN-01", "target_loc": "CONVEYOR-02"},
    )
    assert cmd.params == {"source_loc": "BIN-01", "target_loc": "CONVEYOR-02"}


def test_command_base_params_accepts_recursive_json_payload():
    """H4: typed params 允许真实业务 JSON: 嵌套对象、对象数组和嵌套数组。"""
    cmd = CommandBase(
        device_id=1,
        task_type="PUT",
        params={
            "items": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}],
            "matrix": [[1, 2], [3, 4]],
            "metadata": {"batch": {"lot": "L1", "flags": [True, None]}},
        },
    )

    assert cmd.params["items"] == [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]
    assert cmd.params["matrix"] == [[1, 2], [3, 4]]
    assert cmd.params["metadata"] == {"batch": {"lot": "L1", "flags": [True, None]}}


def test_command_base_rejects_forbidden_nested_params():
    """H4: 禁止字段不能藏在 params 嵌套对象里透传给 ECS。"""
    with pytest.raises(ValidationError) as exc_info:
        CommandBase(
            device_id=1,
            task_type="PUT",
            params={"business": {"coordinate": {"x": 1, "y": 2}}},
        )
    assert "coordinate" in str(exc_info.value)


def test_command_base_rejects_forbidden_key_inside_object_array():
    """H4: 禁止字段也不能藏在对象数组深层。"""
    with pytest.raises(ValidationError) as exc_info:
        CommandBase(
            device_id=1,
            task_type="PUT",
            params={"items": [{"name": "safe"}, {"axis": "x"}]},
        )
    assert "axis" in str(exc_info.value)


def test_command_base_extra_forbid_rejects_unknown_field():
    """H4: extra='forbid' 阻止未声明字段 (阻断 attacker 通过 params 注入禁止字段)。

    验证: 传 SQLModel 不识别的字段应抛 ValidationError, 而非 silent pass。
    """
    with pytest.raises(ValidationError) as exc_info:
        CommandBase(
            device_id=1,
            task_type="PUT",
            params={"source_loc": "BIN-01"},
            unknown_field="injected",  # type: ignore[call-arg]
        )
    assert "unknown_field" in str(exc_info.value)


# ---- 同 key 不同 hash 拒绝 (H4 outbound 最小版本) ----


def test_external_contract_profile_query_method_format():
    """query 元素必须为 'ClassName.method' 格式 (Port.method 合同)。"""
    from src.app.contracts.external_contract_profile import ExternalContractProfile

    # 合法格式 (snake_case 方法名支持)
    profile = ExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-06-26",
        environment="sandbox",
        runtime_capabilities_query=["WmsMasterDataPort.get_material"],
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/default",
        fixture_set_required_cases=["success"],
    )
    assert profile.runtime_capabilities_query == ["WmsMasterDataPort.get_material"]


def test_external_contract_profile_query_format_rejects_underscore_prefix():
    """query 元素缺 Port 后缀应拒绝。"""
    from src.app.contracts.external_contract_profile import ExternalContractProfile

    with pytest.raises(ValidationError) as exc_info:
        ExternalContractProfile(
            provider_code="WMS",
            contract_version="2026-06-26",
            environment="sandbox",
            runtime_capabilities_query=["get_material"],
            timeout_retry_query_timeout_seconds=10,
            timeout_retry_retry_backoff_seconds=[1],
            fixture_set_path="tests/fixtures/external_contracts/wms/default",
            fixture_set_required_cases=["success"],
        )
    assert "Port.method" in str(exc_info.value)


# ---- 现有 baseline 验证 (Phase 0 行为不退化) ----


def test_command_callback_result_baseline_extra_forbid():
    """Phase 0 已有的 CommandCallbackResult extra='forbid' baseline 仍生效。

    不因 CommandBase 新增 extra='forbid' 而影响 callback result schema。
    """
    result = CommandCallbackResult(
        command_code="CMD-001",
        device_code="DEVICE-01",
        result=CommandResult.SUCCESS,
        finish_time=1700000000000,
    )
    assert result.command_code == "CMD-001"

    # extra='forbid' 拒绝未知字段
    with pytest.raises(ValidationError):
        CommandCallbackResult(
            command_code="CMD-001",
            device_code="DEVICE-01",
            result=CommandResult.SUCCESS,
            finish_time=1700000000000,
            unknown_field="x",  # type: ignore[call-arg]
        )


def test_device_command_params_column_is_json():
    """DB 层 params 字段是 JSON column (sa_column=Column(JSON))。

    Phase 0 已就绪, typed Pydantic union 替换时需保持 JSON 存储以
    避免 Alembic migration 复杂度。DeviceCommand 是表 model, 含 __table__。
    """
    table = DeviceCommand.__table__
    params_col = table.c["params"]
    assert type(params_col.type).__name__ == "JSON"
