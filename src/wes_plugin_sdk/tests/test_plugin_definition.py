"""声明合同：无需运行实现即可验证资源需求。"""

from dataclasses import FrozenInstanceError, replace

import pytest
import wes_plugin_sdk as sdk


def test_definition_preserves_named_resources_and_rejects_ambiguous_bindings():
    assert hasattr(sdk, "PluginDefinition"), "missing independent plugin declaration"  # nosec B101 - pytest assertion
    role = sdk.WorkLineDeviceRole(role_key="SCAN", display_name="扫码")
    slot = sdk.WorkLinePositionSlot(
        slot_key="INPUT", display_name="入口", position_type="STATION", location_type="INLET"
    )
    definition = sdk.PluginDefinition(
        plugin_key="example",
        plugin_version="1.0",
        display_name="示例",
        supported_line_types=("MANUAL",),
        device_roles=(role,),
        position_slots=(slot,),
    )
    assert definition.device_roles[0] is role  # nosec B101 - pytest assertion
    assert definition.position_slots[0] is slot  # nosec B101 - pytest assertion
    with pytest.raises(FrozenInstanceError):
        role.role_key = "OTHER"
    with pytest.raises(ValueError, match="duplicate device role"):
        replace(definition, device_roles=(role, role))
    with pytest.raises(ValueError, match="duplicate position slot"):
        replace(definition, position_slots=(slot, slot))
    with pytest.raises(ValueError):
        replace(slot, allowed_rack_kind="FIVE_LAYER")
    with pytest.raises(ValueError):
        replace(definition, supported_line_types=("UNKNOWN",))
    with pytest.raises((TypeError, ValueError)):
        replace(definition, device_roles=[role])
    with pytest.raises(ValueError):
        replace(definition, plugin_key=" ")
