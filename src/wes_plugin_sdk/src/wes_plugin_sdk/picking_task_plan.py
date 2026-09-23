"""PickingTask 已应用计划交给业务插件的不可变合同。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .decisions import TransportRackPosition, TransportRackReference, TransportRcsTemplateId
from .facts import HandlerFact
from .protocols import PositionBindingSnapshot
from .validation import validate_opaque_face
from .validation import validate_required_text as _required


@dataclass(frozen=True, slots=True)
class PickingTaskPlanRack:
    """一个物理货架及其按 WES 本地顺序冻结的待处理面。"""

    rack_id: str
    rack_faces: tuple[str, ...]
    source_evidence_id: str
    plan_revision: int

    def __post_init__(self) -> None:
        _ = _required(self.rack_id, "rack_id")
        _ = _required(self.source_evidence_id, "source_evidence_id")
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ValueError("plan_revision must be a positive integer")
        if type(self.rack_faces) is not tuple:
            raise TypeError("rack_faces must be a tuple")
        if not self.rack_faces:
            raise ValueError("rack_faces must not be empty")
        for face in self.rack_faces:
            validate_opaque_face(face, "rack_faces")
        if len(self.rack_faces) != len(set(self.rack_faces)):
            raise ValueError("rack_faces must not contain duplicates")


@dataclass(frozen=True, slots=True)
class PickingTaskPlanAppliedFact(HandlerFact):
    """宿主从已提交计划和工作线位置绑定构造的 typed fact。"""

    fact_id: str
    evidence_id: str
    fact_version: str
    task_id: str
    plan_revision: int
    target_rack: PickingTaskPlanRack | None
    pending_bin_source_racks: tuple[PickingTaskPlanRack, ...]
    position_bindings: tuple[PositionBindingSnapshot, ...]
    pending_return_racks: tuple[PickingTaskPlanRack, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("fact_id", "evidence_id", "fact_version", "task_id"):
            _ = _required(getattr(self, field_name), field_name)
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ValueError("plan_revision must be a positive integer")
        if self.target_rack is not None and type(self.target_rack) is not PickingTaskPlanRack:
            raise TypeError("target_rack must be a PickingTaskPlanRack")
        if type(self.pending_bin_source_racks) is not tuple or any(
            type(rack) is not PickingTaskPlanRack for rack in self.pending_bin_source_racks
        ):
            raise TypeError("pending_bin_source_racks must contain PickingTaskPlanRack values")
        rack_ids = [rack.rack_id for rack in self.pending_bin_source_racks]
        member_ids = [(rack.source_evidence_id, rack.rack_id) for rack in self.pending_bin_source_racks]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("pending_bin_source_racks must not contain duplicate members")
        if self.target_rack is not None and self.target_rack.rack_id in rack_ids:
            raise ValueError("target_rack must not duplicate a bin source rack_id")
        if type(self.position_bindings) is not tuple or any(
            type(binding) is not PositionBindingSnapshot for binding in self.position_bindings
        ):
            raise TypeError("position_bindings must contain PositionBindingSnapshot values")
        roles = [binding.position_role for binding in self.position_bindings]
        if len(roles) != len(set(roles)):
            raise ValueError("position_bindings must not contain duplicate roles")
        if type(self.pending_return_racks) is not tuple or any(
            type(rack) is not PickingTaskPlanRack for rack in self.pending_return_racks
        ):
            raise TypeError("pending_return_racks must contain PickingTaskPlanRack values")
        return_rack_ids = [rack.rack_id for rack in self.pending_return_racks]
        return_member_ids = [(rack.source_evidence_id, rack.rack_id) for rack in self.pending_return_racks]
        if len(return_member_ids) != len(set(return_member_ids)):
            raise ValueError("pending_return_racks must not contain duplicate members")
        if (self.target_rack is not None and self.target_rack.rack_id in return_rack_ids) or any(
            rack_id in rack_ids for rack_id in return_rack_ids
        ):
            raise ValueError("pending_return_racks rack_id must not duplicate target or bin source racks")


@dataclass(frozen=True, slots=True)
class PickingTaskRackTransportIntent:
    """插件给宿主的完整货架进场意图；不执行 I/O。"""

    task_id: str
    fact_id: str
    source_evidence_id: str
    position_role: str
    rack_id: str
    source: TransportRackReference
    target: TransportRackPosition
    target_face: str
    rcs_template_id: TransportRcsTemplateId

    def __post_init__(self) -> None:
        for field_name in ("task_id", "fact_id", "source_evidence_id", "position_role", "rack_id"):
            _ = _required(getattr(self, field_name), field_name)
        if type(self.source) is not TransportRackReference or self.source.location_code != self.rack_id:
            raise ValueError("source must be a TransportRackReference matching rack_id")
        if type(self.target) is not TransportRackPosition:
            raise TypeError("target must be a TransportRackPosition")
        validate_opaque_face(self.target_face, "target_face")
        if type(self.rcs_template_id) is not TransportRcsTemplateId:
            raise TypeError("rcs_template_id must be a TransportRcsTemplateId")


@dataclass(frozen=True, slots=True)
class PickingTaskPlanHandlingResult:
    transports: tuple[PickingTaskRackTransportIntent, ...]

    def __post_init__(self) -> None:
        if type(self.transports) is not tuple or any(
            type(intent) is not PickingTaskRackTransportIntent for intent in self.transports
        ):
            raise TypeError("transports must contain PickingTaskRackTransportIntent values")


class PickingTaskPlanAppliedHandler(Protocol):
    def __call__(self, fact: PickingTaskPlanAppliedFact) -> PickingTaskPlanHandlingResult: ...


@dataclass(frozen=True, slots=True)
class PickingTaskPlanAdmissionFact:
    """已通过公共 DTO 校验、供插件做业务准入判断的最小计划事实。"""

    task_id: str
    plan_revision: int
    has_direct_picks: bool

    def __post_init__(self) -> None:
        _ = _required(self.task_id, "task_id")
        if type(self.plan_revision) is not int or self.plan_revision < 1:
            raise ValueError("plan_revision must be a positive integer")
        if type(self.has_direct_picks) is not bool:
            raise TypeError("has_direct_picks must be a bool")


class PickingTaskPlanAdmissionDecisionKind(StrEnum):
    """计划业务准入结果。"""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class PickingTaskPlanAdmissionDecision:
    """插件对已验证计划返回的封闭、不可变准入结果。"""

    kind: PickingTaskPlanAdmissionDecisionKind
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not PickingTaskPlanAdmissionDecisionKind:
            raise TypeError("kind must be a PickingTaskPlanAdmissionDecisionKind")
        if self.kind is PickingTaskPlanAdmissionDecisionKind.REJECT:
            _ = _required(self.reason_code, "reason_code")
        elif self.reason_code is not None:
            raise ValueError("ACCEPT must not include reason_code")


class PickingTaskPlanAdmissionPolicy(Protocol):
    def __call__(self, fact: PickingTaskPlanAdmissionFact) -> PickingTaskPlanAdmissionDecision: ...


__all__ = (
    "PickingTaskPlanAdmissionDecision",
    "PickingTaskPlanAdmissionDecisionKind",
    "PickingTaskPlanAdmissionFact",
    "PickingTaskPlanAdmissionPolicy",
    "PickingTaskPlanAppliedFact",
    "PickingTaskPlanAppliedHandler",
    "PickingTaskPlanHandlingResult",
    "PickingTaskPlanRack",
    "PickingTaskRackTransportIntent",
)
