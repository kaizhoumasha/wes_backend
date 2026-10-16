"""自动拣料的不可变声明；设备与位置编码来自工作线绑定。"""

from wes_plugin_sdk import PluginDefinition, WorkLineDeviceRole, WorkLinePositionSlot

SCAN1 = WorkLineDeviceRole(role_key="SCAN1", display_name="料箱入口扫码")
SCAN2 = WorkLineDeviceRole(role_key="SCAN2", display_name="工作位扫码")
SCAN3 = WorkLineDeviceRole(role_key="SCAN3", display_name="流出扫码")
SCAN4 = WorkLineDeviceRole(role_key="SCAN4", display_name="回程扫码")
SOURCE_ARM = WorkLineDeviceRole(role_key="SOURCE_ARM", display_name="来源机械臂")
TARGET_ARM = WorkLineDeviceRole(role_key="TARGET_ARM", display_name="目标机械臂")
FIVE_RACK = WorkLinePositionSlot(
    slot_key="FIVE_RACK",
    display_name="五层来源架",
    position_type="RACK_POSITION",
    location_type="RACK_POSITION",
    allowed_rack_kind="FIVE_LAYER",
)
RETURN_RACK = WorkLinePositionSlot(
    slot_key="RETURN_RACK",
    display_name="直接取料来源架",
    position_type="RACK_POSITION",
    location_type="RACK_POSITION",
    allowed_rack_kind="RETURN",
)
TRANSFER_RACK = WorkLinePositionSlot(
    slot_key="TRANSFER_RACK",
    display_name="目标转运架",
    position_type="RACK_POSITION",
    location_type="RACK_POSITION",
    allowed_rack_kind="TRANSFER",
)
INLET = WorkLinePositionSlot(
    slot_key="INLET", display_name="料箱入口", position_type="STATION", location_type="HANDOFF_POSITION"
)
OUTLET = WorkLinePositionSlot(
    slot_key="OUTLET", display_name="料箱出口", position_type="STATION", location_type="HANDOFF_POSITION"
)
DEFINITION = PluginDefinition(
    plugin_key="automatic-picking",
    plugin_version="0.1.0",
    display_name="自动拣料",
    supported_line_types=("AUTO",),
    device_roles=(SCAN1, SCAN2, SCAN3, SCAN4, SOURCE_ARM, TARGET_ARM),
    position_slots=(FIVE_RACK, RETURN_RACK, TRANSFER_RACK, INLET, OUTLET),
)
