from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.wms_integration.outbound_picking.repositories import PickingWorklineFactsRepository


class _Worklines:
    def __init__(self, *, bindings: list[object] | None = None, positions: list[object] | None = None) -> None:
        self.bindings = bindings if bindings is not None else [_binding("SCAN1")]
        self.positions = positions if positions is not None else [SimpleNamespace(position_role="FIVE_RACK")]

    async def list_bindings(self, _db: object, _workline_id: int) -> list[object]:
        return self.bindings

    async def list_position_bindings(self, _db: object, _workline_id: int) -> list[object]:
        return self.positions


def _binding(role: str) -> object:
    return SimpleNamespace(
        device_code="DEVICE-1",
        device_role=role,
        contract_key="manual.conveyor",
        contract_version="1.0",
        status_max_age_ms=5_000,
    )


@pytest.mark.asyncio
async def test_repository_returns_only_static_binding_roles() -> None:
    repository = PickingWorklineFactsRepository(
        workline_repository=_Worklines(),  # type: ignore[arg-type]
    )
    facts = await repository.read_facts(
        object(),  # type: ignore[arg-type]
        workline_id=7,
    )
    assert facts.device_roles == ("SCAN1",)
    assert facts.position_roles == ("FIVE_RACK",)


@pytest.mark.asyncio
async def test_repository_preserves_missing_static_binding_roles() -> None:
    repository = PickingWorklineFactsRepository(
        workline_repository=_Worklines(bindings=[], positions=[]),  # type: ignore[arg-type]
    )
    facts = await repository.read_facts(
        object(),  # type: ignore[arg-type]
        workline_id=7,
    )
    assert facts.device_roles == ()
    assert facts.position_roles == ()
