# Task 3：共享 EXTERNAL_HTTP NONE/HMAC 实施报告

## 结论

Task 3 已在共享 `EXTERNAL_HTTP` frozen binding、SystemOutbox、canonical dispatcher 和既有
material-flow Outbox 创建边界中完成 `NONE | HMAC_SHA256` 封闭认证扩展。

- `NONE` 只接受 `isolated_lan + 空 credential_reference`。
- `HMAC_SHA256` 保持版本化 credential reference、timestamp、nonce、signature 合同。
- 两种认证继续通过同一个 canonical dispatcher 和 sender。
- WMS compiled profile 是 endpoint、认证方案和网络信任事实的唯一真源。
- 恢复/重试只读取 SystemOutbox 已冻结字段，不读取 live profile。
- 未实现 QUERY executor、EFFECT Gateway/Handler/IntentAdapter、status scanner、WMS sender、
  第二 HTTP client/pool 或第二张 Outbox。

## 主要变更

### 共享绑定与 Provider 映射

- `src/app/sys/external_http_binding.py`
  - 将认证闭集扩展为 `NONE | HMAC_SHA256`。
  - 新增并冻结 `network_trust_mode`。
  - 统一校验 NONE/HMAC 与 network trust/credential 的组合不变量。
  - profile hash 和 persisted binding 均包含 network trust。
- `src/app/runtime/system_capabilities/wms/provider_catalog.py`
  - 从 Task 2 compiled profile 纯映射认证和 network trust。
  - 从 compiled operation endpoint 构造共享 EndpointRegistry，不保留可选 registry 或 fallback。

### SystemOutbox 与数据库

- `src/app/sys/models/outbox.py`
  - 新增 nullable `network_trust_mode`。
  - 应用校验、不可变字段和 update schema 同步纳入该字段。
- `src/app/sys/repositories/outbox_repository.py`
  - repository update 冻结 `network_trust_mode`。
- `src/app/runtime/capabilities/material_flow/station_lease_service.py`
  - Outbox 创建时持久化 `idempotency_key` 和 `network_trust_mode`。
- `migrations/versions/20260729_1826_36aa187238cc_allow_external_http_none_auth.py`
  - 由 `uv run alembic revision` 生成，revision 为 `36aa187238cc`。
  - PostgreSQL constraint 接受且仅接受：
    - `NONE + isolated_lan + NULL credential_reference`
    - `HMAC_SHA256 + isolated_lan/authenticated_network + 版本化 credential_reference`
  - 系统未发布，不包含旧 HMAC-only 数据迁移兼容路径。
  - downgrade 恢复旧 HMAC-only constraint 并删除新列。

### Canonical dispatch 与恢复

- `src/app/sys/canonical_dispatch.py`
  - NONE 不接收 secret/timestamp/nonce，不计算 signature，不产生任何认证 header。
  - content hash、operation identity、idempotency key 和既有 canonical body 合同保持不变。
  - HMAC 继续生成同一组 credential/timestamp/nonce/signature header。
- `src/app/sys/services/outbox_delivery.py`
  - 只按共享 `auth_scheme` 分支；NONE 不调用 credential provider。
  - frozen binding 从数据库字段恢复，同一 sender 发送。

## GitNexus 影响分析

修改前已执行 upstream impact analysis，并在实施前报告风险：

| Symbol | 风险 | 受影响符号 | 直接调用者 | 处理 |
| --- | --- | ---: | ---: | --- |
| `FrozenExternalHttpBinding` | CRITICAL | 236 | 16 | 用户已预授权，先报告后实施 |
| `ExternalHttpDispatchRequest` | CRITICAL | 204 | 32 | 用户已预授权，先报告后实施 |
| `SystemOutbox` | HIGH | 133 | 24 | 用户已预授权，先报告后实施 |
| `dispatch_external_http` | HIGH | 27 | 14 | 保持共享 dispatch flow |
| `SystemOutboxUpdate` | CRITICAL | 222 | 24 | 同步 update schema 冻结字段 |
| `SystemOutboxRepository.update` | LOW | 5 | 1 | 同步 repository 不可变字段 |

## TDD RED/GREEN 证据

### 第一组：binding/model

- RED：`6 failed, 33 passed`
  - NONE 尚不受支持。
  - network trust 未冻结。
  - HMAC 缺 credential 的错误合同不满足。
  - compiled endpoint 未成为唯一冻结输入。
- GREEN：binding、operation catalog、endpoint compiler 合计 `86 passed`。

### 第二组：数据库/migration

- RED：迁移合同 `1 failed`，证明 revision/column/constraint 尚不存在。
- GREEN：
  - 迁移合同 `1 passed`。
  - `uv run alembic heads`：`36aa187238cc (head)`。
  - 隔离临时 PostgreSQL fresh upgrade、constraint 正反例、downgrade/re-upgrade：`2 passed`。

### 第三组：canonical headers/dispatcher

- RED：`7 failed`，覆盖 NONE request、credential provider 旁路缺失及 frozen 字段恢复缺失。
- GREEN：canonical dispatch、frozen delivery、outbox delivery、transport result 合计 `46 passed`。

### 第四组：recovery

- RED：Station 创建边界先暴露 `idempotency_key` 未持久化；随后验证 `network_trust_mode` 缺失。
- GREEN：`1 passed`，证明：
  - idempotency 和 network trust 均持久化；
  - profile endpoint 轮转后旧 Outbox 仍使用旧冻结 URL；
  - 恢复过程不调用 live profile builder；
  - NONE 恢复过程不读取 credential provider。

## 本次 changed-branch 定点覆盖映射

本任务未扩张到四个共享模块的无关历史分支，也未新建 T0 coverage manifest。本次新增/修改的
NONE/HMAC、constraint、dispatcher、recovery 分支均有定点测试：

| 变更分支 | 正向/反向覆盖 |
| --- | --- |
| NONE frozen binding | isolated LAN 正例；authenticated network、携带 credential 负例 |
| HMAC frozen binding | 版本化 credential 正例；缺失/非版本化 credential 负例 |
| 应用 SystemOutbox 不变量 | NONE 正例及非法 network trust/credential 组合负例 |
| PostgreSQL constraint | NONE/HMAC 正例；NONE 非 isolated、NONE 带 credential、HMAC 缺 credential、未知 scheme 负例 |
| canonical NONE request | exact header 正例；secret/timestamp/nonce 三类认证材料逐项拒绝 |
| canonical HMAC request | credential/timestamp/nonce/signature header 和签名 metadata 回归 |
| delivery auth 分支 | NONE credential provider 零读取；HMAC exact credential version 和 revoked/error 回归 |
| compiled profile 映射 | NONE/network trust/endpoint 仅来自 compiled profile；无 runtime fallback |
| crash recovery | profile 轮转后使用数据库 frozen URL，live profile 零读取 |
| migration lifecycle | generator revision、fresh upgrade、downgrade/re-upgrade、无数据兼容 SQL |

共享模块整体 coverage 快照为 `118 passed`，整体 branch coverage `82%`；该数值包含本任务之外的历史分支，
不作为 Task 3 扩张测试范围的依据。后续若 T0 冻结 manifest 将这些共享模块整体列为 100% 目标，应在总门禁任务中
按 manifest 补齐，不能通过 omit、`pragma: no cover` 或弱化断言规避。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| Task 3 定向回归 | `134 passed` |
| WMS contracts | `381 passed` |
| WMS integration 单元域 | `113 passed` |
| Workline runtime | `966 passed` |
| 隔离 PostgreSQL migration/constraint | `2 passed` |
| 测试拓扑 guardrail | `6 passed` |
| collect-only | `4287 tests collected` |
| 默认全集（仅执行一次） | `4282 passed, 5 skipped`，退出码 0 |
| 完整 quality profile（仅执行一次） | Ruff format/check、Bandit、质量/架构/import guardrail 全部通过 |
| Alembic head | `36aa187238cc (head)` |

## GitNexus 提交前检测说明

提交前已执行 `gitnexus_detect_changes(scope="all")`。按索引仓库名 `wes_backend` 查询时返回
`No changes detected`，与当前 worktree 的本地 `git diff` 不一致；改用当前 worktree 绝对路径查询时，
LadybugDB 报告索引存储版本 `42` 与当前运行时版本 `40` 不兼容。因而本报告不把该工具结果误写为“零影响”。
提交范围改由修改前逐 symbol impact analysis、本地完整 diff、定向回归、默认全集和 quality profile 共同验证。

## 范围边界

- 没有 WMS 专用 dispatcher、sender、Outbox 或 credential fallback。
- 没有独立 NONE feature flag。
- 没有实现 Task 4/Task 5 的 QUERY/EFFECT 业务执行、scanner 或双 client/pool。
- callback 等其他 Provider/域的认证策略未被关闭。

---

## 最小复审修复：共享 GET/POST 与 compiled profile 冻结映射

### 修复结论

- `ExternalHttpBindingDefinition`、`ExternalHttpTargetSnapshot` 与
  `ExternalHttpDispatchRequest` 的 method 闭集统一扩展为 `GET | POST`，闭集外方法继续 fail closed。
- canonical dispatcher 对 GET 使用 query params 且不发送 body；POST 继续发送原冻结 canonical bytes。
  NONE/HMAC 仍走同一 typed request、同一 sender 和同一 payload hash 合同。
- 新增 compiled WMS profile 到 frozen binding 的纯结构映射：
  - 19 项 QUERY 冻结 compiled endpoint、静态 method/budget、auth、network trust 与 credential reference。
  - 7 项异步 EFFECT status 冻结 compiled status endpoint，并明确使用 GET。
  - 同步 EFFECT 不得获得 status binding。
- 未实现 QUERY/EFFECT 运行、业务 parser、WMS 专用 dispatcher 或 sender，Task 4/Task 5 边界未被提前侵入。

### GitNexus 影响分析

修改前已执行 upstream impact analysis，并在实施前报告 HIGH/CRITICAL 风险：

| Symbol | 风险 | 受影响符号 | 直接调用者 |
| --- | --- | ---: | ---: |
| `ExternalHttpBindingDefinition` | CRITICAL | 236 | 16 |
| `ExternalHttpTargetSnapshot` | CRITICAL | 237 | 17 |
| `ExternalHttpDispatchRequest` | CRITICAL | 204 | 32 |
| `_send_external_http` | MEDIUM | 6 | 6 |
| `_external_http_effect_profile` | HIGH | 114 | 1 |
| `_external_http_effect_binding` | LOW | 26 | 1 |

### TDD 证据

- RED：新增合同首次运行 `33 failed, 39 passed`，明确暴露 POST-only method、GET projection
  与 compiled profile 冻结映射缺失。
- GREEN：同一组合同 `72 passed`。
- 结构合同覆盖全部 19 项 QUERY 与 7 项异步 EFFECT status，并包含同步 EFFECT 负例。

### 回归与门禁

| 验证 | 结果 |
| --- | --- |
| 共享 system capabilities + sys | `242 passed` |
| WMS integration contracts | `408 passed` |
| Workline runtime | `966 passed` |
| 测试拓扑 guardrail | `6 passed` |
| collect-only | `4321 tests collected` |
| 默认全集（当前代码仅执行一次） | `4314 passed, 5 skipped, 2 failed`；失败均为上一轮新增 station recovery 测试未同步 legacy matrix |
| legacy matrix 定点修复 | 仓库生成器仅补 1 条 CSV；两项原失败通过，完整 matrix 合同 `24 passed` |
| 完整 quality profile（仅执行一次） | Ruff format/check、Bandit、runtime contracts、import-linter、架构与拓扑门禁全部通过 |

默认全集的两项失败具有同一根因：
`test_station_outbox_persists_none_network_trust_for_restart_recovery` 在上一轮 Task 3 新增后，
legacy audit trace CSV 与 Markdown 派生统计未刷新。本轮使用
`scripts/generate_legacy_matrix.py` 同步唯一缺失条目，并定点验证全部 24 项矩阵合同；
未重复执行默认全集，也未把该生成物漂移误归因于 GET/POST 生产代码。

提交前再次执行 `gitnexus_detect_changes(scope="all")`：索引仓库名仍返回
`No changes detected`，当前 worktree 绝对路径仍因 LadybugDB 存储版本 `42/40`
不兼容而无法分析。该结果与本地 diff 不一致，不能作为零影响证明；本轮仍以修改前的逐 symbol
impact analysis、最终 diff、定点回归和完整 quality profile 作为提交范围依据。
