# 旧 runtime 镜像实现:src.workline_runtime.runtime_intent 的平级副本
# 旧 runtime 入口删除后,本模块承载正式实现。

"""Plugin-facing Runtime intent contracts.

Plugins describe what should happen next. Runtime owns whether the intent is
legal, how target devices are resolved, and how state is persisted.
"""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from re import fullmatch
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.app.runtime.extension_identity import sha256_digest, validate_key_version
from src.utils.timezone import timezone

_HANDLING_TRANSPORT_FIELDS = {
    "dispatch_key",
    "external_target_code",
    "outbox_id",
    "payload_json",
    "http_headers",
    "url",
    "auth",
    "retry",
}
_SYSTEM_CAPABILITY_OPERATION_KEY_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}"


def validate_system_capability_operation_key(value: object) -> str:
    """限制为可直接进入 ledger/audit 的稳定 ASCII operation identity。"""

    if not isinstance(value, str) or fullmatch(_SYSTEM_CAPABILITY_OPERATION_KEY_PATTERN, value) is None:
        raise ValueError("SYSTEM_CAPABILITY operation_key must use auditable characters and max length 160")
    return value


class RuntimeIntentKind(str, Enum):
    SYSTEM_CAPABILITY = "SYSTEM_CAPABILITY"
    COMMAND = "COMMAND"
    EXTERNAL_REQUEST = "EXTERNAL_REQUEST"
    RACK_OPERATION_REQUEST = "RACK_OPERATION_REQUEST"
    BIN_OPERATION_REQUEST = "BIN_OPERATION_REQUEST"
    RACK_BIN_EXCHANGE_REQUEST = "RACK_BIN_EXCHANGE_REQUEST"
    DEVICE_EVENT = "DEVICE_EVENT"
    RESOURCE_FACT = "RESOURCE_FACT"
    RESOURCE_RESERVATION = "RESOURCE_RESERVATION"
    RESOURCE_WAIT = "RESOURCE_WAIT"
    ROUTE = "ROUTE"
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    BLOCK = "BLOCK"
    MARK_NG = "MARK_NG"
    CONTINUE_NEXT = "CONTINUE_NEXT"
    UPDATE_CONTEXT = "UPDATE_CONTEXT"
    CREATE_MATERIAL_UNIT = "CREATE_MATERIAL_UNIT"
    UPDATE_MATERIAL_UNIT_STATUS = "UPDATE_MATERIAL_UNIT_STATUS"


class DestinationKind(str, Enum):
    CURRENT = "CURRENT"
    NEXT = "NEXT"
    ROLE = "ROLE"
    DEVICE = "DEVICE"
    PASS_ROUTE = "PASS_ROUTE"  # noqa: S105  # nosec B105
    NG_ROUTE = "NG_ROUTE"
    EXIT = "EXIT"


class BlockScope(str, Enum):
    WORKLINE = "WORKLINE"
    DEVICE = "DEVICE"
    MATERIAL = "MATERIAL"
    COMMAND = "COMMAND"


class Destination(BaseModel):
    kind: DestinationKind
    value: str | int | None = None

    @classmethod
    def current(cls) -> Destination:
        return cls(kind=DestinationKind.CURRENT)

    @classmethod
    def next(cls) -> Destination:
        return cls(kind=DestinationKind.NEXT)

    @classmethod
    def role(cls, role: str) -> Destination:
        return cls(kind=DestinationKind.ROLE, value=role)

    @classmethod
    def device(cls, device_id: int) -> Destination:
        return cls(kind=DestinationKind.DEVICE, value=device_id)

    @classmethod
    def ng_route(cls) -> Destination:
        return cls(kind=DestinationKind.NG_ROUTE)

    @classmethod
    def pass_route(cls) -> Destination:
        return cls(kind=DestinationKind.PASS_ROUTE)

    @classmethod
    def exit(cls) -> Destination:
        return cls(kind=DestinationKind.EXIT)


class RuntimeIntent(BaseModel):
    kind: RuntimeIntentKind
    device_role: str | None = None
    target_device_id: int | None = None
    action: str | None = None
    dispatch_key: str | None = None
    target_code: str | None = None
    source_system: str | None = None
    idempotency_key: str | None = None
    rack_code: str | None = None
    position_code: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    destination: Destination | None = None
    timeout_seconds: int | None = None
    result_policy: Literal["COMMAND_RESULT"] | None = None
    block_scope: BlockScope | None = None
    reason_code: str | None = None
    message: str | None = None
    suggested_action: str | None = None
    context_patch: dict[str, Any] = Field(default_factory=dict)
    # SYSTEM_CAPABILITY 是插件可创建的唯一通用副作用包络；这些字段在创建时固定，
    # 后续执行不得重新选择 capability、binding、provider 或授权策略。
    capability_key: str | None = None
    contract_version: str | None = None
    operation_key: str | None = Field(
        default=None,
        max_length=160,
        pattern=f"^{_SYSTEM_CAPABILITY_OPERATION_KEY_PATTERN}$",
    )
    payload_hash: str | None = Field(default=None, min_length=64, max_length=64)
    precondition_json: dict[str, Any] = Field(default_factory=dict)
    fact_version: str | int | None = None
    creator_authority: str | None = None
    authorization_policy: str | None = None
    binding_snapshot: dict[str, Any] = Field(default_factory=dict)
    provider_snapshot: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def system_capability(
        cls,
        *,
        capability_key: str,
        contract_version: str,
        operation_key: str,
        payload: BaseModel | dict[str, Any],
        precondition: BaseModel | dict[str, Any],
        fact_version: str | int,
        timeout_seconds: int,
        creator_authority: str,
        authorization_policy: str,
        binding_snapshot: BaseModel | dict[str, Any],
        provider_snapshot: BaseModel | dict[str, Any],
    ) -> RuntimeIntent:
        """创建不可变语义的 typed EFFECT intent。"""

        def dump(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
            return value.model_dump(mode="json") if isinstance(value, BaseModel) else deepcopy(value)

        payload_json = dump(payload)
        return cls(
            kind=RuntimeIntentKind.SYSTEM_CAPABILITY,
            capability_key=capability_key,
            contract_version=contract_version,
            operation_key=operation_key,
            payload_json=payload_json,
            payload_hash=sha256_digest(payload_json),
            precondition_json=dump(precondition),
            fact_version=fact_version,
            timeout_seconds=timeout_seconds,
            creator_authority=creator_authority,
            authorization_policy=authorization_policy,
            binding_snapshot=dump(binding_snapshot),
            provider_snapshot=dump(provider_snapshot),
        )

    @classmethod
    def command(
        cls,
        *,
        device_role: str | None = None,
        target_device_id: int | None = None,
        action: str,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
        timeout_seconds: int | None = None,
        result_policy: Literal["COMMAND_RESULT"],
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.COMMAND,
            device_role=device_role,
            target_device_id=target_device_id,
            action=action,
            payload_json=deepcopy(payload) if payload is not None else {},
            destination=destination,
            timeout_seconds=timeout_seconds,
            result_policy=result_policy,
        )

    @classmethod
    def external_request(
        cls,
        *,
        dispatch_key: str,
        target_code: str,
        payload: dict[str, Any] | None = None,
        timeout_seconds: int | None,
        source_system: str | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.EXTERNAL_REQUEST,
            dispatch_key=dispatch_key,
            target_code=target_code,
            source_system=source_system,
            payload_json=deepcopy(payload) if payload is not None else {},
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def rack_operation_request(
        cls,
        *,
        operation_type: str,
        operation_key: str,
        target_code: str,
        payload: dict[str, Any],
        timeout_seconds: int | None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.RACK_OPERATION_REQUEST,
            action=operation_type,
            idempotency_key=operation_key,
            target_code=target_code,
            payload_json=deepcopy(payload),
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def bin_operation_request(
        cls,
        *,
        operation_type: str,
        operation_key: str,
        moves: list[dict[str, Any]],
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RuntimeIntent:
        payload_json: dict[str, Any] = {
            "moves": deepcopy(moves),
            "carrier_type": carrier_type,
        }
        if carrier_code is not None:
            payload_json["carrier_code"] = carrier_code
        return cls(
            kind=RuntimeIntentKind.BIN_OPERATION_REQUEST,
            action=operation_type,
            idempotency_key=operation_key,
            payload_json=payload_json,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def rack_bin_exchange_request(
        cls,
        *,
        operation_type: str,
        operation_key: str,
        moves: list[dict[str, Any]],
        rack_code: str | None = None,
        carrier_type: str = "CTU",
        carrier_code: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RuntimeIntent:
        payload_json: dict[str, Any] = {
            "moves": deepcopy(moves),
            "carrier_type": carrier_type,
        }
        if carrier_code is not None:
            payload_json["carrier_code"] = carrier_code
        if rack_code is not None:
            payload_json["rack_code"] = rack_code
        return cls(
            kind=RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST,
            action=operation_type,
            idempotency_key=operation_key,
            payload_json=payload_json,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def device_event(
        cls,
        *,
        device_code: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        timestamp: int | None = None,
        event_id: str | None = None,
        causation_id: str | None = None,
        canonical_event_type: str | None = None,
    ) -> RuntimeIntent:
        payload_json: dict[str, Any] = {
            "device_code": device_code,
            "event_type": event_type,
            "timestamp": timestamp if timestamp is not None else int(timezone.now_utc().timestamp() * 1000),
            "data": deepcopy(data) if data is not None else {},
        }
        if event_id is not None:
            payload_json["event_id"] = event_id
        if causation_id is not None:
            payload_json["causation_id"] = causation_id
        if canonical_event_type is not None:
            payload_json["canonical_event_type"] = canonical_event_type

        return cls(
            kind=RuntimeIntentKind.DEVICE_EVENT,
            payload_json=payload_json,
        )

    @classmethod
    def resource_fact(
        cls,
        *,
        fact_type: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.RESOURCE_FACT,
            action=fact_type,
            idempotency_key=idempotency_key,
            payload_json=deepcopy(payload),
        )

    @classmethod
    def resource_reservation(
        cls,
        *,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.RESOURCE_RESERVATION,
            action=operation,
            idempotency_key=idempotency_key,
            payload_json=deepcopy(payload),
        )

    @classmethod
    def resource_wait(
        cls,
        *,
        subject_type: str,
        subject_key: str,
        projection_type: str,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeIntent:
        payload_json = deepcopy(payload) if payload is not None else {}
        payload_json["subject_type"] = subject_type
        payload_json["subject_key"] = subject_key
        payload_json["projection_type"] = projection_type
        return cls(
            kind=RuntimeIntentKind.RESOURCE_WAIT,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
            payload_json=payload_json,
        )

    @classmethod
    def block(
        cls,
        *,
        scope: BlockScope,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.BLOCK,
            block_scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
            payload_json=deepcopy(payload) if payload is not None else {},
        )

    @classmethod
    def update_context(cls, patch: dict[str, Any]) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.UPDATE_CONTEXT,
            context_patch=deepcopy(patch),
        )

    @classmethod
    def create_material_unit(
        cls,
        *,
        pkg_code: str,
        material_identity_key: str,
        six_in_one: dict[str, Any],
        status: str = "IN_TRANSIT",
        current_location: str | None = None,
    ) -> RuntimeIntent:
        payload_json: dict[str, Any] = {
            "pkg_code": pkg_code,
            "material_identity_key": material_identity_key,
            "six_in_one": deepcopy(six_in_one),
            "status": status,
        }
        if current_location is not None:
            payload_json["current_location"] = current_location
        return cls(
            kind=RuntimeIntentKind.CREATE_MATERIAL_UNIT,
            payload_json=payload_json,
        )

    @classmethod
    def update_material_unit_status(
        cls,
        *,
        material_unit_id: int,
        status: str,
        current_location: str | None = None,
        clear_session_reference: bool = False,
    ) -> RuntimeIntent:
        payload_json: dict[str, Any] = {
            "material_unit_id": material_unit_id,
            "status": status,
        }
        if current_location is not None:
            payload_json["current_location"] = current_location
        if clear_session_reference:
            payload_json["clear_session_reference"] = True
        return cls(
            kind=RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS,
            payload_json=payload_json,
        )

    @classmethod
    def complete(cls, patch: dict[str, Any] | None = None) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.COMPLETE,
            context_patch=deepcopy(patch) if patch is not None else {},
        )

    @classmethod
    def cancel(
        cls,
        *,
        reason_code: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.CANCEL,
            reason_code=reason_code,
            message=message,
            payload_json=deepcopy(payload) if payload is not None else {},
        )

    @classmethod
    def mark_ng(
        cls,
        *,
        reason_code: str,
        message: str,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.MARK_NG,
            reason_code=reason_code,
            message=message,
            payload_json=deepcopy(payload) if payload is not None else {},
            destination=destination,
        )

    @classmethod
    def continue_next(
        cls,
        *,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.CONTINUE_NEXT,
            action=action,
            payload_json=deepcopy(payload) if payload is not None else {},
            destination=destination or Destination.next(),
        )

    @model_validator(mode="after")
    def validate_intent(self) -> RuntimeIntent:  # noqa: PLR0912
        if self.kind == RuntimeIntentKind.COMMAND:
            if self.result_policy is None:
                raise ValueError("COMMAND intent requires result_policy")
        elif self.result_policy is not None:
            raise ValueError("result_policy is only valid for COMMAND intents")
        if self.kind == RuntimeIntentKind.SYSTEM_CAPABILITY:
            self._validate_system_capability()
        else:
            self._reject_system_capability_fields()
        if self.kind == RuntimeIntentKind.COMMAND and not self.action:
            raise ValueError("COMMAND intent requires action")
        if self.kind == RuntimeIntentKind.EXTERNAL_REQUEST:
            if not self.dispatch_key:
                raise ValueError("EXTERNAL_REQUEST intent requires dispatch_key")
            if not self.target_code:
                raise ValueError("EXTERNAL_REQUEST intent requires target_code")
            if self.target_code.startswith("http://") or self.target_code.startswith("https://"):
                raise ValueError("EXTERNAL_REQUEST target_code must be a registered endpoint code, not a raw URL")
            if not self.payload_json:
                raise ValueError("EXTERNAL_REQUEST intent requires payload")
            if self.timeout_seconds is None or self.timeout_seconds <= 0:
                raise ValueError("EXTERNAL_REQUEST intent requires timeout_seconds")
        if self.kind == RuntimeIntentKind.RACK_OPERATION_REQUEST:
            if not self.action:
                raise ValueError("RACK_OPERATION_REQUEST intent requires operation_type")
            if not self.idempotency_key:
                raise ValueError("RACK_OPERATION_REQUEST intent requires operation_key")
            if not self.target_code:
                raise ValueError("RACK_OPERATION_REQUEST intent requires target_code")
            if not self.payload_json:
                raise ValueError("RACK_OPERATION_REQUEST intent requires payload")
            if self.timeout_seconds is None or self.timeout_seconds <= 0:
                raise ValueError("RACK_OPERATION_REQUEST intent requires timeout_seconds")
        if self.kind in {RuntimeIntentKind.BIN_OPERATION_REQUEST, RuntimeIntentKind.RACK_BIN_EXCHANGE_REQUEST}:
            self._validate_handling_operation_request()
        if self.kind == RuntimeIntentKind.DEVICE_EVENT:
            if not self.payload_json.get("device_code"):
                raise ValueError("DEVICE_EVENT intent requires device_code")
            if not self.payload_json.get("event_type"):
                raise ValueError("DEVICE_EVENT intent requires event_type")
            if "timestamp" not in self.payload_json:
                raise ValueError("DEVICE_EVENT intent requires timestamp")
            if not isinstance(self.payload_json.get("timestamp"), int):
                raise ValueError("DEVICE_EVENT intent timestamp must be an integer")
            if "data" not in self.payload_json:
                raise ValueError("DEVICE_EVENT intent requires data")
            if not isinstance(self.payload_json.get("data"), dict):
                raise ValueError("DEVICE_EVENT intent data must be a dict")
        if self.kind == RuntimeIntentKind.RESOURCE_FACT:
            if not self.action:
                raise ValueError("RESOURCE_FACT intent requires action")
            if not self.payload_json:
                raise ValueError("RESOURCE_FACT intent requires payload")
        if self.kind == RuntimeIntentKind.RESOURCE_RESERVATION:
            if not self.action:
                raise ValueError("RESOURCE_RESERVATION intent requires action")
            if not self.payload_json:
                raise ValueError("RESOURCE_RESERVATION intent requires payload")
        if self.kind == RuntimeIntentKind.CREATE_MATERIAL_UNIT:
            if not self.payload_json.get("pkg_code"):
                raise ValueError("CREATE_MATERIAL_UNIT intent requires pkg_code")
            if not self.payload_json.get("material_identity_key"):
                raise ValueError("CREATE_MATERIAL_UNIT intent requires material_identity_key")
            if not isinstance(self.payload_json.get("six_in_one"), dict):
                raise ValueError("CREATE_MATERIAL_UNIT intent requires six_in_one")
            self._ensure_material_unit_status("status")
        if self.kind == RuntimeIntentKind.UPDATE_MATERIAL_UNIT_STATUS:
            if not self.payload_json.get("material_unit_id"):
                raise ValueError("UPDATE_MATERIAL_UNIT_STATUS intent requires material_unit_id")
            self._ensure_material_unit_status("status")
        if self.kind == RuntimeIntentKind.RESOURCE_WAIT:
            if not self.reason_code:
                raise ValueError("RESOURCE_WAIT intent requires reason_code")
            if not self.message:
                raise ValueError("RESOURCE_WAIT intent requires message")
            subject_type = self.payload_json.get("subject_type")
            if not isinstance(subject_type, str) or not subject_type.strip():
                raise ValueError("RESOURCE_WAIT intent requires subject_type")
            subject_key = self.payload_json.get("subject_key")
            if not isinstance(subject_key, str) or not subject_key.strip():
                raise ValueError("RESOURCE_WAIT intent requires subject_key")
            projection_type = self.payload_json.get("projection_type")
            if not isinstance(projection_type, str) or not projection_type.strip():
                raise ValueError("RESOURCE_WAIT intent requires projection_type")
        if self.kind == RuntimeIntentKind.BLOCK:
            if self.block_scope is None:
                raise ValueError("BLOCK intent requires block_scope")
            if not self.reason_code:
                raise ValueError("BLOCK intent requires reason_code")
            if not self.message:
                raise ValueError("BLOCK intent requires message")
        if self.kind == RuntimeIntentKind.CANCEL:
            if not self.reason_code:
                raise ValueError("CANCEL intent requires reason_code")
            if not self.message:
                raise ValueError("CANCEL intent requires message")
        if self.kind == RuntimeIntentKind.MARK_NG:
            if not self.reason_code:
                raise ValueError("MARK_NG intent requires reason_code")
            if not self.message:
                raise ValueError("MARK_NG intent requires message")
        return self

    def _reject_system_capability_fields(self) -> None:
        exclusive_fields = {
            "capability_key": self.capability_key,
            "contract_version": self.contract_version,
            "operation_key": self.operation_key,
            "payload_hash": self.payload_hash,
            "precondition_json": self.precondition_json,
            "fact_version": self.fact_version,
            "creator_authority": self.creator_authority,
            "authorization_policy": self.authorization_policy,
            "binding_snapshot": self.binding_snapshot,
            "provider_snapshot": self.provider_snapshot,
        }
        if any(value not in (None, {}, "") for value in exclusive_fields.values()):
            raise ValueError("non-SYSTEM_CAPABILITY intent contains SYSTEM_CAPABILITY-only fields")

    def _validate_system_capability(self) -> None:
        for field_name in (
            "capability_key",
            "contract_version",
            "operation_key",
            "creator_authority",
            "authorization_policy",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SYSTEM_CAPABILITY intent requires {field_name}")
        validate_key_version(str(self.capability_key), field_name="capability_key")
        validate_key_version(str(self.contract_version), field_name="contract_version")
        validate_system_capability_operation_key(self.operation_key)
        if not self.payload_json:
            raise ValueError("SYSTEM_CAPABILITY intent requires typed payload")
        if self.payload_hash != sha256_digest(self.payload_json):
            raise ValueError("SYSTEM_CAPABILITY intent payload_hash does not match canonical payload")
        if not self.precondition_json:
            raise ValueError("SYSTEM_CAPABILITY intent requires precondition")
        if isinstance(self.fact_version, str) and not self.fact_version.strip():
            raise ValueError("SYSTEM_CAPABILITY intent requires fact_version")
        if self.fact_version is None or isinstance(self.fact_version, bool):
            raise ValueError("SYSTEM_CAPABILITY intent requires fact_version")
        if self.timeout_seconds is None or self.timeout_seconds <= 0:
            raise ValueError("SYSTEM_CAPABILITY intent requires timeout_seconds")
        if not self.binding_snapshot or not self.provider_snapshot:
            raise ValueError("SYSTEM_CAPABILITY intent requires binding/provider snapshot")
        legacy_fields = {
            "device_role": self.device_role,
            "target_device_id": self.target_device_id,
            "action": self.action,
            "dispatch_key": self.dispatch_key,
            "target_code": self.target_code,
            "source_system": self.source_system,
            "idempotency_key": self.idempotency_key,
            "rack_code": self.rack_code,
            "position_code": self.position_code,
            "destination": self.destination,
            "block_scope": self.block_scope,
            "reason_code": self.reason_code,
            "message": self.message,
            "suggested_action": self.suggested_action,
        }
        if any(value is not None for value in legacy_fields.values()) or self.context_patch:
            raise ValueError("SYSTEM_CAPABILITY intent must not use legacy intent fields")

    def _ensure_material_unit_status(self, field_name: str) -> None:
        """校验料盘状态为合法 MaterialUnitStatus 枚举值，fail-fast 对齐 manifest loader。"""
        raw = self.payload_json.get(field_name)
        if not raw:
            raise ValueError(f"{self.kind.value} intent requires {field_name}")
        from src.app.runtime.orchestration.models.material_unit import MaterialUnitStatus

        try:
            _ = MaterialUnitStatus(raw)
        except ValueError as exc:
            raise ValueError(
                f"{self.kind.value} intent {field_name} must be a valid MaterialUnitStatus, got: {raw!r}"
            ) from exc

    def _validate_handling_operation_request(self) -> None:
        kind = self.kind.value
        if not self.action:
            raise ValueError(f"{kind} intent requires operation_type")
        if not self.idempotency_key:
            raise ValueError(f"{kind} intent requires operation_key")
        if self.dispatch_key or self.target_code:
            raise ValueError(f"{kind} intent must not expose transport fields")
        if not self.payload_json.get("carrier_type"):
            raise ValueError(f"{kind} intent requires carrier_type")
        moves = self.payload_json.get("moves")
        if not isinstance(moves, list) or not moves:
            raise ValueError(f"{kind} intent requires payload.moves")
        for index, move in enumerate(moves, start=1):
            if not isinstance(move, dict):
                raise TypeError(f"{kind} intent requires payload.moves[{index}] mapping")
            if _HANDLING_TRANSPORT_FIELDS.intersection(move):
                raise ValueError(f"{kind} intent must not expose transport fields")
        if self.timeout_seconds is None or self.timeout_seconds <= 0:
            raise ValueError(f"{kind} intent requires timeout_seconds")


__all__ = [
    "BlockScope",
    "Destination",
    "DestinationKind",
    "RuntimeIntent",
    "RuntimeIntentKind",
    "validate_system_capability_operation_key",
]
