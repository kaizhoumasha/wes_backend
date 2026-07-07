"""Guard Phase4 business contracts against service/repository/database cycles."""

from __future__ import annotations

import importlib
from pathlib import Path

from scripts.check_phase5_business_destructive_cleanup_gate import phase4_contract_layer_violations

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_phase4_business_contract_package_imports_without_service_side_effects() -> None:
    package = importlib.import_module("src.app.runtime.capabilities.phase4.contracts")

    assert package.__name__ == "src.app.runtime.capabilities.phase4.contracts"


def test_phase4_business_contracts_do_not_import_service_repository_or_database_layers() -> None:
    assert phase4_contract_layer_violations(REPO_ROOT) == ()
