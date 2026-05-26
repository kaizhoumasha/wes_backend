# Handling Domain 一致性补齐计划

日期：2026-05-26
依赖：[2026-05-26-handling-domain-consistency-spec.md](../specs/2026-05-26-handling-domain-consistency-spec.md)

## What already exists

- `src/app/handling` 已有系统级 `HandlingOperation` / `HandlingMove` / `HandlingStep` 模型，以及 Repository、Gateway、Operation Service、Lifecycle Service。
- Runtime intent 已通过 `BIN_OPERATION_REQUEST` 和 `RACK_BIN_EXCHANGE_REQUEST` 调用 `HandlingOperationService.request_bin_operation()`。
- Handling 对外通信已通过 `SystemOutbox(operation_domain="HANDLING", dispatch_type="EXTERNAL_HTTP")` 进入系统出站引擎。
- Callback 编排已能将 handling dispatch key、BIN 回调、满箱交换回调路由到 `HandlingOperationLifecycleService.record_callback_from_external_http()`。
- Session resolver 已能通过 handling step / operation 找回等待中的 `WorklineSession`。

## 数据流

```text
Plugin / RuntimeIntent
        |
        v
RuntimeIntentEffectApplier
        |
        v
HandlingOperationService.request_bin_operation()
        |
        +--> HandlingOperation / HandlingMove / HandlingStep
        |
        +--> SystemOutbox(operation_domain="HANDLING")
                    |
                    v
             SystemOutboxEngine
                    |
                    v
              WMS / RCS / CTU
                    |
                    v
CallbackOrchestrationService
        |
        v
HandlingOperationLifecycleService.record_callback_from_external_http()
        |
        +--> step / move / operation status
        |
        +--> Session RUNNING or MANUAL_HOLD
```

## NOT in scope

- 不新建 `src/app/bin` 领域；料箱搬运继续归属 `src/app/handling`。
- 不抽象公共 `OperationBaseService`；Rack 与 Handling 保持各自业务边界。
- 不新增 `/v1/handling` API；本次只补齐内部域合同、文档和测试验证。
- 不在本次将满箱交换显式改造成 `CALLBACK_PLUS_RECONCILIATION` 持久化策略；该工作已记录为 TODO。

## 执行计划清单

- [x] **Step 1: 规范文档补齐**
  - [x] 确立 Handling Domain 是一等系统级域。
  - [x] 拒绝与 Rack 进行过度抽象（不建立公共的 `OperationBaseService`）。
  - [x] 明确完成策略为默认的 `CALLBACK_TRUSTED`。
  - [x] 明确满箱交换是 Handling 域内的对账例外：缺少交换后关系或 `rack_release_id` 不匹配时进入 `RECONCILING` / manual hold。

- [x] **Step 2: 系统级代码垃圾审计**
  - [x] 运行 `git ls-files '*.pyc' '*/__pycache__/*'`，确认没有 Python 缓存文件进入 Git 跟踪。
  - [x] 确认本地 ignored `__pycache__` 不作为 PR 主要成果；无需纳入仓库 diff。

- [x] **Step 3: 测试用例命名一致性审查**
  - [x] 搜索并替换 `tests/handling` 和 `tests/workline_runtime` 中可能残留的历史废弃命名。
  - [x] 确保测试方法名中的诸如 `test_workline_handling_*` 都已经被更新为 `test_handling_*`。
  - [x] 替换时保留合法的 `workline_id`、`workline_code`、`WorkLine.id` 可选上下文语义。

- [x] **Step 4: 测试覆盖回归**
  - [x] 增加 Handling 模型合同断言：`completion_policy` 默认值必须是 `OperationCompletionPolicy.CALLBACK_TRUSTED`。
  - [x] 运行 Handling 的领域单元测试：`uv run pytest tests/handling/`
  - [x] 运行 Runtime 对 Handling 的意图连通性测试：`uv run pytest tests/workline_runtime/test_runtime_intent_effects.py`
  - [x] 运行 Callback 路由测试：`uv run pytest tests/api/test_callback_api.py`
  - [x] 运行 Session Resolver 回调找回测试：`uv run pytest tests/workline_runtime/test_session_resolver.py`
  - [x] 运行 SystemOutbox 外部 HTTP 派发测试：`uv run pytest tests/sys/test_system_outbox_engine.py`

## 验证记录

- `rtk uv run pytest tests/handling/ tests/workline_runtime/test_runtime_intent_effects.py tests/api/test_callback_api.py tests/workline_runtime/test_session_resolver.py tests/sys/test_system_outbox_engine.py`：`138 passed`
- `rtk uv run ruff check tests/handling/test_handling_operation_core.py`：passed
- `rtk git ls-files '*.pyc' '*/__pycache__/*'`：empty
- focused old-name rg over `src/app/handling tests/handling tests/workline_runtime`：no matches

## Failure modes

| 失败模式 | 现有处理 | 验证方式 |
| --- | --- | --- |
| 普通料箱搬运成功回调迟到或重复 | terminal step 忽略不可信覆盖 | `tests/handling/test_handling_operation_lifecycle.py` |
| 满箱交换物理完成但缺少交换后关系 | `RECONCILING` + manual hold | `tests/handling/test_handling_operation_lifecycle.py` |
| 满箱交换 `rack_release_id` 不匹配 | `RECONCILING` + manual hold | `tests/handling/test_handling_operation_lifecycle.py` |
| 外部 callback 只进入 inbox 但未路由 lifecycle | callback focused tests | `tests/api/test_callback_api.py` |
| callback 找不到等待中的 Session | session resolver focused tests | `tests/workline_runtime/test_session_resolver.py` |

## Implementation Tasks

- [x] **T1 (P1, human: ~30min / CC: ~5min)** — 文档 — 补齐满箱交换对账例外与数据流图
  - Surfaced by: Architecture Review — spec 只写 `CALLBACK_TRUSTED`，未说明现有 `RECONCILING` 分支。
  - Files: `docs/superpowers/specs/2026-05-26-handling-domain-consistency-spec.md`, `docs/superpowers/plans/2026-05-26-handling-domain-consistency-plan.md`
  - Verify: 文档包含满箱交换例外、数据流图、NOT in scope。
- [x] **T2 (P2, human: ~20min / CC: ~5min)** — 测试 — 锁定 Handling 默认完成策略合同
  - Surfaced by: Test Review — completion_policy 默认值是核心架构合同，但现有测试未直接断言。
  - Files: `tests/handling/test_handling_operation_core.py`
  - Verify: `uv run pytest tests/handling/`
- [x] **T3 (P1, human: ~45min / CC: ~10min)** — 验证 — 扩展 focused 回归测试命令
  - Surfaced by: Test Review — 当前计划未覆盖 callback、session resolver、SystemOutbox dispatch。
  - Files: `docs/superpowers/plans/2026-05-26-handling-domain-consistency-plan.md`
  - Verify: 执行 Step 4 的全部 focused pytest 命令。
