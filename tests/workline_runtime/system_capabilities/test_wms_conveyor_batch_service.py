"""E12 批次 identity 与 pinned capacity snapshot 的纯合同。"""

from __future__ import annotations

from src.app.runtime.orchestration.services.wms_conveyor_batch_service import (
    WmsConveyorBatchService,
)


def test_e12_capacity_snapshot_and_batch_identity_are_canonical_and_stable() -> None:
    snapshot = WmsConveyorBatchService.capacity_snapshot_version(
        binding_id=17,
        binding_version=3,
        plugin_config_hash="a" * 64,
        entry_capacity=4,
    )
    repeated_snapshot = WmsConveyorBatchService.capacity_snapshot_version(
        binding_id=17,
        binding_version=3,
        plugin_config_hash="a" * 64,
        entry_capacity=4,
    )
    changed_snapshot = WmsConveyorBatchService.capacity_snapshot_version(
        binding_id=17,
        binding_version=3,
        plugin_config_hash="a" * 64,
        entry_capacity=5,
    )

    first = WmsConveyorBatchService.batch_identity(
        workline_id=9,
        queue_code="CONVEYOR_ENTRY",
        batch_token="winner-token-1",
    )
    repeated = WmsConveyorBatchService.batch_identity(
        workline_id=9,
        queue_code="CONVEYOR_ENTRY",
        batch_token="winner-token-1",
    )
    next_physical_cycle = WmsConveyorBatchService.batch_identity(
        workline_id=9,
        queue_code="CONVEYOR_ENTRY",
        batch_token="winner-token-2",
    )

    assert snapshot == repeated_snapshot
    assert snapshot != changed_snapshot
    assert first == repeated
    assert first != next_physical_cycle
    assert first.batch_id.startswith("wms-e12:")
    assert first.dispatch_key.startswith("wms-e12:")
    assert len(first.batch_id) <= 160
    assert len(first.dispatch_key) <= 240
    assert WmsConveyorBatchService.route_instance_id(first.batch_id, sequence_no=1) == (
        WmsConveyorBatchService.route_instance_id(repeated.batch_id, sequence_no=1)
    )
    assert WmsConveyorBatchService.route_instance_id(first.batch_id, sequence_no=1) != (
        WmsConveyorBatchService.route_instance_id(next_physical_cycle.batch_id, sequence_no=1)
    )
    assert WmsConveyorBatchService.route_instance_id(first.batch_id, sequence_no=1).endswith(":route:1")
