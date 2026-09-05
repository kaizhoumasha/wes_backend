"""无注册副作用的 handler 静态元数据。"""

from collections.abc import Callable
from dataclasses import dataclass, is_dataclass
from typing import TypeVar

from .facts import FactReference

THandler = TypeVar("THandler")


@dataclass(frozen=True, slots=True)
class HandlerMetadata:
    fact_type: type[FactReference]
    name: str
    supported_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fact_type, type):
            raise TypeError("fact_type must be a type")
        if not issubclass(self.fact_type, FactReference):
            raise TypeError("fact_type must inherit FactReference")
        params = self.fact_type.__dict__.get("__dataclass_params__")
        fields = self.fact_type.__dict__.get("__dataclass_fields__")
        slots = self.fact_type.__dict__.get("__slots__")
        if (
            not is_dataclass(self.fact_type)
            or params is None
            or fields is None
            or not params.frozen
            or not params.slots
            or not isinstance(slots, tuple)
            or "__dict__" in slots
            or self.fact_type.__dictoffset__ != 0
            or any(not isinstance(slot, str) or slot not in fields for slot in slots)
            or "__setattr__" not in self.fact_type.__dict__
            or "__delattr__" not in self.fact_type.__dict__
        ):
            raise TypeError("fact_type must be declared as a frozen slots dataclass")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must not be blank")
        if type(self.supported_versions) is not tuple:
            raise TypeError("supported_versions must be a tuple")
        if not self.supported_versions:
            raise ValueError("supported_versions must not be empty")
        if len(self.supported_versions) != len(set(self.supported_versions)):
            raise ValueError("supported_versions must not contain duplicates")
        if any(not isinstance(version, str) or not version.strip() for version in self.supported_versions):
            raise ValueError("supported_versions must contain non-blank strings")


def handler(
    *, fact_type: type[FactReference], name: str, supported_versions: tuple[str, ...]
) -> Callable[[THandler], THandler]:
    metadata = HandlerMetadata(fact_type=fact_type, name=name, supported_versions=supported_versions)

    def decorate(target: THandler) -> THandler:
        setattr(target, "__wes_handler__", metadata)  # noqa: B010 - target may be a class or callable object.
        return target

    return decorate
