from __future__ import annotations

import tomllib
from pathlib import Path

MATRIX = Path(__file__).parents[2] / "docs" / "architecture" / "reliable-recovery-ac-owner-matrix.toml"
VALID_STATUSES = {"VERIFIED", "PARTIAL", "UNVERIFIED", "BLOCKED-EXTERNAL"}


def test_reliable_recovery_owner_matrix_has_one_entry_per_acceptance_criterion() -> None:
    document = tomllib.loads(MATRIX.read_text(encoding="utf-8"))
    entries = document["acceptance"]
    assert [entry["id"] for entry in entries] == [f"AC{index}" for index in range(1, 43)]
    assert all(entry["status"] in VALID_STATUSES for entry in entries)
    for entry in entries:
        assert entry["owner"] or entry["status"] in {"UNVERIFIED", "BLOCKED-EXTERNAL"}
