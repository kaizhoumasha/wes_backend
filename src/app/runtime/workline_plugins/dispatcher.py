"""Workline Plugin typed snapshot 与确定性静态分派边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.system_capabilities.gateway import GatewayQueryResult
from src.app.runtime.system_capabilities.outcomes import ContractViolation
from src.app.runtime.workline_plugins.contracts import PluginContext, PluginDecision

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

StableString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
StableHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type CapabilityIdentity = tuple[str, str]
type HandlerKey = tuple[str, str, str]


class AttemptSystemCapabilityGateway(Protocol):
    """Dispatcher 可接收的 attempt-scoped Gateway 最小合同。"""

    async def execute(
        self,
        capability_key: str,
        contract_version: str,
        input_data: BaseModel | dict[str, object],
    ) -> GatewayQueryResult: ...


class PluginHandler(Protocol):
    """所有 route handler 的固定调用参数；禁止 kwargs service locator。"""

    async def __call__(
        self,
        logical_input: BaseModel,
        *,
        state: BaseModel,
        config: BaseModel,
        facts: BaseModel,
        context: PluginContext[BaseModel],
        gateway: AttemptSystemCapabilityGateway,
    ) -> PluginDecision[BaseModel]: ...


@dataclass(frozen=True, slots=True)
class HandlerRegistration:
    """静态 route handler 与其 immutable facts schema。"""

    handler: PluginHandler
    facts_model: type[BaseModel]

    def __post_init__(self) -> None:
        if not callable(self.handler):
            raise TypeError("handler must be callable")
        if not isinstance(self.facts_model, type) or not issubclass(self.facts_model, BaseModel):
            raise TypeError("facts_model must be a Pydantic model class")


class PinnedPluginSnapshot(BaseModel):
    """claim 阶段固定、dispatcher 必须逐字段重校验的 binding 快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_key: StableString
    contract_version: StableString
    binding_identity: StableString
    binding_id: int = Field(gt=0)
    binding_version: int = Field(gt=0)
    config_hash: StableHash
    index_digest: StableHash
    profile_identity: StableString

    @model_validator(mode="after")
    def validate_binding_identity(self) -> PinnedPluginSnapshot:
        expected = f"binding:{self.binding_id}:{self.binding_version}"
        if self.binding_identity != expected:
            raise ValueError("binding_identity does not match binding id/version")
        return self


class PluginDispatchRequest(BaseModel):
    """Stage 2 唯一 dispatcher 输入；仅携带 immutable typed snapshot 原料。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_key: StableString
    contract_version: StableString
    logical_route: StableString
    raw_config: dict[str, object]
    raw_state: dict[str, object]
    context_state: dict[str, object]
    raw_input: dict[str, object]
    raw_facts: dict[str, object]
    snapshot: PinnedPluginSnapshot


class DeclaredCapabilityGateway:
    """按 Definition allowlist 包装 attempt Gateway，未声明调用不触达底层。"""

    def __init__(
        self,
        gateway: AttemptSystemCapabilityGateway,
        *,
        allowed_capabilities: frozenset[CapabilityIdentity],
    ) -> None:
        self._gateway = gateway
        self._allowed_capabilities = allowed_capabilities
        self.violation: ContractViolation | None = None

    async def execute(
        self,
        capability_key: str,
        contract_version: str,
        input_data: BaseModel | dict[str, object],
    ) -> GatewayQueryResult:
        if self.violation is not None:
            return GatewayQueryResult(outcome=self.violation, evidence=None)
        identity = (capability_key, contract_version)
        if identity not in self._allowed_capabilities:
            self.violation = ContractViolation(
                error_code="CAPABILITY_NOT_DECLARED",
                message="handler requested a capability not declared by its Definition",
            )
            return GatewayQueryResult(outcome=self.violation, evidence=None)
        return await self._gateway.execute(capability_key, contract_version, input_data)


class WorklinePluginDispatcher:
    """从 generated index 校验 snapshot 并调用唯一静态 handler；从不接收 DB。"""

    def __init__(
        self,
        *,
        plugin_index: Mapping[tuple[str, str], WorklinePluginDefinition] | None = None,
        plugin_index_digest: str | None = None,
        handler_registry: Mapping[HandlerKey, tuple[HandlerRegistration, ...]] | None = None,
    ) -> None:
        if plugin_index is None or plugin_index_digest is None:
            from src.app.runtime.workline_plugins.generated_index import (
                WORKLINE_PLUGIN_INDEX,
                WORKLINE_PLUGIN_INDEX_DIGEST,
            )

            plugin_index = WORKLINE_PLUGIN_INDEX if plugin_index is None else plugin_index
            plugin_index_digest = WORKLINE_PLUGIN_INDEX_DIGEST if plugin_index_digest is None else plugin_index_digest
        if handler_registry is None:
            from src.app.runtime.workline_plugins.handler_registry import build_generated_handler_registry

            handler_registry = build_generated_handler_registry(plugin_index)
        self._plugin_index = dict(plugin_index)
        self._plugin_index_digest = plugin_index_digest
        self._handler_registry = {key: tuple(candidates) for key, candidates in handler_registry.items()}

    async def dispatch(  # noqa: PLR0911 - 每个 fail-closed snapshot 边界保留独立稳定错误码。
        self,
        *,
        request: PluginDispatchRequest,
        gateway: AttemptSystemCapabilityGateway,
    ) -> PluginDecision[BaseModel] | ContractViolation:
        identity = (request.plugin_key, request.contract_version)
        definition = self._plugin_index.get(identity)
        if definition is None:
            return _violation("PLUGIN_IDENTITY_UNKNOWN", "plugin identity is not present in generated index")
        if request.logical_route not in definition.routes:
            return _violation("PLUGIN_ROUTE_UNKNOWN", "logical route is not declared by plugin")
        candidates = self._handler_registry.get((*identity, request.logical_route), ())
        if not candidates:
            return _violation("PLUGIN_HANDLER_MISSING", "logical route has no static handler")
        if len(candidates) > 1:
            return _violation("PLUGIN_HANDLER_AMBIGUOUS", "logical route has multiple static handlers")
        registration = candidates[0]
        snapshot_violation = self._validate_snapshot(request=request, definition=definition)
        if snapshot_violation is not None:
            return snapshot_violation
        try:
            config = definition.config_model.model_validate(request.raw_config)
            if sha256_digest(config.model_dump(mode="json")) != request.snapshot.config_hash:
                return _violation("PLUGIN_CONFIG_HASH_MISMATCH", "typed config hash differs from pinned binding")
            if config.provider_profile != request.snapshot.profile_identity:  # type: ignore[attr-defined]
                return _violation("PLUGIN_PROFILE_MISMATCH", "config provider profile differs from pinned profile")
            state = definition.state_model.model_validate(request.raw_state)
            context_state = definition.state_model.model_validate(request.context_state)
            if state != context_state:
                return _violation("STATE_CONTEXT_MISMATCH", "context state differs from parsed plugin state")
            facts = registration.facts_model.model_validate(request.raw_facts)
            facts_violation = _validate_facts_snapshot(facts, request.snapshot)
            if facts_violation is not None:
                return facts_violation
            logical_input = definition.parsers[request.logical_route](request.raw_input)
            if not isinstance(logical_input, BaseModel):
                raise TypeError("route parser must return a Pydantic model")
            context = PluginContext(state=state)
        except (AttributeError, KeyError, TypeError, ValidationError, ValueError) as exc:
            return _violation("PLUGIN_CONTRACT_INVALID", str(exc))

        declared_gateway = DeclaredCapabilityGateway(
            gateway,
            allowed_capabilities=frozenset(definition.allowed_capabilities),
        )
        decision = await registration.handler(
            logical_input,
            state=state,
            config=config,
            facts=facts,
            context=context,
            gateway=declared_gateway,
        )
        if declared_gateway.violation is not None:
            return declared_gateway.violation
        try:
            if not isinstance(decision, PluginDecision):
                raise TypeError("handler must return PluginDecision")
            definition.state_model.model_validate(decision.next_state)
        except (AttributeError, TypeError, ValidationError, ValueError) as exc:
            return _violation("PLUGIN_CONTRACT_INVALID", str(exc))
        return decision

    def _validate_snapshot(
        self,
        *,
        request: PluginDispatchRequest,
        definition: WorklinePluginDefinition,
    ) -> ContractViolation | None:
        snapshot = request.snapshot
        if (snapshot.plugin_key, snapshot.contract_version) != (definition.plugin_key, definition.contract_version):
            return _violation("PLUGIN_BINDING_IDENTITY_MISMATCH", "binding plugin identity differs from Definition")
        if snapshot.index_digest != self._plugin_index_digest:
            return _violation("PLUGIN_INDEX_DIGEST_MISMATCH", "binding index digest differs from generated index")
        return None


def _validate_facts_snapshot(facts: BaseModel, snapshot: PinnedPluginSnapshot) -> ContractViolation | None:
    facts_binding = getattr(facts, "binding_snapshot", None)
    if facts_binding is None:
        return _violation("PLUGIN_FACT_SNAPSHOT_MISSING", "facts are missing pinned binding snapshot")
    expected = (
        snapshot.binding_id,
        snapshot.binding_version,
        snapshot.profile_identity,
        snapshot.config_hash,
        snapshot.index_digest,
    )
    actual = (
        getattr(facts_binding, "binding_id", None),
        getattr(facts_binding, "binding_version", None),
        getattr(facts_binding, "profile_identity", None),
        getattr(facts_binding, "plugin_config_hash", None),
        getattr(facts_binding, "generated_index_digest", None),
    )
    if actual[:2] != expected[:2]:
        return _violation("PLUGIN_BINDING_IDENTITY_MISMATCH", "facts binding id/version differs from pinned binding")
    if actual != expected:
        return _violation("PLUGIN_FACT_SNAPSHOT_MISMATCH", "facts binding snapshot differs from pinned binding")
    return None


def _violation(error_code: str, message: str) -> ContractViolation:
    return ContractViolation(error_code=error_code, message=message)


__all__ = [
    "AttemptSystemCapabilityGateway",
    "DeclaredCapabilityGateway",
    "HandlerRegistration",
    "PinnedPluginSnapshot",
    "PluginDispatchRequest",
    "PluginHandler",
    "WorklinePluginDispatcher",
]
