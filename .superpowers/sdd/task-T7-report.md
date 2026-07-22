# T7 实施报告：rough-sorter 首个 QUERY 切片迁移

## 状态与边界

T7 代码迁移已完成：rough-sorter 已切换到通用 `wms.inventory.query_inventory@v1` 与 T4 纯 policy，旧专用
capability 的 package、identity、routing、binding、generated index 和 import 已物理删除；不存在 fallback、alias、
dual-run 或 compatibility adapter。本任务没有实现 T8+ EFFECT lifecycle。

生产启用仍被真实 readiness 授权严格阻止。当前工作树没有可核验的文件型报告；读取本地持久化数据库时
`localhost:5432` 拒绝连接，无法证明存在同一 immutable report ID 的 READY + GO。因此本次只完成结构切换并把
production startup/release 配置为 fail closed，绝不以测试报告或假值放行。

## 实现结果

- 新增通用 inventory QUERY System Capability Definition/Handler，identity 唯一来自 T3
  `OPERATION_IDENTITY`，输入输出使用 `InventoryQueryOperationRequest/Result`，required Port 为
  `InventoryQueryOperationPort`。
- Handler 仅负责 typed Port 与 System Capability 封闭 outcome 的薄适配；成功、业务拒绝、可重试技术失败、
  不可重试技术失败和合同失败保留稳定分类，不包含 rough-sorter admission 规则。
- rough-sorter 在 PICK result 路径构造 typed query，通过 attempt-scoped Gateway 获取 decision evidence，再把封闭
  outcome 归一化为 T4 `RoughSorterInventoryQuerySnapshot` 并调用纯
  `decide_rough_sorter_inventory_admission`。动作选择只依赖 T4 的 ADMIT/REJECT/HOLD。
- runtime 默认 profile 改由 WMS material-flow 版本生成；插件允许能力与两个 generated index 全部切换为通用
  identity。
- 新增持久化 cutover readiness repository/service：production 只读取目标 production profile + operation 的最新
  append-only report，并校验数据库 metadata、report 内容摘要、READY verdict、同 report ID approval 和 GO；缺失、
  不一致、损坏、数据库异常均拒绝。
- `register_init` 在 Redis 和 serving 前执行 production gate；Jenkins 在 migration 后、新容器启动前执行同一 gate
  CLI。dev/test 不声明 production 授权，也不提供任何旧查询路径。
- 删除旧专用 capability 五个源码文件、旧共享 helper 和两组专用 capability/policy-boundary 测试；业务 policy
  覆盖由 T4 独立测试保留，通用 capability、rough-sorter 接线、architecture 零引用和发布门禁均有新合同测试。
- 北向 WMS 清点表删除全部 T7 专用引用行；architecture guard 允许历史清点 scanner 自身保存 legacy token，
  但扫描 `src/` 与其余可执行测试时要求专用 package/import/identity 为零。

## TDD 记录

1. RED：通用 capability/readiness 模块不存在，目标测试在 collection 阶段失败；实现最小 Definition、Handler 和
   DB-backed readiness service 后 GREEN。
2. RED：生产 startup 在 readiness 拒绝时仍可进入 serving；在 `init_db` 后、`init_redis` 前接入同一 gate 后
   GREEN。
3. RED：发布脚本与 Jenkins 顺序缺少 gate；新增 fail-closed CLI 并固定 migration → gate → start 顺序后 GREEN。
4. RED：旧 package、旧 identity/import 与 generated index 仍存在；物理删除、迁移测试 fixture、重生成 index 并
   清理清点表后 architecture guard GREEN。
5. PostgreSQL 性能重测试暴露陈旧 `WmsTypedPortService.cache` fixture 和旧 service singleton 注入；改为注入
   attempt-scoped generic inventory factory、隔离 evidence writer/queue gateway 和 sandbox credential 后，真实通用
   HTTP transport/evidence 路径 GREEN。

## GitNexus

- 变更前影响分析：旧 Handler LOW；旧 Input/Output MEDIUM；`_pick_result_decision` LOW；`decide`、
  `create_attempt_runtime` MEDIUM；plugin Definition、`register_init` LOW。没有修改唯一 HIGH 结果
  `_configure_attempt_runtime_ports`。
- 测试 composition helper 的影响分析均为 LOW；最高生产符号风险为 MEDIUM，已由 plugin、runtime、profile、
  generated index、readiness 与 PostgreSQL production-chain 回归覆盖。
- 提交前 CLI `detect-changes --scope staged` 检出本任务 27 个文件、35 个图谱符号、5 条受影响 execution flow，
  综合风险 MEDIUM；flow 均以 `register_init` 为变更节点，已由 production startup 与 quality runtime contracts
  覆盖。MCP 对本 worktree 的 LadybugDB 存储版本 v42/v40 不兼容，故按项目既有做法使用同一刷新索引的 CLI。
  工作树原有的 `AGENTS.md` / `CLAUDE.md` 改动未进入 staged scope，也不会纳入本提交。

## 验证

- T7、T4 policy、rough-sorter、runtime profile、generated index、deployment、architecture 与 topology：
  `371 passed`。
- 隔离 PostgreSQL 17 production-chain 性能测试：`1 passed in 62.75s`；容器在测试后已停止并自动删除。
- 默认测试收集：`3583 tests collected`。
- `ruff format --check .`：982 files already formatted；`ruff check .`、`git diff --check` 通过。
- `./scripts/git-quality-gate.sh --profile quality`：通过；Bandit 0 issue、348 runtime contracts、11 process
  naming、import-linter、enforced architecture guardrails 与 test topology 全部通过。
- 以 `APP_ENV=prod` 执行真实 release gate 返回 exit code 1，输出
  `inventory QUERY cutover readiness gate blocked: OSError`，证明数据库/真实授权不可用时不会放行。

## Concern / Blocker

- 当前无法访问应承载 T6 append-only readiness report/approval 的数据库，因此没有实际报告 ID、READY verdict 或
  GO approver 可供核验。production 发布必须先在目标数据库生成并审批真实报告，再重新执行 release gate；在此
  之前启动与发布会按设计失败。
- dev/test 会跳过 production readiness 查询，仅用于开发和验证；这不是旧 capability fallback，所有环境都只包含
  通用 query/policy 执行路径。
