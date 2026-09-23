"""退役模型不再进入当前数据库元数据。"""

from sqlmodel import SQLModel

from src.app.device import models as device_models
from src.app.runtime.orchestration import models as runtime_models


def test_retired_fact_tables_are_absent_from_current_metadata() -> None:
    assert device_models.DeviceCommand.__table__.name == "device_commands"
    assert runtime_models.WorklineSession.__table__.name == "workline_sessions"
    assert "wes_biz.device_status_observations" not in SQLModel.metadata.tables
    assert "wes_biz.runtime_location_events" not in SQLModel.metadata.tables
