# Foundation 回归修复报告

日期：2026-07-22

## 修复范围

- 通过 `scripts/generate_wms_operation_index.py` 重新生成 WMS operation 静态索引，更新 digest。
- 将 northbound WMS operation inventory 的 `NBWMS-029` 来源改为扫描器实际发现的 `tests/support/rough_sorter_inventory_admission.py`。

## RED 记录

- `uv run python scripts/generate_wms_operation_index.py --check`：报告 generated operation index drift。
- `uv run pytest tests/architecture/test_northbound_wms_operation_inventory.py::test_inventory_covers_every_discovered_legacy_reference -q`：报告 CSV 中的旧 plugin 路径与实际 support helper 路径不一致。

## GREEN 验证

- `uv run python scripts/generate_wms_operation_index.py --check`
- `uv run pytest tests/architecture/test_northbound_wms_operation_inventory.py -q`
- `uv run ruff check src/app/runtime/system_capabilities/wms/generated_operation_index.py tests/architecture/test_northbound_wms_operation_inventory.py`

未修改 `AGENTS.md`、`CLAUDE.md`，未执行 T6。
