"""WorkLine START 等待只依赖持久 Outbox 事实。"""

from types import SimpleNamespace

from src.app.workline.services.diagnosis_verdict_builder_service import DiagnosisVerdictBuilder


def test_start_wait_diagnosis_uses_parked_outbox_without_retired_admission_projection() -> None:
    result = SimpleNamespace(
        session=SimpleNamespace(status="WAITING"),
        sessions=[],
        timelines=[],
        callback_logs=[],
        inboxes=[],
        commands=[],
        diagnostics=[],
        outboxes=[SimpleNamespace(status="RETRY_WAIT", blocked_reason="WORKLINE_STOPPED_WAITING_START")],
    )

    verdict = DiagnosisVerdictBuilder().build(result)

    assert verdict.state == "waiting"
    assert verdict.title == "等待现场 START"
    assert verdict.summary == "WorkLine 已停止，相关消息会在成功 START 后恢复派发。"
    assert verdict.requires_operator_action is False
    assert verdict.primary_action == "确认现场条件后发起 START"
    assert all(item.key != "workline_admission" for item in verdict.evidence_health.items)
