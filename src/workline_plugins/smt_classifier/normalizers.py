"""SMT 粗分机插件归一化辅助。"""

from __future__ import annotations

from src.workline_plugins.smt_classifier.contract import MeasurementResultData, PickPlaceResultData
from src.workline_runtime.plugin_base import try_parse_normalized_result_data
from src.workline_runtime.plugin_sdk.contracts import NormalizedCommandResult  # noqa: TC001


def parse_measurement_result_data(result: NormalizedCommandResult) -> MeasurementResultData | None:
    """从标准化命令结果中提取测量结果 data。"""

    return try_parse_normalized_result_data(result, MeasurementResultData)


def parse_pick_place_result_data(result: NormalizedCommandResult) -> PickPlaceResultData | None:
    """从标准化命令结果中提取抓取放置结果 data。"""

    return try_parse_normalized_result_data(result, PickPlaceResultData)


__all__ = [
    "parse_measurement_result_data",
    "parse_pick_place_result_data",
]
