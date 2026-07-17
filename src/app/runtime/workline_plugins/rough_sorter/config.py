"""粗分机插件类型化 binding 配置。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class RoughSorterDeviceRoles(BaseModel):
    """现场设备角色名；具体设备实例仍由 binding/runtime 解析。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_arm: NonEmptyString
    conveyor: NonEmptyString
    output_arm: NonEmptyString


class RoughSorterConfig(BaseModel):
    """激活时校验的粗分机 typed config。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_roles: RoughSorterDeviceRoles
    pipeline_input_location: NonEmptyString
    pipeline_output_location: NonEmptyString
    ng_location: NonEmptyString
    warehouse_code: NonEmptyString
    owner_code: NonEmptyString
    provider_profile: NonEmptyString


__all__ = ["RoughSorterConfig", "RoughSorterDeviceRoles"]
