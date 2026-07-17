"""DeviceCommand write capability。"""

from .contracts import DeviceCommandWriteInput, DeviceCommandWriteOutput
from .definition import DEFINITION
from .handler import DeviceCommandWriteHandler

__all__ = ["DEFINITION", "DeviceCommandWriteHandler", "DeviceCommandWriteInput", "DeviceCommandWriteOutput"]
