# 阶段 2 burn-down C5a 镜像:src.workline_runtime.device_ordering 的平级副本
# wlr 目录在阶段 3 整体删除时,本镜像与 wlr 副本合并 / 删除。

"""Workline runtime device ordering helpers."""

from __future__ import annotations

from typing import Any

from src.utils.value_normalization import optional_int_attr


def device_sort_key(device: Any) -> tuple[int, int, int]:
    """按拓扑配置顺序稳定排序设备。"""
    return (
        optional_int_attr(device, "sort_order") or 0,
        optional_int_attr(device, "role_index") or 0,
        optional_int_attr(device, "id") or 0,
    )


__all__ = ["device_sort_key"]
