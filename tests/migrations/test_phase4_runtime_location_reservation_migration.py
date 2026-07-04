"""Phase4 RuntimeLocationEvent / CellReservation migration 语义合同。"""

from __future__ import annotations

from pathlib import Path


MIGRATION_FILE = Path(
    "migrations/versions/20260704_0158_de288342b42d_phase4_runtime_location_and_reservation.py"
)


def _migration_source() -> str:
    return MIGRATION_FILE.read_text(encoding="utf-8")


def _downgrade_body() -> str:
    source = _migration_source()
    return source[source.index("def downgrade()") :]


def test_phase4_reservation_downgrade_blocks_reconciling_before_old_status_constraint() -> None:
    """downgrade 恢复旧状态约束前，必须显式处理 RECONCILING 数据。"""

    source = _migration_source()
    downgrade = _downgrade_body()

    guard_index = downgrade.index("_block_reconciling_reservations_before_downgrade()")
    old_constraint_index = downgrade.index("reservation_status IN ('PLANNED', 'CONSUMED', 'RELEASED', 'CANCELLED')")

    assert guard_index < old_constraint_index
    assert "Cannot downgrade workline_bin_cell_reservations with RECONCILING reservations" in source
    assert "reservation_status = 'RECONCILING'" in source
