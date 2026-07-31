"""WMS 入站事件在 callback 与 RuntimeInbox 之间共享的稳定身份常量。"""

WMS_BUSINESS_EVENT_TYPES = frozenset(
    {
        "WMS_GRN_RECEIVED",
        "WMS_PALLET_ARRIVED",
        "WMS_INVENTORY_UPDATED",
        "WMS_PDA_OPERATION_RECORDED",
    }
)

__all__ = ["WMS_BUSINESS_EVENT_TYPES"]
