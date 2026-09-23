from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.wms_adapter.outbound_picking.manual_rack_direct_pick_wire import (
    ManualRackDirectPickInvalidData,
    parse_manual_rack_direct_pick_event,
    parse_manual_rack_direct_pick_receipt,
)

OPERATION_ID = "019f12d0-58d7-7b4d-a23a-1b90aa5d4472"


def test_direct_pick_completed_accepts_valid_event() -> None:
    """测试合法的直接取料完成事件能够成功解析"""
    event = parse_manual_rack_direct_pick_event(
        {
            "operation_id": OPERATION_ID,
            "operation": "outbound.manual_rack.direct_pick_completed@v1",
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": "PICK-001",
                "plan_revision": 1,
                "rack_id": "RACK-001",
                "rack_face": "A",
                "completed_at": 1_788_389_999_000,
            },
        }
    )

    assert event.data.task_id == "PICK-001"
    assert event.data.rack_id == "RACK-001"
    assert event.data.rack_face == "A"
    assert event.data.completed_at == 1_788_389_999_000


def test_direct_pick_completed_rejects_unknown_fields_in_data() -> None:
    """测试 data 中的未知字段被拒绝"""
    with pytest.raises(ValidationError):
        parse_manual_rack_direct_pick_event(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_rack.direct_pick_completed@v1",
                "timestamp": 1_788_390_000_000,
                "data": {
                    "task_id": "PICK-001",
                    "rack_id": "RACK-001",
                    "rack_face": "A",
                    "completed_at": 1_788_389_999_000,
                    "extra_field": "should_not_exist",
                },
            }
        )


def test_direct_pick_completed_rejects_empty_rack_face() -> None:
    """测试 rack_face 不能为空字符串"""
    with pytest.raises(ValidationError):
        parse_manual_rack_direct_pick_event(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_rack.direct_pick_completed@v1",
                "timestamp": 1_788_390_000_000,
                "data": {
                    "task_id": "PICK-001",
                    "rack_id": "RACK-001",
                    "rack_face": "",
                    "completed_at": 1_788_389_999_000,
                },
            }
        )


def test_direct_pick_completed_rejects_over_long_rack_face() -> None:
    """rack_face 与全仓一致使用 RackFaceText（max_length=10），超长面必须被拒绝"""
    with pytest.raises(ValidationError):
        parse_manual_rack_direct_pick_event(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_rack.direct_pick_completed@v1",
                "timestamp": 1_788_390_000_000,
                "data": {
                    "task_id": "PICK-001",
                    "rack_id": "RACK-001",
                    "rack_face": "A" * 11,
                    "completed_at": 1_788_389_999_000,
                },
            }
        )


def test_direct_pick_completed_rejects_future_completion_time() -> None:
    """测试 completed_at 不能晚于 timestamp"""
    with pytest.raises(ValidationError):
        parse_manual_rack_direct_pick_event(
            {
                "operation_id": OPERATION_ID,
                "operation": "outbound.manual_rack.direct_pick_completed@v1",
                "timestamp": 1_788_390_000_000,
                "data": {
                    "task_id": "PICK-001",
                    "rack_id": "RACK-001",
                    "rack_face": "A",
                    "completed_at": 1_788_390_000_001,
                },
            }
        )


def test_parse_receipt_returns_invalid_data_on_error() -> None:
    """测试 parse_..._receipt 对非法输入返回 ManualRackDirectPickInvalidData 而不是抛异常"""
    result = parse_manual_rack_direct_pick_receipt(
        {
            "operation_id": OPERATION_ID,
            "operation": "outbound.manual_rack.direct_pick_completed@v1",
            "timestamp": 1_788_390_000_000,
            "data": {
                "task_id": "PICK-001",
                "rack_id": "RACK-001",
                "rack_face": "A",
                "completed_at": 1_788_390_000_001,  # 晚于 timestamp，会触发验证错误
            },
        }
    )

    assert isinstance(result, ManualRackDirectPickInvalidData)
    assert result.raw_envelope["data"]["completed_at"] == 1_788_390_000_001
    assert isinstance(result.validation_error, ValueError)
