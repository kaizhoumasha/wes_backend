# Task 1 实施报告：冻结 WMS 全工厂 35 项合同

## 结论

Task 1 已完成。WMS 北向目标合同现由 19 项 QUERY、16 项 EFFECT 的 operation-specific typed Definition
组成；各业务域显式导出静态 `OPERATIONS`，顶层 registry 只做静态组合。Provider operation manifest、
conformance requirement、业务场景覆盖与 Mock fixture 均由该 registry 派生并 fail closed。

## 主要变更

- 新增静态 Definition、预算、分页、completion mode、lane 与唯一 registry；注册表导入时强制恰好 35 个唯一
  identity。
- 为 35 项 operation 分别建立严格 request/result model，禁止 extra 字段；所有 EFFECT 使用 POST。
- Q19 冻结为无副作用 POST QUERY，包含六合一码、测量、correlation identity、typed result 与 7 项拒绝码闭集。
- GRN 收敛为 PO 行级记录，删除 `WmsGrnItem`、`list_grn_items`、`item_count`；事件身份同步为
  `grn_id + po_number + po_item + material_code`。
- E01–E07/E15/E16 固定 `SYNC_RESULT`，E08–E14 固定 `ASYNC_TASK`；Definition 仅允许异步项声明 status 能力。
- E11 请求不再携带 WES 选择的空箱；E12/E13 使用批量 typed request/result，冻结批次成员、FIFO 候选、
  candidate digest、接纳成员和逐箱结果字段。
- 增加业务场景覆盖 manifest、旧 transport identity 到 E08–E14 的迁移清单；目标 manifest 不注册旧
  transport identity，也不提供 alias/facade/fallback。
- Mock validator 从 registry 解析模型与拒绝码；新增 35 组 request/result fixture，缺少任一项即合同测试失败。
- 修订北向合同、Mock 能力、可观测性、外部 profile 与全工厂业务蓝图；callback 只保留 hint 语义，南向
  `PICK ACK → 下一北向；PICK result → SCAN；SCAN result → WES 决策；PUT result → 最终事实` 已冻结。

## TDD 证据

1. RED：首次运行 registry 合同测试失败，原因为
   `src.app.wms_integration.operation_registry` 不存在。
2. GREEN：实现静态 registry 与 typed contracts 后，35 项唯一 identity 合同通过。
3. RED：补充 Q19 未声明拒绝码测试后，旧实现未抛 `ValidationError`。
4. GREEN：将 Q19 `reason_code` 收窄为 7 项 Literal 闭集后测试通过。

## GitNexus

- 修改前已对所有涉及的现有生产 symbol 执行 upstream impact analysis。
- `WmsGrnInfo`、`WmsDocumentPort`、`WmsFulfillmentPort`、`WmsGrnReceivedEvent` 等为 LOW。
- `FullBoxExchangeOperationRequest`、`WmsEffectStatusRequest` 为 MEDIUM；本任务未改动旧 runtime status
  接线。
- `WmsProviderOperationBinding` 为 HIGH、`build_active_wms_provider_profile` 为 CRITICAL；虽然控制器已确认
  未发布系统允许优化，本任务仍未修改二者，避免提前实现 T2/T5。
- 索引刷新后运行 `gitnexus detect-changes --scope all`：21 files / 82 symbols，0 affected processes，
  risk level LOW。

## 验证

- `uv run pytest tests/contracts/wms_integration tests/wms_integration tests/architecture/test_wms_7_ports_contract.py -q`
  加 fulfillment 边界最终复核 → 391 passed。
- `uv run pytest tests/mock/test_wms_northbound_contract.py -q` → 37 passed。
- 针对 catalog、Mock、process naming 与 fulfillment 边界复核 → 62 passed。
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q` → 6 passed。
- `uv run pytest --collect-only -q -o addopts=''` → 4249 tests collected。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Ruff format/check、Bandit、runtime guardrails
  365 passed、legacy absence、process naming、import-linter、architecture、topology）。

## 后续边界

本任务只冻结目标合同与静态派生资产，不提前切换运行时 endpoint compiler/dispatcher。现有旧 runtime
full-box/status/transport 执行路径由 T2/T5 按本 registry 迁移；它们不属于目标 Provider manifest，也不能作为新
operation 的兼容入口。
