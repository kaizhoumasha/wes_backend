from pathlib import Path


def test_failed_outbox_repair_does_not_treat_pending_as_device_occupancy() -> None:
    migration = Path("migrations/versions/20260425_1515_f7a8b9c0d1e2_repair_failed_outbox_device_commands.py")
    migration_text = migration.read_text(encoding="utf-8")
    release_device_sql = migration_text.split("UPDATE wes_biz.devices AS d", maxsplit=1)[1]

    assert "active_dc.status IN ('SENT', 'ACK_RECEIVED')" in release_device_sql
    assert "active_dc.status IN ('PENDING', 'SENT', 'ACK_RECEIVED')" not in release_device_sql
