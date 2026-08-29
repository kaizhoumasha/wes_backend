"""Transport execution authority 与北向 caller 分离并在任务创建时冻结。"""

from __future__ import annotations

import pytest

from src.app.transport.contracts import TransportExecutionAuthority
from src.app.transport.models import TransportTask


def test_execution_authority_requires_positive_internal_identities() -> None:
    authority = TransportExecutionAuthority(workline_id=7, line_run_epoch_id=11, bin_execution_id=31)

    assert authority.workline_id == 7
    assert authority.line_run_epoch_id == 11
    assert authority.bin_execution_id == 31
    for values in ((0, 11, None), (7, 0, None), (7, 11, 0)):
        with pytest.raises(ValueError):
            TransportExecutionAuthority(
                workline_id=values[0],
                line_run_epoch_id=values[1],
                bin_execution_id=values[2],
            )


def test_transport_task_persists_all_authority_parts_separately_from_caller() -> None:
    fields = TransportTask.model_fields

    assert {"authority_workline_id", "authority_line_run_epoch_id", "authority_bin_execution_id"} <= set(fields)
    check_names = {constraint.name for constraint in TransportTask.__table__.constraints if constraint.name is not None}
    assert any(name.endswith("transport_execution_authority_all_or_none") for name in check_names)
