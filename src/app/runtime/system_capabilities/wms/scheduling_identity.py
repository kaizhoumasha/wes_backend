"""WMS northbound 调度身份常量，避免 gateway 与 Provider catalog 循环依赖。"""

from src.utils.value_normalization import runtime_profile_environment

WMS_MATERIAL_FLOW_CONTRACT_VERSION = "2026-07-06.material-flow"


def wms_runtime_profile_identity(app_env: object) -> str:
    environment = runtime_profile_environment(app_env)
    return f"wms.{WMS_MATERIAL_FLOW_CONTRACT_VERSION}.{environment}"


__all__ = [
    "WMS_MATERIAL_FLOW_CONTRACT_VERSION",
    "wms_runtime_profile_identity",
]
