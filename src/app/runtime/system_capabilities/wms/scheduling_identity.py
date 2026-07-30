"""WMS northbound 调度身份，复用唯一 Provider contract version。"""

from src.app.wms_integration.provider_profile import WMS_PROVIDER_CONTRACT_VERSION, build_wms_provider_identity


def wms_runtime_profile_identity() -> str:
    return build_wms_provider_identity("WMS", WMS_PROVIDER_CONTRACT_VERSION)


__all__ = ["wms_runtime_profile_identity"]
