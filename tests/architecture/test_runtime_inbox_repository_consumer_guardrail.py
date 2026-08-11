"""锁定 repository/UoW 消费者只使用 RuntimeInbox query port。"""

import inspect
from pathlib import Path

from src.app.workline.repositories.workline_repository import WorkLineRepository

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MIGRATED_FILES = (
    "src/app/workline/repositories/workline_repository.py",
    "src/app/workline/unit_of_work.py",
    "src/app/runtime/orchestration/services/query/runtime_query_service.py",
    "src/app/runtime/orchestration/services/trace/trace_query_service.py",
    "src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py",
    "src/app/sys/repositories/outbox_repository.py",
)


def test_repository_uow_consumers_have_no_legacy_workline_inbox_reference() -> None:
    for relative_path in MIGRATED_FILES:
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert "models.inbox" not in source
        assert "repositories.inbox_repository" not in source


def test_runtime_inbox_has_one_repository_owner() -> None:
    legacy_repository = REPOSITORY_ROOT / "src/app/runtime/orchestration/consumers/runtime_inbox_repository.py"
    assert not legacy_repository.exists(), "consumers 下仍存在第二个 RuntimeInboxRepository owner"

    forbidden_import = "src.app.runtime.orchestration.consumers.runtime_inbox_repository"
    for path in (REPOSITORY_ROOT / "src").rglob("*.py"):
        assert forbidden_import not in path.read_text(encoding="utf-8"), f"{path} 仍 import 旧 repository owner"

    claim_repository = REPOSITORY_ROOT / "src/app/runtime/orchestration/repositories/runtime_inbox_claim_repository.py"
    assert not claim_repository.exists(), "claim/fencing 仍由第二个 RuntimeInbox repository 持有"

    claim_import = "runtime_inbox_claim_repository"
    for path in (REPOSITORY_ROOT / "src").rglob("*.py"):
        assert claim_import not in path.read_text(encoding="utf-8"), f"{path} 仍 import 第二个 claim repository"


def test_business_repositories_require_query_port_without_concrete_persistence_import() -> None:
    concrete_module = "src.app.runtime.orchestration.repositories.runtime_inbox_repository"
    for relative_path in MIGRATED_FILES[:1]:
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert concrete_module not in source, f"{relative_path} 直接依赖 RuntimeInbox persistence implementation"

    assert inspect.signature(WorkLineRepository).parameters["runtime_inbox_query"].default is inspect.Parameter.empty


def test_runtime_repository_wiring_is_the_only_business_repository_composition_root() -> None:
    wiring = REPOSITORY_ROOT / "src/app/runtime/orchestration/repository_wiring.py"
    assert wiring.is_file(), "缺 RuntimeInbox query port 的 composition root"
    source = wiring.read_text(encoding="utf-8")
    assert "WorkLineRepository(runtime_inbox_query=runtime_inbox_repository)" in source
