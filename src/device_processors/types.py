"""Shared types for legacy device processors."""

from enum import Enum


class DeviceProcessorEventType(str, Enum):
    """Generic event enum for the legacy device processor pipeline."""

    ESTOP_PRESSED = "ESTOP_PRESSED"
    DEVICE_ONLINE = "DEVICE_ONLINE"
    DEVICE_OFFLINE = "DEVICE_OFFLINE"
    DEVICE_ERROR = "DEVICE_ERROR"
    MATERIAL_ARRIVED = "MATERIAL_ARRIVED"
    SCAN_COMPLETED = "SCAN_COMPLETED"
    INSPECTION_COMPLETED = "INSPECTION_COMPLETED"
    PICK_COMPLETED = "PICK_COMPLETED"
    PUT_COMPLETED = "PUT_COMPLETED"
    PROCESS_COMPLETED = "PROCESS_COMPLETED"


__all__ = ["DeviceProcessorEventType"]
