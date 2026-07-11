"""锁定 repository/UoW 消费者不再引用 legacy WorklineInbox。"""

import inspect
from pathlib import Path

from src.app.runtime.orchestration.repositories.smt_inbound_handoff_repository import SmtInboundHandoffRepository
from src.app.workline.repositories.workline_repository import WorkLineRepository

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


def test_runtime_inbox_has_one_repository_owner() -> None:
    legacy_repository = REPOSITORY_ROOT / "src/app/runtime/orchestration/consumers/runtime_inbox_repository.py"
    assert not legacy_repository.exists(), "consumers 下仍存在第二个 RuntimeInboxRepository owner"

    forbidden_import = "src.app.runtime.orchestration.consumers.runtime_inbox_repository"
    for path in (REPOSITORY_ROOT / "src").rglob("*.py"):
        assert forbidden_import not in path.read_text(encoding="utf-8"), f"{path} 仍 import 旧 repository owner"


def test_business_repositories_require_query_port_without_concrete_persistence_import() -> None:
    concrete_module = "src.app.runtime.orchestration.repositories.runtime_inbox_repository"
    for relative_path in MIGRATED_FILES[:2]:
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert concrete_module not in source, f"{relative_path} 直接依赖 RuntimeInbox persistence implementation"

    assert inspect.signature(WorkLineRepository).parameters["runtime_inbox_query"].default is inspect.Parameter.empty
    assert (
        inspect.signature(SmtInboundHandoffRepository).parameters["runtime_inbox_query"].default
        is inspect.Parameter.empty
    )


def test_runtime_repository_wiring_is_the_only_business_repository_composition_root() -> None:
    wiring = REPOSITORY_ROOT / "src/app/runtime/orchestration/repository_wiring.py"
    assert wiring.is_file(), "缺 RuntimeInbox query port 的 composition root"
    source = wiring.read_text(encoding="utf-8")
    assert "WorkLineRepository(runtime_inbox_query=runtime_inbox_repository)" in source
    assert "SmtInboundHandoffRepository(runtime_inbox_query=runtime_inbox_repository)" in source
