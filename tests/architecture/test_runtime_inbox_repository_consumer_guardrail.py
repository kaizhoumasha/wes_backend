"""锁定 repository/UoW 消费者不再引用 legacy WorklineInbox。"""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATED_FILES = (
    "src/app/runtime/orchestration/repositories/smt_inbound_handoff_repository.py",
    "src/app/workline/repositories/workline_repository.py",
    "src/app/workline/unit_of_work.py",
)


def test_repository_uow_consumers_have_no_legacy_workline_inbox_reference() -> None:
    forbidden_tokens = ("WorklineInbox", "WorklineInboxRepository")

    for relative_path in MIGRATED_FILES:
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in source, f"{relative_path} 仍引用 legacy {token}"
