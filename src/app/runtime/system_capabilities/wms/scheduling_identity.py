"""WMS northbound 调度身份，复用唯一 Provider contract version。"""

from src.app.wms_integration.provider_profile import WMS_PROVIDER_CONTRACT_VERSION
from src.utils.value_normalization import runtime_profile_environment


def wms_runtime_profile_identity(app_env: object) -> str:
    environment = runtime_profile_environment(app_env)
    return f"wms.{WMS_PROVIDER_CONTRACT_VERSION}.{environment}"


__all__ = ["wms_runtime_profile_identity"]
