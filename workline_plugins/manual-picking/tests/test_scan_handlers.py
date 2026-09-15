from manual_picking.handlers.scan1 import Scan1Handler
from manual_picking.handlers.scan2 import Scan2Handler
from manual_picking.handlers.scan3 import Scan3Handler
from manual_picking.handlers.scan4 import Scan4Handler
from manual_picking.handlers.scan_types import PassageSnapshot, ScanFact


def _fact(role: str, raw: str | None, passage: PassageSnapshot | None = None) -> ScanFact:
    return ScanFact(role=role, evidence_id=10, raw_bin_code=raw, passage=passage, task_id="TASK-1")


def test_scan1_only_accepts_its_b_suffix_and_routes_ng_right() -> None:
    handler = Scan1Handler()
    assert handler.decide(_fact("SCAN1", "A000001234-B")).route == "MOVE_FORWARD"
    for raw in (None, "A000001234-C", "A00000123-B", "B000001234-B"):
        decision = handler.decide(_fact("SCAN1", raw))
        assert decision.route == "MOVE_RIGHT"
        assert decision.normal_bin_code == ("A000001234" if raw == "A000001234-C" else None)


def test_scan2_only_accepts_c_and_same_passage_before_wms() -> None:
    handler = Scan2Handler()
    passage = PassageSnapshot(bin_code="A000001234")
    decision = handler.decide(_fact("SCAN2", "A000001234-C", passage))
    assert decision.route == "REQUEST_WMS"
    assert decision.normal_bin_code == "A000001234"
    for raw in (None, "A000001234-A", "A000001234-B", "A000009999-C"):
        assert handler.decide(_fact("SCAN2", raw, passage)).route == "MOVE_FORWARD"


def test_scan3_does_not_infer_normal_authorization_from_fifo_or_bin_code_alone() -> None:
    handler = Scan3Handler()
    normal = PassageSnapshot(bin_code="A000001234", normal_authorized=True)
    assert handler.decide(_fact("SCAN3", "A000001234-B", normal)).route == "MOVE_FORWARD"
    for raw, passage in (
        ("A000001234-B", None),
        ("A000001234-B", PassageSnapshot(bin_code="A000001234")),
        ("A000001234-B", PassageSnapshot(bin_code="A000001234", ng=True, normal_authorized=True)),
        ("A000009999-B", normal),
        ("A000001234-C", normal),
        (None, normal),
    ):
        assert handler.decide(_fact("SCAN3", raw, passage)).route == "MOVE_LEFT"


def test_scan4_holds_without_unique_prior_normal_scan3_authorization() -> None:
    handler = Scan4Handler()
    normal = PassageSnapshot(bin_code="A000001234", normal_authorized=True, scan3_forward=True)
    assert handler.decide(_fact("SCAN4", "A000001234-B", normal)).route == "MOVE_FORWARD"
    for raw, passage in (
        (None, normal),
        ("A000001234-C", normal),
        ("A000009999-B", normal),
        ("A000001234-B", None),
        ("A000001234-B", PassageSnapshot(bin_code="A000001234", normal_authorized=True)),
    ):
        assert handler.decide(_fact("SCAN4", raw, passage)).route == "HOLD"
