"""WorklineInbox 退役终态 guardrail。"""

from pathlib import Path

from src.app.runtime.orchestration.models.diagnostic import WorklineDiagnostic
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHold
from src.app.runtime.orchestration.models.smt_inbound_handoff import SmtInboundHandoffSourceItem
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_FILES = (
    "src/app/runtime/orchestration/models/inbox.py",
    "src/app/runtime/orchestration/repositories/inbox_repository.py",
    "src/app/runtime/orchestration/services/inbox/inbox_service.py",
)
FORBIDDEN_TOKENS = (
    "WorklineInbox",
    "WorklineInboxRepository",
    "WorklineInboxService",
    "wes_biz.workline_inbox",
    "callback_ingress_service.inbox_service",
    "create_device_event_inbox",
    "create_command_result_inbox",
    "create_external_http_inbox",
    "create_internal_event_inbox",
    "create_timeout_inbox",
)


def test_legacy_workline_inbox_surface_is_physically_removed() -> None:
    for relative_path in LEGACY_FILES:
        assert not (REPO_ROOT / relative_path).exists(), f"旧 Inbox 文件仍存在: {relative_path}"


def test_active_source_and_tests_have_zero_legacy_workline_inbox_references() -> None:
    offenders: list[str] = []
    for root_name in ("src", "tests"):
        for path in (REPO_ROOT / root_name).rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in FORBIDDEN_TOKENS):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, f"active code/test 仍引用旧 WorklineInbox: {sorted(offenders)}"


def test_runtime_inbox_and_dependent_models_point_to_current_authorities() -> None:
    assert RuntimeInbox.__table__.c.workline_session_id.foreign_keys.pop().target_fullname == (
        "wes_biz.workline_sessions.id"
    )
    runtime_target = "wes_runtime.runtime_inbox.id"
    for model, column_name in (
        (SmtInboundHandoffSourceItem, "source_pick_inbox_id"),
        (RuntimeHold, "source_inbox_id"),
        (WorklineDiagnostic, "inbox_id"),
    ):
        foreign_keys = getattr(model.__table__.c, column_name).foreign_keys
        assert len(foreign_keys) == 1
        assert next(iter(foreign_keys)).target_fullname == runtime_target
