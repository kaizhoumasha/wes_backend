# Task 2 实施报告：typed Provider profile 与 endpoint 编译器

## 结论

Task 2 已完成 Provider profile、endpoint 编译、digest/readiness 与启动装配。部署入口收敛为绝对路径
`WMS_PROVIDER_PROFILE_FILE`；旧 `WMS_SYNC_BASE_URL`、`WMS_EFFECT_STATUS_URL` 与 endpoint fallback 已从生产配置和
compose 装配中移除。

本任务没有实现 T3 的 QUERY/EFFECT transport、EXTERNAL_HTTP sender、Outbox、数据库约束或 Celery worker。真实 QUERY
port 在未注入 compiled endpoint 时仅于实际 `execute()` 调用 fail closed，不阻断不使用 WMS QUERY 的插件 attempt
初始化。

## 主要实现

- `provider_profile.py`
  - strict、frozen 的 profile 模型与单一 YAML parser。
  - 精确校验 35 个 operation identity，拒绝缺失、重复、未知 operation 与重复 YAML key。
  - 验证 `NONE + isolated_lan`、`HMAC_SHA256 + versioned credential reference`。
- `endpoint_compiler.py`
  - 校验唯一 HTTP(S) origin、相对 path 与 placeholder 精确集合。
  - 通过 typed request 字段渲染并 percent-encode path segment，不接受 Mapping、`str.format` 或 Jinja。
  - 从 Task 1 registry 派生 HTTP method、completion mode、lane、预算、分页与 result model。
  - 生成稳定 profile revision、profile digest 与 operation endpoint digest。
- `provider_readiness.py`
  - 从静态 lane 派生 WES 与 fulfillment worker 的 readiness 集合。
  - 两个进程角色共享同一 profile digest。
- `provider_startup.py`
  - 启动时只读取一次 profile 文件、编译一次 endpoint，不创建 HTTP client、连接池、breaker 或 transport。
- 配置与旧入口清理
  - `Settings`、环境模板和 compose 改用 `WMS_PROVIDER_PROFILE_FILE`。
  - catalog/startup 不再读取旧 URL。
  - T3 composition root 未完成前，旧 QUERY/EFFECT fallback 明确 fail closed。

## TDD 证据

| 批次 | RED | GREEN |
| --- | --- | --- |
| Profile model | 模块缺失，7 failed | 基础 7 passed；扩展后 10 passed |
| Endpoint compiler | 模块缺失，19 failed | 基础 19 passed；扩展后 31 passed |
| Digest/readiness | 2 failed、2 passed | 4 passed |
| Startup assembly | 4 failed | 基础 4 passed；扩展后 5 passed |
| 惰性 fail-closed 回归 | attempt 初始化提前抛错，1 failed | 实际 execute 才拒绝，目标 1 passed |

新增四个生产模块的 branch coverage 为 100%：250 statements、64 branches。

## GitNexus 影响分析

- `Settings`：CRITICAL，367 个受影响符号、73 个直接依赖；实际改动限定为旧 endpoint 字段删除和单一绝对 profile
  路径字段。
- `wms_sync_base_url`：HIGH，13 个受影响符号、3 个直接依赖；按已确认范围物理删除。
- `build_wms_effect_status_binding`：HIGH，53 个受影响符号、4 个直接依赖；移除 Settings URL fallback，要求显式
  compiled status endpoint。
- `build_inventory_query_port_factory`：LOW，4 个直接测试调用方、0 条执行流；保持 T3 未注入时实际调用 fail closed。
- 其它 catalog/startup 边界为 LOW。

worktree 的 GitNexus 数据库由 CLI 42 版生成，而当前 MCP storage 版本为 40；对新符号和测试符号的查询返回
`UNKNOWN`。旧符号使用主仓库索引完成影响分析；MCP `detect_changes(scope=all)` 同样因版本不兼容返回 LadybugDB
unavailable。随后改用与索引匹配的 CLI 对 staged scope 完成检测：32 files、39 symbols、0 affected processes、
LOW risk。`git diff --name-status` 与 `git diff --check` 复核也未发现任务外代码变更。

## 验证结果

- 新增核心合同与配置回归：83 passed。
- QUERY factory 直接调用方：44 passed。
- workline/SMT 回归：28 passed。
- 受影响 WMS/startup/config/workline 回归：477 passed、1 个过期测试断言失败；迁移为惰性 fail-closed
  断言后，相关 compiler/workline 集合 59 passed。
- legacy matrix/ledger 精准测试：10 passed；business legacy final gate passed。
- 测试拓扑 guardrail：6 passed。
- 默认 collect-only：4246 tests。
- 新模块定向 coverage：100% branch coverage。
- 全仓 `ruff format --check .`：1078 files already formatted。
- 全仓 `ruff check .`：passed。
- `git diff --check`：passed。

默认测试套件按约束只运行一次。该次运行发现 21 个 last-failed 节点：19 个 workline/SMT 节点由本分支过早
fail-closed 引起，已修复并以 28 passed 验证；另一个相关旧断言已迁移并通过。剩余 legacy matrix 失败通过正式
`scripts/generate_legacy_matrix.py` 确认 650 条机器真源后，同步 Markdown 派生计数并以 10 passed 验证。

完整 `./scripts/git-quality-gate.sh --profile quality` 最终通过，包括：

- Ruff format 与 lint。
- Bandit 104773 行扫描，0 issue。
- runtime toggle/evidence/production closure gates。
- runtime contract guardrails 361 passed。
- business legacy absence final gate。
- process naming 11 passed。
- import-linter contract kept。
- architecture guardrails 0 violation、0 warning。
- test suite topology 6 passed。

## 提交

本报告随中文 Conventional Commit `feat(wms): 实现 Provider profile 与 endpoint 编译` 一并提交。
