# 全分支终审修复报告

## 结论

`.superpowers/sdd/final-branch-review.md` 提出的 1 项 Critical 与 2 项 Important 已全部关闭：

1. SMT generated identity 已接入正式 handoff candidate、route、boundary、activation、seed 和
   generated attempt 主链，生产代码不再保留旧 identity 与 legacy unbound activation。
2. `COMMAND_RESULT` 已建立
   `RuntimeInbox.command_id → DeviceCommand → Session wait / binding owner` 的唯一权威链；
   缺失、错误或串线 ID 均产生稳定诊断且零业务 effect。
3. 三个超过 1000 行的测试文件已按 DDL、mandatory binding/SMT claim、registration identity
   职责拆分，六个目标文件均低于治理阈值。

真实 PostgreSQL 55432、默认全量、Ruff、Bandit、generated index、架构门禁和仓库 quality profile
均已通过。本轮没有增加未来功能、数据库迁移或兼容分支。

## Critical：SMT 生产 handoff 闭环

### 根因

- production repository、route filter、open-session recheck 与开发 seed 仍使用已退休的
  `SMT_SORTING_INBOUND@2026-06-21.p1`。
- generated `smt_sorting_inbound@smt_sorting_inbound.v1` Definition 未声明 route service 强制要求的
  source/target resource boundaries。
- `WorkLineService` 仍保留旧 SMT 无 binding 激活分支。
- PostgreSQL helper 注入 `SelectedRouteService`，绕过 production candidate、manifest boundary 和
  ECS probe，导致验收伪绿。

### 修复

- candidate query、route filter、open-session recheck、NG reason 与 seed 统一使用 generated
  `DEFINITION.plugin_key/contract_version`。
- Definition 声明 `SOURCE_STATION_A/B`、`TARGET_STATION` rack positions，以及
  `SORTING_INBOUND_SOURCE/TARGET` resource boundaries。
- 删除旧常量、registry fallback 与 `WorkLineService` legacy activation/schema 分支。
- 开发 seed 先创建 inactive WorkLine，再经正式 `WorkLineService.activate` 创建 mandatory binding；
  dry-run 保持 inactive。
- production ECS probe 使用 `httpx.AsyncClient(trust_env=False)` 直连设备端点，避免宿主
  `HTTP(S)_PROXY` 重定向。
- PostgreSQL helper 改走真实 activation、production route/repository 与 loopback HTTP ECS status
  probe；性能批次通过单调 route priority 明确选择当前在线的真实候选，不注入 route stub。
- 新增静态 guard，拒绝旧 plugin key/version 在 `src/` 与 `scripts/` 回流。

### 真实闭环证明

- 旧 identity candidate 数量为 0。
- generated WorkLine 从 inactive 正式激活并固定 binding 后，可经 production request/claim 创建
  Session、ExecutionSession、ExecutionWorkItem 与 RuntimeInbox。
- `UNKNOWN_SOURCE` 进入 `MANUAL_HOLD / SOURCE_BOUNDARY_INVALID`，证明 manifest boundary admission
  未被绕过。
- callback success、device failure、recovery、重复处理与事务回滚继续通过真实 generated
  attempt 链。

## Important 1：COMMAND_RESULT 命令权威

### 根因

- context loader 会按 payload `command_code` 查命令并回填缺失的 Inbox `command_id`。
- `accept_command_result` 在显式 ID/code 不匹配后仍可按 trace fallback，并保留
  `system-capability:` compatibility 特判。
- bridge 只使用持久命令的 `task_type`，未校验 persisted code、correlation、workline/session owner、
  plugin identity 与 binding。

### 修复

- `COMMAND_RESULT` 禁止 payload command-code lookup 和 command-id hydration。
- 仅允许正整数 `command_id` 或显式 `workline-session:<session_code>` correlation 解析 owner；
  删除 trace fallback 和旧 compatibility 特判。
- bridge 校验以下持久化事实必须同时一致：
  - `DeviceCommand.command_code == payload.command_code == Session.awaiting_device_command_code`；
  - command、Inbox、Session correlation；
  - command、Inbox、Session、WorkLine owner；
  - Inbox session ID；
  - command、Session、binding 的 plugin key/version；
  - Session binding ID。
- 缺 ID、未找到命令、ID/code 或 owner 串线分别产生稳定
  `COMMAND_ID_MISSING`、`COMMAND_NOT_FOUND`、`COMMAND_RESULT_CORRELATION_MISMATCH`，不得覆盖诊断，
  不创建业务 effect。
- late-result 判断把无命令但仍匹配当前 wait 的 callback 留给 generated dispatcher，确保上述诊断可持久化。

### PostgreSQL 权威链证明

新增用例逐一经过
`accept_command_result → load_related_entities → claim_for_processing → process_claimed`：

- 缺失 `command_id` 保持 `None`，得到 `COMMAND_ID_MISSING`；
- 不存在 ID 得到 `COMMAND_NOT_FOUND`；
- 存在但属于其它 command/workline/plugin 的 ID 得到
  `COMMAND_RESULT_CORRELATION_MISMATCH`；
- 三种场景均只有允许的 Inbox/Session 乐观锁记账变化，业务 effect 写集保持不变。

## Important 2：测试职责拆分

| 原文件 | 拆分后行数 | 新职责文件 | 行数 |
| --- | ---: | --- | ---: |
| `test_workline_migration_inventory_postgresql.py` | 808 | `test_runtime_plugin_binding_ddl_postgresql.py` | 312 |
| `test_plugin_binding_runtime_wiring.py` | 940 | `test_runtime_plugin_binding_required.py` | 195 |
| `test_runtime_extension_index_generation.py` | 988 | `test_runtime_extension_registration_identity.py` | 83 |

移动范围：

- 完整 mandatory plugin binding DDL invariant PostgreSQL 合同；
- 4 个 mandatory binding 用例与 1 个 SMT claim fail-closed 用例；
- 4 个 plugin digest / registration identity 用例。

所有原断言均保留；新增职责守卫固定六个文件存在且每个低于 1000 行。

## TDD 证据

- SMT route RED：正式 generated identity 候选为 0，boundary 为
  `PLUGIN_CONTRACT_INVALID`，旧 helper 仍依赖 route stub。
- SMT route GREEN：单元/SQLite 聚焦 22 passed；production route 与 binding PostgreSQL 聚焦通过。
- command authority RED：完整 processor 路径会把缺失 ID 按 payload 回填，并允许错误 ID 借 payload
  wait 推进。
- command authority GREEN：直接 authority 8 passed；真实 PostgreSQL 三类负向子场景通过且零 effect；
  既有 COMMAND_RESULT 链 282 passed。
- test split RED：职责守卫因三个新职责文件缺失而失败。
- test split GREEN：职责守卫、topology 与拆分前后聚焦组合 99 passed；新 DDL PostgreSQL 1 passed。
- 性能 fixture RED：首次完整 PostgreSQL 组合为 21 passed / 2 failed；production candidate 稳定排序
  选中前一条已关闭 ECS 端口的 WorkLine。
- 性能 fixture GREEN：当前 route 使用单调优先级后，generated attempt median 为 29.131ms
  （预算 500ms），100-item recovery 通过；最终 PostgreSQL 组合 23/23 passed。
- 默认全量首轮：4250 passed、5 skipped、1 个陈旧 monitor boundary 断言失败。
- 更新断言后第二轮：4256 collected，4251 passed、5 skipped。

## GitNexus 影响分析

修改符号前已执行 upstream impact analysis：

- route boundary、NG reason 与 route resolver：`HIGH`；
- `WorkLineService` activation compatibility 清理：`CRITICAL`；
- seed sync：`HIGH`；
- command-result bridge：`MEDIUM`；
- context loader、accept service、session resolver：`LOW`；
- late-result detector：`MEDIUM`；
- 测试拆分与性能 fixture：`LOW`。

用户已预授权 HIGH/CRITICAL 风险修复；实现严格限定在终审指出的生产闭环、命令权威和测试治理范围。
最终 staged detect：32 个文件、87 个符号、1 条受影响执行流，风险 `MEDIUM`；唯一 execution flow 为
`Resolve_route → _route_config`，changed symbol 是终审预期的 `_ordered_config_candidates`。

## 最终验证

- 真实 PostgreSQL 55432 完整组合：
  - migration inventory 12；
  - mandatory binding DDL 1；
  - production SMT route 1；
  - command authority 1；
  - binding 1；
  - callback/recovery/rollback 4；
  - performance 3；
  - 合计 `23 passed`，225.30 秒。
- 默认全量：`4251 passed, 5 skipped`，458.81 秒。
- collect-only：`4256 tests collected`。
- 测试 topology + 职责守卫：`7 passed`。
- 拆分文件聚焦：`99 passed`。
- COMMAND_RESULT 既有链：`282 passed`。
- generated extensions `--check`：
  - workline plugin count 2，digest
    `e3adadf43b0c8ac61a2cfacf95bde685b74c7817734de876735c59d55f97c9e7`；
  - system capability count 9，digest
    `0df4e015e59583ec6289088ef031244766ed9b15d16f6bf69aba30ac435358c5`。
- Ruff format：1109 files already formatted。
- Ruff lint：All checks passed。
- Bandit：108063 行，0 issue。
- `./scripts/git-quality-gate.sh --profile quality`：passed。

## 变更边界

- 未新增数据库 migration。
- 未修改 archive。
- 未提交 `AGENTS.md`、`CLAUDE.md`、`.serena/` 或其它索引生成文档。
- 未保留旧 identity、route stub、unbound seed 或 activation compatibility。
- 未降低 performance budget、生产 boundary admission 或 command authority 校验。
