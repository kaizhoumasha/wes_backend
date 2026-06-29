# ADR 0001: Phase 2 Runtime 域所有权固化

**状态**: Accepted
**日期**: 2026-06-28
**适用范围**: Phase 2 launch PR (Phase 2 burn-down 前置)
**配套文档**: [`../runtime-ownership-map.md`](../../runtime-ownership-map.md), [`../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md) §9.2 + §10.3

## 背景

Phase 0 (#63) + Phase 1 Packet A/B/C/D (#64, #66) 已落地 runtime/orchestration 9 实体 + RuntimeSnapshot 合同 + 双向 guardrail (R-I3a/b/c)。但 Phase 2 launch PR 前仍有 3 个悬空问题:

1. **runtime 域代码归属未明文**:9 entity + 1 repo + 2 service 文件存在但无 ownership map,Phase 2 burn-down 814 rows 迁移时容易越界。
2. **device/callback 反向依赖 workline**:`src/app/device/services/device_command_service.py:182,393` 与 `src/app/wms_integration/services/callback_normalizer.py:5` 等 3 处跨域 import 违反分层架构 (主计划 §7.5)。
3. **wlr production import 无防护**:32 处 `src.workline_runtime` import 横跨 workline / wms_integration services,Phase 2 burn-down 启动后无 guardrail 拦截新增违规。

## 决策

### 1. 固化 runtime/orchestration 域为对账能力官方入口

- 9 entity + 1 repository + 3 service 文件全部归属 `src/app/runtime/orchestration/`,跨域访问必须经 facade。
- 新增 `RuntimeReconciliationFacade` 作为 device/callback 域对账能力唯一入口;内部当前委托 workline 单例作为 launch PR 阶段合规桥接。
- Phase 2 burn-down 阶段把 `workline_runtime_reconciliation_service` 整体迁入 `services/runtime_reconciliation_service_impl.py`,facade 直接 import 本地实现。

### 2. wlr production import 严格型 (Step 3)

- 唯一允许 import `src.workline_runtime` 的入口:
  - `src/workline_runtime/` 自身
  - `src/app/runtime/orchestration/consumers/` (RuntimeInboxConsumer 单点入口)
  - `tests/`
  - `migrations/`
- 当前 28 处跨域 wlr production import 全部纳入 `scripts/architecture-guardrails.allowlist` 严格型条目,格式:
  ```
  R-WLR|<path>|legacy wlr import, Phase 2 迁 runtime|2026-09-30|legacy:<path>:<file>#R-WLR|phase2
  ```
- Phase 2 burn-down 期间逐 PR 消除一条;Phase 2 T3 删除整个 `src/workline_runtime/` 目录。

### 3. R-I3c guardrail scope 扩展 (Step 4)

- `scripts/architecture-guardrails.sh` rule_ri3c `SCAN_ROOTS` 从 2 域扩展为 5 域:
  - `src/app/runtime` + `src/app/workline` (原有)
  - `src/app/callback` + `src/app/wms_integration/services` + `src/app/device` (新增)
- 拒绝任何 capability 持有 `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` / `RuntimeInbox` / `RuntimeInboxConsumer` / `InboundNormalizerContext` / `create_inbound_normalizer_context` 类型 hint。

### 4. Step 5 跨域 import 修复范围

仅修复 device/callback 对 workline 的反向依赖 (3 处 + 5D device_command_service 修复),不展开 32 处 wlr migration;**wlr migration 留待 Phase 2 burn-down 6 阶段**:
- `src/app/device/services/device_command_service.py` line 182,393 → `runtime_reconciliation_facade`
- `src/app/wms_integration/services/callback_normalizer.py:5` → 本地 utils
- `src/app/workline/services/device_command_gateway.py:11,12` → runtime 域内对应模块 (5B callback mirror 已覆盖)

## 后果

### 正面

- runtime 域 ownership map 与 ADR 配套发布,Phase 2 burn-down 814 rows 迁移有明确目标态参考。
- wlr allowlist 严格型 + R-I3c 5 域扩展 + RuntimeReconciliationFacade 三重防护,任何新增跨域 import 立即被 CI 拦截。
- device/callback 不再反向依赖 workline 域,分层架构合规。
- facade 内部当前仍委托 workline 单例,Phase 2 burn-down 阶段无破坏性替换风险。

### 负面

- 28 处 R-WLR allowlist 需在 Phase 2 burn-down 期间逐 PR 消除,增加单 PR 工作量。
- RuntimeReconciliationFacade 引入额外间接层,Phase 2 burn-down 阶段需替换为本地实现 (一次性删除 workline 委托)。
- 当前 facade 内部仍 import workline 域,**不算**纯净分层;Phase 2 burn-down 完成后才算纯净。

### 中和

- Step 5 + Step 3 必须在同一 launch PR 内合并,顺序不可调整 (Step 5 修复后才能让 Step 3 guardrail 不触发批量违规)。
- Phase 2 burn-down 阶段替换 facade 实现时,需先新增 contract 测试覆盖 facade 公共方法 (`record_late_callback_if_pending` / `activate_execution_deadline_after_ack`),再切换 import 路径。

## 验收

- `docs/architecture/runtime-ownership-map.md` 已发布 (本 ADR 配套)
- `docs/architecture/file_index.md` §2.3 同步新增 `runtime/orchestration/` 完整索引,删除 `src/workline_runtime/` 索引引用 (Phase 2 T3 前保留 wlr 索引作为占位)
- `ARCHITECTURE_PHASE=phase1 ./scripts/architecture-guardrails.sh --phase phase1` 退出码 0
- `uv run pytest tests/architecture/test_wlr_import_guardrail.py tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py -v` 全绿
- `uv run python -c "from src.app.runtime.orchestration.services.runtime_reconciliation_service import RuntimeReconciliationFacade, runtime_reconciliation_facade; print(runtime_reconciliation_facade)"` import 成功

## 引用

- 顶层设计:[`../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md) §9.2 + §10.3 + §7.5
- 主计划 §5.4 H5 幂等键命名:`WES-{OPERATION_KIND}-{HASH}`
- 主计划 §C2 跨域 session FK 收敛:ExecutionCorrelation
- 主计划 §3.5.1 + H2 inbound normalizer 边界
- 主计划 §10.8 L2269 legacy-runtime-migration-spec (Step 8 待补)
- 现有 ADR:[`../workline-restructuring/0001-b方案选择与capability-freeze.md`](../workline-restructuring/0001-b方案选择与capability-freeze.md), [`../workline-restructuring/0007-execution-correlation-key.md`](../workline-restructuring/0007-execution-correlation-key.md), [`../workline-restructuring/0005-idempotency-composite-key.md`](../workline-restructuring/0005-idempotency-composite-key.md)
- Phase 1 SPEC:`docs/superpowers/specs/2026-06-26-workline-restructuring-phase-1-spec.md`
- Phase 2 launch PR 实施计划:`/Users/kaizhou/.claude/plans/dreamy-wondering-otter.md`
