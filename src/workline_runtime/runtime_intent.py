"""Plugin-facing Runtime intent contracts.

Plugins describe what should happen next. Runtime owns whether the intent is
legal, how target devices are resolved, and how state is persisted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RuntimeIntentKind(str, Enum):
    COMMAND = "COMMAND"
    ROUTE = "ROUTE"
    COMPLETE = "COMPLETE"
    BLOCK = "BLOCK"
    MARK_NG = "MARK_NG"
    CONTINUE_NEXT = "CONTINUE_NEXT"
    UPDATE_CONTEXT = "UPDATE_CONTEXT"


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
    payload_json: dict[str, Any] = Field(default_factory=dict)
    destination: Destination | None = None
    timeout_seconds: int | None = None
    block_scope: BlockScope | None = None
    reason_code: str | None = None
    message: str | None = None
    suggested_action: str | None = None
    context_patch: dict[str, Any] = Field(default_factory=dict)

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
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.COMMAND,
            device_role=device_role,
            target_device_id=target_device_id,
            action=action,
            payload_json=payload or {},
            destination=destination,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def block(
        cls,
        *,
        scope: BlockScope,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.BLOCK,
            block_scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
        )

    @model_validator(mode="after")
    def validate_intent(self) -> RuntimeIntent:
        if self.kind == RuntimeIntentKind.COMMAND and not self.action:
            raise ValueError("COMMAND intent requires action")
        if self.kind == RuntimeIntentKind.BLOCK:
            if self.block_scope is None:
                raise ValueError("BLOCK intent requires block_scope")
            if not self.reason_code:
                raise ValueError("BLOCK intent requires reason_code")
            if not self.message:
                raise ValueError("BLOCK intent requires message")
        return self


__all__ = [
    "BlockScope",
    "Destination",
    "DestinationKind",
    "RuntimeIntent",
    "RuntimeIntentKind",
]
