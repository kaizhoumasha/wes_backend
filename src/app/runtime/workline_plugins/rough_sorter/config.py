"""粗分机插件类型化 binding 配置。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
LocationCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
AdmissionCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ProviderProfile = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class RoughSorterDeviceRoles(BaseModel):
    """现场设备角色名；具体设备实例仍由 binding/runtime 解析。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_arm: RoleName
    conveyor: RoleName
    output_arm: RoleName


class RoughSorterConfig(BaseModel):
    """激活时校验的粗分机 typed config。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_roles: RoughSorterDeviceRoles
    pipeline_input_location: LocationCode
    pipeline_output_location: LocationCode
    ng_location: LocationCode
    warehouse_code: AdmissionCode
    owner_code: AdmissionCode
    provider_profile: ProviderProfile


__all__ = ["RoughSorterConfig", "RoughSorterDeviceRoles"]
