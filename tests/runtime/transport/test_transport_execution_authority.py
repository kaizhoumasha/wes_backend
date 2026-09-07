"""Transport execution authority 与北向 caller 分离并在任务创建时冻结。"""

from __future__ import annotations

import pytest

from src.app.transport.contracts import TransportExecutionAuthority
from src.app.transport.models import TransportTask


def test_execution_authority_requires_positive_workline_identity() -> None:
    authority = TransportExecutionAuthority(workline_id=7)
    assert authority.workline_id == 7
    for value in (0, -1, True, "7", None):
        with pytest.raises(ValueError):
            TransportExecutionAuthority(workline_id=value)


def test_transport_task_persists_workline_authority_separately_from_caller() -> None:
    fields = TransportTask.model_fields
    assert "authority_workline_id" in fields
    assert {"authority_line_run_epoch_id", "authority_bin_execution_id"}.isdisjoint(fields)
    assert any(
        foreign_key.target_fullname == "wes_biz.work_lines.id"
        for foreign_key in TransportTask.__table__.c.authority_workline_id.foreign_keys
    )
