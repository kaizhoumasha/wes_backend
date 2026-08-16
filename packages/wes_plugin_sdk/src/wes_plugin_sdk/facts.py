"""已验证事实的不可变触发引用。"""

from dataclasses import dataclass


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True, slots=True)
class FactReference:
    fact_id: str
    evidence_id: str
    fact_version: str
    material_execution_id: str

    def __post_init__(self) -> None:
        for field_name in ("fact_id", "evidence_id", "fact_version", "material_execution_id"):
            _required(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class EvidenceReadyFact(FactReference):
    pass


@dataclass(frozen=True, slots=True)
class WmsResultReadyFact(FactReference):
    operation_id: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _required(self.operation_id, "operation_id")


@dataclass(frozen=True, slots=True)
class DeviceResultReadyFact(FactReference):
    command_code: str
    device_code: str
    material_trace_id: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _required(self.command_code, "command_code")
        _required(self.device_code, "device_code")
        _required(self.material_trace_id, "material_trace_id")


@dataclass(frozen=True, slots=True)
class TransportResultReadyFact(FactReference):
    transport_task_id: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _required(self.transport_task_id, "transport_task_id")


@dataclass(frozen=True, slots=True)
class ReconciliationResultReadyFact(FactReference):
    reconciliation_id: str

    def __post_init__(self) -> None:
        FactReference.__post_init__(self)
        _required(self.reconciliation_id, "reconciliation_id")


Fact = FactReference
