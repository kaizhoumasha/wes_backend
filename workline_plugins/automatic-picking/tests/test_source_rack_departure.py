"""普通来源架离场只接受精确显式身份。"""

from dataclasses import FrozenInstanceError

import pytest
from automatic_picking.application.source_rack_departure import MatchedSourceRackDeparture


@pytest.mark.parametrize(
    "identity,operation",
    [(True, "op"), (0, "op"), (-1, "op"), (2**63, "op"), (1, ""), (1, " "), (1, "x" * 121), (1, 3)],
)
def test_departure_rejects_incomplete_or_untyped_identity(identity, operation):
    with pytest.raises(ValueError):
        MatchedSourceRackDeparture(identity, operation)


def test_departure_identity_is_immutable():
    identity = MatchedSourceRackDeparture(1, "original")
    with pytest.raises(FrozenInstanceError):
        identity.operation_id = "other"


def test_first_departure_candidate_has_no_existing_operation():
    candidate = MatchedSourceRackDeparture(1)
    assert candidate.operation_id is None
