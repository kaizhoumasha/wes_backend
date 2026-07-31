# Task 2 实施报告：typed Provider profile 与 endpoint 编译器

## 结论

Task 2、独立复审与最终复审修复均已完成。当前分支为 `feature/wms-full-factory-integration`。部署入口收敛为绝对路径
`WMS_PROVIDER_PROFILE_FILE`；同一次启动只编译一个 profile，catalog、conformance、readiness 与运维基线均显式消费
该 compiled profile。旧模块级 profile、旧 active credential selector、旧 endpoint 环境变量及 fallback 已从 tracked
tree 物理删除。

本任务没有实现 T3 的 QUERY/EFFECT transport、EXTERNAL_HTTP sender、Outbox、数据库约束或 Celery worker。真实 QUERY
port 在未注入 compiled endpoint 时仅于实际 `execute()` 调用 fail closed，不阻断不使用 WMS QUERY 的插件 attempt
初始化。

## 主要实现

- strict、frozen Provider profile 与唯一 YAML parser：精确覆盖 35 个 operation，拒绝重复键、缺失项、未知项和非法认证
  组合。
- endpoint 编译器：只接受合法 bare HTTP(S) origin 和安全相对路径；typed placeholder 逐段编码；递归解码后拒绝
  dot-segment、slash + dot 逃逸和非法 hostname；渲染后复核 scheme/origin/query/fragment 不变量。
- 稳定 profile revision、profile digest、operation endpoint digest，以及 WES/fulfillment 两条 lane 的同源 readiness。
- 启动装配只读取、解析并编译一次 profile；派生 catalog，并将同一 catalog 注入运维 Repository，不创建 HTTP client、
  连接池、breaker 或 transport。
- conformance manifest/report 的构建与验签均要求显式 compiled profile，不再读取模块级默认。
- effect status、QUERY sandbox、feasibility probe、repository 与既有测试/重测试调用方均改为显式 compiled
  profile/catalog。
- 全树门禁通过 `git ls-files` 扫描退役 endpoint 变量和 active HMAC selector，防止配置、脚本或文档回流。
- 无消费者的 `runtime/system_capabilities/wms/effect_binding.py` lazy facade 已物理删除；catalog 内显式接收
  compiled profile 的真实实现保持不变。

## 独立复审项关闭

| 复审项 | 结果 |
| --- | --- |
| typed `.` / `..` URL 规范化逃逸 | 已关闭；包含一次/多次 percent encoding 与 slash + dot 组合 |
| compiled profile 非唯一 active 真源 | 已关闭；catalog/conformance/readiness/startup 共享显式实例 |
| 旧 endpoint 配置残留 | 已关闭；init-env、reset fallback、配置、compose、文档及历史报告物理删除 |
| 非法 hostname 字符 | 已关闭；DNS label/IP literal 严格验证 |
| 孤立 effect-binding lazy facade | 已关闭；刷新分支索引确认 0 消费者、0 执行流后物理删除 |
| 默认全集证据 | 最终 staged 状态为 4269 passed、5 skipped、0 failed |
| 报告 statement/branch/计数 | 已更新 |

## TDD 证据

| 批次 | RED | GREEN |
| --- | --- | --- |
| 初始 profile model | 模块缺失，7 failed | 基础 7 passed；扩展后 10 passed |
| 初始 endpoint compiler | 模块缺失，19 failed | 基础 19 passed；扩展后 31 passed |
| digest/readiness | 2 failed、2 passed | 4 passed |
| startup assembly | 4 failed | 基础 4 passed；扩展后 5 passed |
| 惰性 fail-closed | attempt 初始化提前抛错，1 failed | 目标 1 passed |
| endpoint 安全复审 | 12 failed、32 passed | 44 passed |
| 双重编码 slash + dot 补强 | 3 failed、5 passed | endpoint 全集 47 passed |
| compiled profile active truth | 3 failed | 3 passed |
| feasibility probe 调用方 | collection ImportError | 18 passed |
| 退役配置 tracked-tree guard | 2 failed | 2 passed；扩展 active selector 后继续通过 |
| effect-status/external profile 迁移 | 24 failed、386 passed | 108 个精准节点通过 |
| repository/status-service 迁移 | 39 failed、9 passed | 相关调用方全部通过 |
| 孤立 effect-binding facade | 文件仍存在，架构守卫 1 failed | facade 物理删除；目标 1 passed、相关架构文件 13 passed |

## GitNexus 影响分析

- `Settings`：CRITICAL，367 个受影响符号、73 个直接依赖；实际改动限定为退役 selector 删除和单一绝对 profile
  路径。
- `build_active_wms_provider_profile`：CRITICAL，166 个受影响符号、4 个直接依赖；旧构建器和模块级默认已删除。
- `resolve_wms_operation_binding`：HIGH，25 个受影响符号、6 个直接依赖；改为显式 catalog。
- `build_wms_effect_status_binding`：CRITICAL/HIGH（索引批次不同），53 个受影响符号、4 个直接依赖；改为显式
  compiled profile。
- `build_workline_runtime_services`：HIGH，8 个受影响符号、3 个直接依赖、1 条执行流；新增可选 compiled profile
  注入，未注入仍按 T3 边界 fail closed。
- `RuntimeInboxProcessorBridge.create_attempt_runtime`：CRITICAL，41 个受影响符号、10 个直接依赖；仅将默认 profile
  contract version 切换到唯一新版本。
- 新增/未入索引的 endpoint、startup、probe、Repository 注入符号返回 `UNKNOWN`，均以定向合同测试覆盖。
- `effect_binding.py::freeze_wms_effect_binding`：旧索引曾误报 3 个已不存在的 gateway 消费者；刷新当前
  worktree 索引后，精确结果为 0 个直接/间接消费者、0 条执行流、LOW，因此选择物理删除而非保留 fallback。
- 提交前 detect 已以 staged、all 和 compare-to-HEAD 三种 scope 执行；本次删除孤立文件和新增测试未映射到
  受影响执行流，CLI 均返回 `No changes detected`。MCP runtime 为 storage v40，无法读取 CLI 生成的 v42 数据库。

HIGH/CRITICAL 影响面已按任务授权汇报后继续。worktree 的 GitNexus 数据库由 CLI 42 版生成，检测阶段使用匹配版本的
CLI。

## 验证结果

- 复审相关最终回归：471 passed。
- 版本迁移、运行时与 Celery/WMS 启动装配补充回归：370 passed。
- 默认测试套件仅运行一次：`4273 collected, 4243 passed, 25 failed, 5 skipped`。其中 3 个失败来自临时的
  `provider_catalog -> orchestration repository` 反向依赖，22 个失败来自旧 material-flow
  admission/profile version 残留。
- 默认套件发现问题后的精准修复证据：架构边界 3 passed；版本/profile 失败组 135 passed；enforced
  architecture guardrail 为 0 violations / 0 warnings。
- 最终复审定点回归：目标守卫 1 passed；完整相关架构文件 13 passed；tracked-tree guard 与相关架构文件合计
  14 passed。
- 最终复审默认全集第一次执行：`4274 collected, 4268 passed, 1 failed, 5 skipped`。唯一失败是 facade 已在
  working tree 删除但尚未 staged，`git ls-files` 仍返回旧路径，tracked-tree guard 读取该路径时触发
  `FileNotFoundError`。
- 将物理删除写入 Git index 后，最终 staged 状态默认全集：`4274 collected, 4269 passed, 5 skipped`，0 failed。
- 测试拓扑 guardrail：6 passed。
- 默认 collect-only：4273 tests。
- 变更 Python 文件 Ruff lint/format：passed。
- `git diff --check`：passed。
- 完整 `./scripts/git-quality-gate.sh --profile quality`：passed；包含 Ruff format/check、Bandit、runtime
  release/evidence/production gates、361 个 runtime contract tests、business legacy absence、process naming、
  import-linter、enforced architecture guardrails 与测试拓扑 guardrail。

## 提交

初始实现提交：`3813615d feat(wms): 实现 Provider profile 与 endpoint 编译`。

独立复审修复提交：`6d931a43 fix(wms): 收敛 compiled profile 唯一真源`。

最终复审修复将以新的中文 Conventional Commit 提交。
