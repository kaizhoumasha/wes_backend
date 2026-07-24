# T10 实施报告：`notify_pkg_binding` typed EFFECT 硬切换

## 结论

`notify_pkg_binding` 已硬切换为唯一 `wms.fulfillment.notify_pkg_binding@v1` typed EFFECT。粗分机
material-flow 现在只产生 `RuntimeIntent.system_capability`；operation-owned gateway/adapter 冻结 canonical payload、
Provider profile、endpoint binding、credential reference 与 dispatch identity，orchestration Service 在调用方事务内复用
T8 双账本写入口，创建唯一 `RuntimeIntentLog(PROPOSED) + SystemOutbox(NEW)`。

旧 family Port 料盘绑定方法、旧 result、external contract catalog 字符串、通用 `WMS_FULFILLMENT` target/config、
EXTERNAL_REQUEST 消费者、旧 mock route、测试与活动 inventory 行已删除，不保留 alias、delegate、fallback 或双运行。
其它 fulfillment operation、T11 metrics、Jenkins 和 GitLab 均未实施。

## 合同与稳定 identity

- Definition：`EFFECT + OUTBOX_ASYNC`，输入 `NotifyPackageBindingOperationRequest`，输出只表示 durable acceptance；
  admission 要求 package/pallet 本地物理事实已记录，并冻结 `fact_version`。
- Capability identity：`wms.fulfillment.notify_pkg_binding@v1`。
- Business operation key：`provider_code:package_id:pallet_id`；dispatch key：
  `wms-notify-pkg-binding:{provider_code}:{package_id}:{pallet_id}`。二者均不依赖瞬时 request/correlation。
- 出站 gateway 冻结 `WMS_PACKAGE_BINDING` endpoint、Provider profile、credential reference 与 canonical payload/hash；
  endpoint rotation 不改写已冻结 outbox，只影响新业务 identity。
- 粗分机 runtime/preview/plugin generated definition 均使用 typed operation identity，旧字符串 effect 合同归零。
- 新 preparation Service 已从 orchestration services `__init__.py` 导出。

## Callback reducer 与异常时序

operation callback adapter 只把 typed result 映射为 T8 reducer event，不直接写业务状态：

- callback-before-response 与重复 callback 共享同一 reducer，terminal 状态单调且 evidence 幂等；
- terminal 后迟到矛盾结果只追加 evidence/open reconciliation case，不反转完成态；
- transport timeout 先收敛为 `UNKNOWN/RECONCILING`，后续成功 callback 保留完成证据，再由显式 reconciliation
  闭合为 `COMPLETED`；
- `AFTER_SEND` 崩溃保持 sender 调用一次，lease recovery 不盲发。

## 遗留与 inventory 归零

T10 inventory 共删除 19 行：
`NBWMS-003/007/010/018/024/025/036/039/042/045/049/050/053/055/060/106/113/116/118`。
其中 `NBWMS-042` 仅从已完成 T10 operation 的活动迁移清单移除；没有借此实现 T11 指标。

全仓扫描后，旧 Port method、旧 result、`WMS_FULFILLMENT_URL`、旧 target code 与旧 mock path 的字面量只保留在
`test_notify_pkg_binding_legacy_cutover.py` 的禁止回归规则中。`WMS_FULFILLMENT` 作为 typed envelope 的
operation domain 仍保留，它不是已删除的字符串路由 target/config。

## TDD 与回归

- 首轮新增合同/消费者/reducer/删旧集合：`14 failed`，失败原因与缺少新 definition、adapter、handler、callback
  bridge 及仍存在旧链一致；最小实现后核心集合通过。
- 最终全部变更面非重测试：`214 passed`。
- 历史文档与 legacy 归零门禁：`16 passed`。
- 测试拓扑：`6 passed`；默认显式收集：`3802 tests collected`。
- extension generator `--check`：workline plugin count 1，system capability count 6，digest 稳定。
- 初次扩大回归 `442` 项中 `439 passed`，仅发现 provider 字段和生成索引数量的 3 处旧断言；
  修正后对应参数化/索引定点回归 `5 passed`，随后完整变更面集合重新通过。

## PostgreSQL integration / resilience

使用临时 `timescale/timescaledb:latest-pg17` 容器、独立端口和测试凭据运行，退出时删除精确容器。
T10 定向 integration/resilience 最终 `2 passed, 4 deselected`：

- 同一稳定 business identity 只保留一条 RuntimeIntentLog 与一条 SystemOutbox；
- endpoint rotation 只作用于新 intent；
- `AFTER_SEND` crash 收敛为 `UNKNOWN/RECONCILING`，重启后不盲发。

首轮 PostgreSQL 测试发现 provider snapshot 使用 `WMS`，而平台 execution identity 约定要求
`RUNTIME/runtime`；按实际 admission 栈修正 adapter 并补充单测后通过。

## 质量与影响分析

完整 `./scripts/git-quality-gate.sh --profile quality` 通过：

- 1043 个文件格式检查、Ruff、Bandit（0 issues）；
- runtime toggle/readiness/production closure；
- 345 项 runtime contract guardrails；
- 11 项 process naming；
- import-linter；
- architecture enforced 0 violations；
- 测试拓扑 6 项。

修改前 GitNexus upstream impact 无 HIGH/CRITICAL。生产 symbol 均为 LOW；受影响测试辅助函数最高 MEDIUM
（6 个直接调用）。新文件 symbol 在增量索引中部分返回 UNKNOWN，已刷新当前 worktree GitNexus index 并记录，
没有跳过已有 symbol 的影响分析。

提交明确排除用户维护中的 `AGENTS.md`、`CLAUDE.md`。最终 staged GitNexus detect 为
`58 files / 12 indexed symbols / 0 affected processes / LOW`；本地增量图主要识别到历史文档 section，
生产实现范围同时由逐 symbol 写前 impact、聚焦测试、PostgreSQL 与完整 quality 门禁覆盖。提交 hash 由任务回执记录。

## Review P1：cleanup matrix 同步

architecture review 发现 `legacy-cleanup-matrix.csv` 保留 599 条，而当前 `parse_entries()` 返回 605 条。
精确差集是 T10 新增的 6 个顶层测试符号：

- `test_notify_pkg_binding_typed_effect_consumer.py` 的 typed consumer、稳定 dispatch identity、preview identity 3 项；
- `test_notify_pkg_binding_callback_reducer.py` 的 callback-before-response/duplicate、迟到矛盾、timeout-success 3 项。

按仓库唯一生成入口 `uv run python scripts/generate_legacy_matrix.py` 重建 CSV 为 605 条，没有手工编辑 CSV，
也没有修改生成器、allowlist 或 guard。同步 Markdown 派生摘要后，最终统计为 163 个 test、336 个 rebuild、
252 个 keep-contract、263 个 phase5-tech、223 个 phase2、153 个 workline_runtime entry。

六项均为 `phase4_carrier=False`，所以 business closure ledger 的 110 条 entry 集合与 disposition 统计不变，
无需修改 `business-legacy-absence-ledger.csv/.md`。定向 matrix/closure 合同由 `2 failed, 19 passed`
转为 `29 passed`，final business absence gate 通过；完整 `tests/architecture` 为 `390 passed, 1 skipped`，
完整 quality profile 通过。P1 staged GitNexus detect 为
`3 files / 12 document symbols / 0 affected processes / LOW`。
