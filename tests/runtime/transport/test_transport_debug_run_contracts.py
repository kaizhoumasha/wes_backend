from __future__ import annotations

import pytest

from src.app.transport.debug_run_contracts import (
    CreateTransportDebugRun,
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
    TransportDebugRunPhase,
    TransportDebugRunStatus,
    TransportDebugRunStepStatus,
)


def _bin(bin_id: str, slot_id: str) -> TransportDebugBinSelection:
    return TransportDebugBinSelection(bin_id=bin_id, slot_id=slot_id)


def _group(face: str, *bins: TransportDebugBinSelection) -> TransportDebugFaceGroup:
    return TransportDebugFaceGroup(face=face, bins=tuple(bins))


def test_debug_run_contract_exposes_only_the_persisted_state_vocabulary() -> None:
    assert tuple(TransportDebugRunStatus) == (
        TransportDebugRunStatus.RUNNING,
        TransportDebugRunStatus.NEEDS_ATTENTION,
        TransportDebugRunStatus.COMPLETED,
        TransportDebugRunStatus.FAILED,
        TransportDebugRunStatus.ABORTED,
    )
    assert tuple(TransportDebugRunStepStatus) == (
        TransportDebugRunStepStatus.PENDING,
        TransportDebugRunStepStatus.WAITING,
        TransportDebugRunStepStatus.SUCCEEDED,
        TransportDebugRunStepStatus.FAILED,
        TransportDebugRunStepStatus.NEEDS_ATTENTION,
    )
    assert TransportDebugRunPhase.WAIT_SCAN12.value == "WAIT_SCAN12"


def test_debug_run_contract_preserves_face_strings_exactly() -> None:
    request = CreateTransportDebugRun(
        rack_id="510056",
        face_groups=(
            _group(" 90 ", _bin("A000001922", "510056A3F2C101")),
            _group("090", _bin("A000002653", "510056A3F2C102")),
        ),
    )

    assert tuple(group.face for group in request.face_groups) == (" 90 ", "090")


@pytest.mark.parametrize(
    ("rack_id", "bin_id", "slot_id", "message"),
    [
        (" ", "BIN-1", "SLOT-1", "货架编码"),
        ("RACK-1", " ", "SLOT-1", "料箱编码"),
        ("RACK-1", "BIN-1", " ", "原货架槽位"),
    ],
)
def test_debug_run_contract_rejects_incomplete_operator_input(
    rack_id: str,
    bin_id: str,
    slot_id: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        CreateTransportDebugRun(
            rack_id=rack_id,
            face_groups=(_group("90", _bin(bin_id, slot_id)),),
        )


@pytest.mark.parametrize("face", ["", " ", "\t\n"])
def test_debug_run_contract_rejects_blank_faces(face: str) -> None:
    with pytest.raises(ValueError, match="面值"):
        CreateTransportDebugRun(
            rack_id="510056",
            face_groups=(_group(face, _bin("A000001922", "510056A3F2C101")),),
        )


@pytest.mark.parametrize("size", [0, 5])
def test_debug_run_contract_limits_each_face_to_one_through_four_bins(size: int) -> None:
    bins = tuple(_bin(f"BIN-{index}", f"SLOT-{index}") for index in range(size))

    with pytest.raises(ValueError, match="1～4"):
        CreateTransportDebugRun(rack_id="510056", face_groups=(_group("90", *bins),))


def test_debug_run_contract_rejects_duplicate_raw_face_strings() -> None:
    with pytest.raises(ValueError, match="重复面值"):
        CreateTransportDebugRun(
            rack_id="510056",
            face_groups=(
                _group("90", _bin("BIN-1", "SLOT-1")),
                _group("90", _bin("BIN-2", "SLOT-2")),
            ),
        )


def test_debug_run_contract_rejects_a_bin_selected_on_multiple_faces() -> None:
    with pytest.raises(ValueError, match="重复料箱"):
        CreateTransportDebugRun(
            rack_id="510056",
            face_groups=(
                _group("90", _bin("BIN-1", "SLOT-1")),
                _group("270", _bin("BIN-1", "SLOT-2")),
            ),
        )
