from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.full_box_exchange_service import FullBoxExchangeService


def test_e11_domain_context_requires_persisted_workline_but_not_plugin_session() -> None:
    db = object()
    workline = SimpleNamespace(id=13, line_code="SMT-ROUGH-1")

    assert FullBoxExchangeService._validate_execution_context({"db": db, "workline": workline}) == (
        db,
        13,
        "SMT-ROUGH-1",
    )


def test_e11_domain_context_rejects_missing_persisted_workline() -> None:
    with pytest.raises(ValueError, match="workline"):
        FullBoxExchangeService._validate_execution_context({"db": object()})
