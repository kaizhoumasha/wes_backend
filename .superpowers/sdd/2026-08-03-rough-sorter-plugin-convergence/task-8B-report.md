# Task 8B 实施报告

- status: `DONE_WITH_CONCERNS`
- BASE: `12d8cbb4da8c9bb7dbf19a7cb88d6d765c9d463f`
- migration: `5695afa99545`（`闭合粗分持久触发`）
- delivery: 独立正常 commit，不 Push；真实 PostgreSQL、selected HEAVY 与独立 Review 由主 Agent 执行。

## 行为闭环

- Transport：增加通用 `TRANSPORT_RESULT` evidence 与稳定 task/version identity；只有当前 `RECONCILING` 确由同 task 较低版本
  `UNKNOWN` evidence 造成时，后续确定结果才可重建 Fact。核心不解释 `NEW_IN/OLD_OUT`。
- Recovery：SDK 与 WMS wire 直接替换为单 execution `RecoveryDecidedFact`；删除批量 binding、旧 operation/class/handler 与
  `resume_action`。rough_sorter 使用内部 sealed continuation，严格执行 `CONTINUE` 必须有、`ABORT` 禁止有，并验证位置/拓扑/因果 identity。
- WMS business WAIT：先保存确定 response evidence，原 confirmation 完成，再在同一事务创建新 operation identity 的到期 follow-up；
  技术投递未知仍复用原 identity。非法 planner 输出或异常保留 evidence 并 fail closed 到 `RECONCILING`。
- Dispatcher：增加固定批量、无业务载荷的 WMS confirmation Celery scanner，固定 `wms-fulfillment` route 与 10 秒 Beat；没有
  ETA/countdown，也没有让 execution scanner 顺带派发 WMS。
- Schema：随机 revision migration 增加 Transport 字段、两条身份约束和索引，删除批量 binding 表；无开发数据迁移，downgrade 不支持。

## TDD 与验证

- RED：Transport contract `4 failed`；SDK/wire recovery 分别以缺失新符号 import error 失败；business WAIT 与 dispatcher 分别以缺失
  typed follow-up / task module import error 失败。
- GREEN：
  - `uv run pytest tests/contracts/wms_adapter tests/runtime/execution tests/api/test_wms_transport_events.py tests/api/test_qa_regression_transport_openapi.py tests/architecture/test_plugin_sdk_boundary_guardrail.py tests/deployment/test_wms_confirmation_dispatcher.py -q`：`398 passed`。
  - `uv run pytest workline_plugins/rough_sorter/tests -q`：`68 passed`。
  - `uv run pytest tests/scripts/test_select_heavy_tests.py -q`：`267 passed`。
  - staged selector 精确选择 10 个 HEAVY 文件；全 manifest `--collect-only -o addopts=''`：`76 collected`，无 collection error。
  - 范围 Ruff check/format 通过；范围 basedpyright 为 `0 errors, 0 warnings`；`uv run alembic heads` 为
    `5695afa99545 (head)`；旧符号生产残留扫描与 `git diff --check` 通过。
- 首次正常 commit hook 的 Ruff 已通过，Bandit 唯一报告新增 `assert` 的 B101 Low；已改为显式 fail-closed 分支，受影响 WMS
  `23 passed`、scoped Bandit/Ruff 通过后才重试 commit，未使用 `--no-verify`。
- 第二次 hook 输出被运行器截断，因此在同一 Git 环境单独复现 QUALITY：`3442 passed, 4 skipped` 后由既有 fulfillment Beat
  精确枚举测试识别出新增 dispatcher 未传播；补齐直接架构 owner 后，dispatcher/fulfillment 聚焦回归 `24 passed`。
- GitNexus：索引刷新后批量 upstream impact 因本机 LadybugDB storage v42 / reader v40 不兼容全部为 `UNKNOWN`；按仓库规则降级为
  精确调用点、测试 owner、旧符号残留与 diff 检查，没有把 UNKNOWN 当成风险证明。

## 自审与边界

- 未发现 compatibility alias、双路径、人工工单、通用 ledger、基础层位置业务推导或 recovery 随机 operation identity。
- Task 8C 必须基于 verified causal evidence + snapshots 稳定构造插件 continuation，并完成 Web/Celery 共用的插件 factory、WMS
  adapter/planner/session dispatcher runtime 装配与 post-commit wake；Task 8B 的独立 task 在该装配完成前不代表生产可运行。
- 实施 owner 未运行真实 PostgreSQL migration、PG tests、HEAVY 或 Docker；这些门禁不能由 FAST/collect/QUALITY 替代。
