"""人工拣料的身份与资源需求；业务实现直接引用这些不可变对象。"""

from wes_plugin_sdk import PluginDefinition, WorkLineDeviceRole, WorkLinePositionSlot

# 入口检查 NG 与朝向（扫码后缀 -B）；具体判断由后续 handler 实现。
SCAN1 = WorkLineDeviceRole(role_key="SCAN1", display_name="料箱入口校验扫码")
# 通知 WMS 实际料箱到达工作位。
SCAN2 = WorkLineDeviceRole(role_key="SCAN2", display_name="料箱工作位扫码")
# 检查退箱 NG 状态。
SCAN3 = WorkLineDeviceRole(role_key="SCAN3", display_name="退箱检验扫码")
# 料箱经过后计入退箱 FIFO 队列。
SCAN4 = WorkLineDeviceRole(role_key="SCAN4", display_name="退箱入队扫码")

# 一个位点，容量由工作线配置，包含一个作业位与其余排队位。
FIVE_RACK = WorkLinePositionSlot(
    slot_key="FIVE_RACK",
    display_name="五层货架位",
    position_type="RACK_POSITION",
    location_type="RACK_POSITION",
    allowed_rack_kind="FIVE_LAYER",
)
RETURN_RACK = WorkLinePositionSlot(
    slot_key="RETURN_RACK",
    display_name="退料货架位",
    position_type="RACK_POSITION",
    location_type="RACK_POSITION",
    allowed_rack_kind="RETURN",
)
TRANSFER_RACK = WorkLinePositionSlot(
    slot_key="TRANSFER_RACK",
    display_name="转运货架位",
    position_type="RACK_POSITION",
    location_type="RACK_POSITION",
    allowed_rack_kind="TRANSFER",
)
INLET = WorkLinePositionSlot(
    slot_key="INLET",
    display_name="料箱投料口",
    position_type="STATION",
    location_type="HANDOFF_POSITION",
)
OUTLET = WorkLinePositionSlot(
    slot_key="OUTLET",
    display_name="料箱出料口",
    position_type="STATION",
    location_type="HANDOFF_POSITION",
)

DEFINITION = PluginDefinition(
    plugin_key="manual-picking",
    plugin_version="0.1.0",
    display_name="人工拣料",
    supported_line_types=("MANUAL",),
    device_roles=(SCAN1, SCAN2, SCAN3, SCAN4),
    position_slots=(FIVE_RACK, RETURN_RACK, TRANSFER_RACK, INLET, OUTLET),
)
