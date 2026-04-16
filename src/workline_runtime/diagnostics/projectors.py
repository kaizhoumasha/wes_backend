"""角色化诊断投影工具。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .codes import OwnerRole

if TYPE_CHECKING:
    from .models import DiagnosticCard


def project_card_for_role(card: DiagnosticCard, role: OwnerRole) -> dict[str, Any]:
    """按角色返回不同粒度的诊断信息。"""

    base = {
        "title": card.title,
        "summary": card.summary,
        "error_code": card.error_code.value,
        "owner_role": card.owner_role.value,
        "context": {
            "correlation_id": card.context.correlation_id,
            "device_code": card.context.device_code,
            "workline_code": card.context.workline_code,
        },
    }

    if role == OwnerRole.USER:
        return {
            **base,
            "user_message": card.user_message,
            "operator_action": card.operator_action,
        }

    if role == OwnerRole.HARDWARE_ENGINEER:
        return {
            **base,
            "technical_summary": card.technical_summary,
            "next_steps": card.next_steps,
            "communication_hint": card.context.extra.get("communication_profile"),
        }

    if role == OwnerRole.OPS:
        return {
            **base,
            "technical_summary": card.technical_summary,
            "next_steps": card.next_steps,
            "severity": card.severity.value,
            "recoverability": card.recoverability.value,
        }

    return {
        **base,
        "technical_summary": card.technical_summary,
        "next_steps": card.next_steps,
        "transition": card.context.transition,
        "canonical_event_type": card.context.canonical_event_type,
    }


__all__ = ["project_card_for_role"]
