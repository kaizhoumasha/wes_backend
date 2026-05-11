"""In-memory runtime projections for operator current views."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


@dataclass
class MaterialRuntimeView:
    material_identity_key: str
    workline_id: int
    current_device_id: int | None = None
    current_device_role: str | None = None
    current_action: str | None = None
    blocked: bool = False
    block_reason: str | None = None


@dataclass
class LineRuntimeView:
    workline_id: int
    blocked_count: int = 0


@dataclass
class ProjectionState:
    materials: dict[str, MaterialRuntimeView] = field(default_factory=dict)
    lines: dict[int, LineRuntimeView] = field(default_factory=dict)

    def apply(self, event: RuntimeEvent) -> None:
        line = self.lines.setdefault(event.workline_id, LineRuntimeView(workline_id=event.workline_id))
        material = self._ensure_material(event)

        if event.event_type == RuntimeEventType.MATERIAL_ENTERED_DEVICE and material is not None:
            material.current_device_id = event.device_id
            material.current_device_role = event.device_role
            material.current_action = event.action

        if event.event_type == RuntimeEventType.PROCESS_BLOCKED:
            line.blocked_count += 1
            if material is not None:
                material.blocked = True
                material.block_reason = event.reason_code

    def _ensure_material(self, event: RuntimeEvent) -> MaterialRuntimeView | None:
        if event.material_identity_key is None:
            return None

        return self.materials.setdefault(
            event.material_identity_key,
            MaterialRuntimeView(
                material_identity_key=event.material_identity_key,
                workline_id=event.workline_id,
            ),
        )


__all__ = ["LineRuntimeView", "MaterialRuntimeView", "ProjectionState"]
