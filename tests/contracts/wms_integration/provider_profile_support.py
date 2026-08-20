"""Task 2 Provider profile 合同测试数据构造。"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import yaml

from src.app.wms_integration.operation_contract import WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATIONS

if TYPE_CHECKING:
    from pathlib import Path


def build_provider_profile_payload() -> dict[str, Any]:
    """从唯一静态 registry 派生完整、合法的测试 profile。"""

    operations: dict[str, dict[str, str]] = {}
    for operation in WMS_OPERATIONS:
        field_name = "path" if operation.mode is WmsOperationMode.QUERY else "submit_path"
        operations[operation.identity] = {field_name: f"/api/wms{operation.path_template}"}
    return {
        "profile": {
            "provider_code": "WMS",
            "contract_version": "2026-07-28.full-factory",
        },
        "server_url": "http://factory-wms.example:8080",
        "transport_submit_path": "/api/v1/wes/transport-requests",
        "effect_status_path": "/api/wms/operations/status",
        "network_trust_mode": "isolated_lan",
        "outbound_auth": {"scheme": "NONE"},
        "inbound_auth": {"scheme": "NONE"},
        "operations": operations,
    }


def changed_profile_payload(**changes: Any) -> dict[str, Any]:
    payload = deepcopy(build_provider_profile_payload())
    payload.update(changes)
    return payload


def build_hmac_provider_profile_payload() -> dict[str, Any]:
    payload = build_provider_profile_payload()
    payload["server_url"] = "https://factory-wms.example"
    payload["network_trust_mode"] = "authenticated_network"
    payload["outbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/factory@v1",
    }
    payload["inbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/factory-inbound@v1",
    }
    return payload


def build_compiled_provider_profile(payload: dict[str, Any] | None = None):
    from src.app.wms_integration.endpoint_compiler import compile_wms_provider_profile
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    return compile_wms_provider_profile(
        WmsProviderProfileSettings.model_validate(payload or build_provider_profile_payload())
    )


def build_provider_catalog(payload: dict[str, Any] | None = None):
    from src.app.runtime.system_capabilities.wms.provider_catalog import build_wms_provider_catalog

    return build_wms_provider_catalog(build_compiled_provider_profile(payload))


def write_provider_profile(path: Path, payload: dict[str, Any] | None = None) -> Path:
    path.write_text(
        yaml.safe_dump(payload or build_provider_profile_payload(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


__all__ = [
    "build_compiled_provider_profile",
    "build_hmac_provider_profile_payload",
    "build_provider_catalog",
    "build_provider_profile_payload",
    "changed_profile_payload",
    "write_provider_profile",
]
