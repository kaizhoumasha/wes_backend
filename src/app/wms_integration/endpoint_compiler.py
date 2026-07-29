"""WMS Provider profile 的启动时 endpoint 编译器。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, unquote, urlsplit

from src.app.wms_integration.operation_contract import (
    WmsCompletionMode,
    WmsExecutionLane,
    WmsHttpMethod,
    WmsOperationBudget,
    WmsOperationDefinition,
    WmsOperationMode,
    WmsPaginationConstraint,
)
from src.app.wms_integration.operation_registry import WMS_OPERATIONS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HOST_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _stable_digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_valid_hostname(hostname: str) -> bool:
    if not hostname.isascii() or "%" in hostname or len(hostname) > 253:
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.removesuffix(".").split(".")
        return bool(labels) and all(_HOST_LABEL_PATTERN.fullmatch(label) for label in labels)
    return True


def _compile_origin(server_url: str) -> str:
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in server_url):
        raise ValueError("server_url must be a bare HTTP(S) origin")
    try:
        parsed = urlsplit(server_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("server_url must be a bare HTTP(S) origin") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or not _is_valid_hostname(hostname)
        or parsed.netloc.endswith(":")
        or parsed.username is not None
        or parsed.password is not None
        or "?" in server_url
        or "#" in server_url
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("server_url must be a bare HTTP(S) origin")
    return f"{parsed.scheme.lower()}://{parsed.netloc}"


def _placeholder_names(path_template: str) -> tuple[str, ...]:
    matches = tuple(_PLACEHOLDER_PATTERN.finditer(path_template))
    remainder = _PLACEHOLDER_PATTERN.sub("", path_template)
    if "{" in remainder or "}" in remainder:
        raise ValueError("path contains an illegal placeholder")
    names = tuple(match.group(1) for match in matches)
    if len(names) != len(set(names)):
        raise ValueError("path contains a repeated placeholder")
    for match in matches:
        before = path_template[match.start() - 1] if match.start() else ""
        after = path_template[match.end()] if match.end() < len(path_template) else ""
        if before != "/" or after not in {"", "/"}:
            raise ValueError("placeholder must occupy one complete path segment")
    return names


def _compile_relative_path(path_template: str, expected_placeholders: tuple[str, ...]) -> str:
    if any(
        character.isspace() or character == "\\" or ord(character) < 32 or ord(character) == 127
        for character in path_template
    ):
        raise ValueError("endpoint must use a safe relative path")
    parsed = urlsplit(path_template)
    if parsed.netloc:
        raise ValueError("relative path must not escape the configured origin")
    if parsed.scheme:
        raise ValueError("endpoint must use a relative path")
    if (
        not path_template.startswith("/")
        or "?" in path_template
        or "#" in path_template
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("endpoint must use a relative path without query or fragment")
    if any(unquote(segment) in {".", ".."} for segment in parsed.path.split("/")):
        raise ValueError("relative path must not contain a dot segment")
    placeholders = _placeholder_names(path_template)
    if set(placeholders) != set(expected_placeholders):
        raise ValueError("path placeholder set must exactly match the static operation contract")
    return path_template


def _static_placeholders(operation: WmsOperationDefinition) -> tuple[str, ...]:
    return _placeholder_names(operation.path_template)


@dataclass(frozen=True, slots=True)
class CompiledWmsOperationEndpoint:
    """单项静态 operation 与部署 endpoint 的冻结组合。"""

    identity: str
    request_model: type[BaseModel]
    result_model: type[BaseModel]
    mode: WmsOperationMode
    http_method: WmsHttpMethod
    completion_mode: WmsCompletionMode | None
    execution_lane: WmsExecutionLane
    budget: WmsOperationBudget
    pagination: WmsPaginationConstraint | None
    endpoint_template: str
    status_endpoint: str | None
    placeholder_names: tuple[str, ...]
    endpoint_digest: str

    def render_endpoint(self, request: BaseModel) -> str:
        """只从 operation-specific typed request 读取并编码 path segment。"""

        if not isinstance(request, self.request_model):
            raise TypeError(f"{self.identity} endpoint requires its operation-specific typed request")

        def encode_placeholder(match: re.Match[str]) -> str:
            value = str(getattr(request, match.group(1)))
            decoded = value
            while True:
                if any(segment in {".", ".."} for segment in re.split(r"[/\\]", decoded)):
                    raise ValueError("typed path value must not resolve to a dot segment")
                next_decoded = unquote(decoded)
                if next_decoded == decoded:
                    break
                decoded = next_decoded
            return quote(str(value), safe="")

        rendered = _PLACEHOLDER_PATTERN.sub(encode_placeholder, self.endpoint_template)
        compiled_origin = urlsplit(self.endpoint_template)
        rendered_endpoint = urlsplit(rendered)
        if (
            rendered_endpoint.scheme != compiled_origin.scheme
            or rendered_endpoint.netloc != compiled_origin.netloc
            or rendered_endpoint.query
            or rendered_endpoint.fragment
        ):
            raise ValueError("rendered endpoint must preserve the compiled origin")
        return rendered


@dataclass(frozen=True, slots=True)
class CompiledWmsProviderProfile:
    """启动后共享的冻结 profile 与 35 项 endpoint。"""

    profile: WmsProviderProfileSettings
    profile_revision: str
    profile_digest: str
    operations: Mapping[str, CompiledWmsOperationEndpoint]


def _compile_operation(
    *,
    profile: WmsProviderProfileSettings,
    origin: str,
    operation: WmsOperationDefinition,
    status_endpoint: str,
) -> CompiledWmsOperationEndpoint:
    configured_path = profile.operations[operation.identity]
    path_template = cast(
        "str",
        configured_path.path if operation.mode is WmsOperationMode.QUERY else configured_path.submit_path,
    )
    placeholders = _static_placeholders(operation)
    relative_path = _compile_relative_path(path_template, placeholders)
    endpoint_template = f"{origin}{relative_path}"
    operation_status_endpoint = status_endpoint if operation.supports_status_query else None
    endpoint_digest = _stable_digest(
        {
            "budget": operation.budget.model_dump(mode="json"),
            "completion_mode": operation.completion_mode,
            "endpoint_template": endpoint_template,
            "execution_lane": operation.execution_lane,
            "http_method": operation.http_method,
            "identity": operation.identity,
            "mode": operation.mode,
            "pagination": operation.pagination.model_dump(mode="json") if operation.pagination else None,
            "result_model": f"{operation.result_model.__module__}.{operation.result_model.__qualname__}",
            "status_endpoint": operation_status_endpoint,
        }
    )
    return CompiledWmsOperationEndpoint(
        identity=operation.identity,
        request_model=operation.request_model,
        result_model=operation.result_model,
        mode=operation.mode,
        http_method=operation.http_method,
        completion_mode=operation.completion_mode,
        execution_lane=operation.execution_lane,
        budget=operation.budget,
        pagination=operation.pagination,
        endpoint_template=endpoint_template,
        status_endpoint=operation_status_endpoint,
        placeholder_names=placeholders,
        endpoint_digest=endpoint_digest,
    )


def compile_wms_provider_profile(profile: WmsProviderProfileSettings) -> CompiledWmsProviderProfile:
    """以静态 registry 为唯一语义真源编译完整 Provider profile。"""

    origin = _compile_origin(profile.server_url)
    status_path = _compile_relative_path(profile.effect_status_path, ())
    status_endpoint = f"{origin}{status_path}"
    operations = {
        operation.identity: _compile_operation(
            profile=profile,
            origin=origin,
            operation=operation,
            status_endpoint=status_endpoint,
        )
        for operation in WMS_OPERATIONS
    }
    profile_revision = _stable_digest(profile.model_dump(mode="json"))
    profile_digest = _stable_digest(
        {
            "contract_version": profile.profile.contract_version,
            "operation_endpoint_digests": tuple(
                (identity, endpoint.endpoint_digest) for identity, endpoint in operations.items()
            ),
            "profile_revision": profile_revision,
        }
    )
    return CompiledWmsProviderProfile(
        profile=profile,
        profile_revision=profile_revision,
        profile_digest=profile_digest,
        operations=MappingProxyType(operations),
    )


__all__ = [
    "CompiledWmsOperationEndpoint",
    "CompiledWmsProviderProfile",
    "compile_wms_provider_profile",
]
