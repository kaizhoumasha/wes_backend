"""BinExecution 只拥有活动料箱执行与关闭时的 current projection 清理。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.app.execution.models.bin_execution import BinExecutionStatus
from src.app.execution.services.bin_execution_service import (
    ActiveBinExecutionExistsError,
    BinExecutionService,
)


class _Repository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.active = None

    async def lock_epoch_lifecycle(self, _db: object, line_run_epoch_id: int) -> None:
        self.calls.append(f"epoch:{line_run_epoch_id}")

    async def get_active_epoch_for_update(self, _db: object, line_run_epoch_id: int):
        self.calls.append(f"epoch-row:{line_run_epoch_id}")
        return SimpleNamespace(id=line_run_epoch_id, workline_id=7, status="ACTIVE")

    async def lock_bin_execution(self, _db: object, bin_id: str) -> None:
        self.calls.append(f"bin-lock:{bin_id}")

    async def get_active_by_bin_for_update(self, _db: object, bin_id: str):
        self.calls.append(f"active-bin:{bin_id}")
        return self.active

    async def add(self, _db: object, execution):
        execution.id = 31
        self.active = execution
        self.calls.append("add")
        return execution

    async def get_by_id_for_update(self, _db: object, execution_id: int):
        self.calls.append(f"bin-row:{execution_id}")
        return self.active

    async def flush(self, _db: object) -> None:
        self.calls.append("flush")


class _ProjectionRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def lock_projection(self, _db: object, object_type: str, object_id: str) -> None:
        self.calls.append(f"projection-lock:{object_type}:{object_id}")

    async def delete_for_bin_execution(self, _db: object, bin_execution_id: int) -> None:
        self.calls.append(f"projection-delete:{bin_execution_id}")


def _service() -> tuple[BinExecutionService, _Repository, list[str]]:
    calls: list[str] = []
    repository = _Repository(calls)
    return (
        BinExecutionService(repository=repository, projection_repository=_ProjectionRepository(calls)),
        repository,
        calls,
    )


@pytest.mark.asyncio
async def test_create_serializes_epoch_then_bin_and_rejects_a_second_active_owner() -> None:
    service, repository, calls = _service()

    first = await service.create(
        object(),
        execution_code="BIN-EXEC-001",
        bin_id="BIN-001",
        workline_id=7,
        line_run_epoch_id=11,
        started_at=datetime(2026, 8, 28),
    )

    assert first.status == BinExecutionStatus.ACTIVE
    assert calls == ["epoch:11", "epoch-row:11", "bin-lock:BIN-001", "active-bin:BIN-001", "add"]
    with pytest.raises(ActiveBinExecutionExistsError):
        await service.create(
            object(),
            execution_code="BIN-EXEC-002",
            bin_id="BIN-001",
            workline_id=7,
            line_run_epoch_id=11,
            started_at=datetime(2026, 8, 28, 0, 1),
        )
    assert repository.active is first


@pytest.mark.asyncio
async def test_close_uses_fixed_lock_order_and_deletes_current_bin_projection() -> None:
    service, _, calls = _service()
    execution = await service.create(
        object(),
        execution_code="BIN-EXEC-001",
        bin_id="BIN-001",
        workline_id=7,
        line_run_epoch_id=11,
        started_at=datetime(2026, 8, 28),
    )
    calls.clear()

    closed = await service.close(object(), execution, closed_at=datetime(2026, 8, 28, 0, 2))

    assert closed.status == BinExecutionStatus.CLOSED
    assert closed.closed_at == datetime(2026, 8, 28, 0, 2)
    assert calls == [
        "epoch:11",
        "bin-row:31",
        "projection-lock:BIN:BIN-001",
        "projection-delete:31",
        "flush",
    ]
