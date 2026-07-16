"""系统能力最终 Definition 合同。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from numbers import Real
from typing import Any

from pydantic import BaseModel

from src.app.runtime.extension_identity import sha256_digest, stable_sort, validate_key_version


class SystemCapabilityMode(str, Enum):
    """系统能力调用模式。"""

    QUERY = "QUERY"
    EFFECT = "EFFECT"


class EffectCompletionMode(str, Enum):
    """Effect 能力支持的封闭完成语义。"""

    LOCAL_TRANSACTIONAL = "LOCAL_TRANSACTIONAL"
    OUTBOX_ASYNC = "OUTBOX_ASYNC"


def _type_identity(value: type[Any]) -> str:
    return f"{value.__module__}.{value.__qualname__}"


def _stable_callable_identity(value: Any, *, field_name: str) -> str:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if (
        not isinstance(module, str)
        or not module
        or not isinstance(qualname, str)
        or not qualname
        or qualname == "<lambda>"
        or "<locals>" in qualname
    ):
        raise TypeError(f"{field_name} must have a stable import identity")
    return f"{module}.{qualname}"


@dataclass(frozen=True, slots=True)
class SystemCapabilityDefinition:
    """系统能力不可变声明；handler 仅保存 factory，不持有运行时实例。"""

    capability_key: str
    contract_version: str
    mode: SystemCapabilityMode
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler_factory: Any
    required_ports: tuple[type[Any], ...]
    admission: str
    timeout_seconds: float
    completion_mode: EffectCompletionMode
    audit_policy: str

    def __post_init__(self) -> None:
        validate_key_version(self.capability_key, field_name="capability_key")
        validate_key_version(self.contract_version, field_name="contract_version")
        validate_key_version(self.admission, field_name="admission")
        validate_key_version(self.audit_policy, field_name="audit_policy")
        object.__setattr__(self, "mode", SystemCapabilityMode(self.mode))
        object.__setattr__(self, "completion_mode", EffectCompletionMode(self.completion_mode))
        for field_name in ("input_model", "output_model"):
            model = getattr(self, field_name)
            if not inspect.isclass(model) or not issubclass(model, BaseModel):
                raise TypeError(f"{field_name} must be a Pydantic model class")
        if not (inspect.isclass(self.handler_factory) or inspect.isroutine(self.handler_factory)):
            raise TypeError("handler_factory must be a class or function, not a handler instance")
        _stable_callable_identity(self.handler_factory, field_name="handler_factory")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, Real):
            raise TypeError("timeout_seconds must be a finite positive real number")
        timeout_seconds = float(self.timeout_seconds)
        if not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive real number")
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        if any(not inspect.isclass(port) for port in self.required_ports):
            raise TypeError("required_ports must contain Port types")
        object.__setattr__(self, "required_ports", stable_sort(set(self.required_ports), key=_type_identity))

    @property
    def identity(self) -> str:
        """返回只由声明元数据决定的稳定 identity。"""

        payload = {
            "admission": self.admission,
            "audit_policy": self.audit_policy,
            "completion_mode": self.completion_mode.value,
            "handler_factory": _stable_callable_identity(self.handler_factory, field_name="handler_factory"),
            "input_model": _type_identity(self.input_model),
            "mode": self.mode.value,
            "output_model": _type_identity(self.output_model),
            "required_ports": [_type_identity(port) for port in self.required_ports],
            "timeout_seconds": self.timeout_seconds,
        }
        return f"{self.capability_key}@{self.contract_version}:{sha256_digest(payload)}"


__all__ = ["EffectCompletionMode", "SystemCapabilityDefinition", "SystemCapabilityMode"]
