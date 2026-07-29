# Legacy Runtime 迁移规格 (Phase 2 launch PR)

> **目标**:固化 Phase 2 launch PR (`feature/phase2-launch`) 在 `src.workline_runtime` 退役路径上所做的全部安全门禁,作为后续 Phase 2 burn-down PR(814 rows,756 rebuild + 58 move)的执行契约。
>
> 与 [`./workline-and-plugin-restructuring.md`](./workline-and-plugin-restructuring.md) §9.2 + §10.3 + §10.8 L2269 + [`./runtime-ownership-map.md`](./runtime-ownership-map.md) + [`./adr/0001-phase2-runtime-ownership.md`](./adr/0001-phase2-runtime-ownership.md) 配套阅读。

## 1. 范围与基线

本规格覆盖 Phase 2 launch PR 落地的 5 项安全门禁 + 8 项行为契约 + 9 处跨域 import 修复,作为 burn-down 前的「执行许可」。**不**覆盖以下 burn-down 阶段工作(留给后续 PR):

- 814 rows cleanup matrix 实际迁移(Phase 2 burn-down 6 阶段)
- `src/workline_runtime/` 整目录删除(Phase 2 T3)
- `src/app/workline/services/` 32 个 service 实际迁到 runtime(Phase 2 T2,launch PR 仅修复跨域 import)
- Runtime API facade 迁移(Phase 2 burn-down 阶段 5)
- Phase 3 ENG-009 / ENG-011 / ENG-020(idempotency 完整 + inbox backpressure + scenario replay)
- Phase 5 tech-debt cleanup(debug endpoints 等)

**基线状态**(2026-06-28,launch PR commit `8602c33b` 合并前):

- 32 处 `src.workline_runtime` production import(全在 `src/app/workline/` 与 `src/app/wms_integration/services/callback_normalizer.py:5`)
- `runtime/orchestration/` 已 15 文件:7 entities + idempotency_guard + snapshot_assembler + repositories/
- `tests/contracts/workline/` 8 contract files(31 passed + 2 xfailed,87% baseline)
- `legacy-cleanup-matrix.csv` phase2 rows = **814**(756 rebuild + 58 move)
- ARCHITECTURE_PHASE 默认值:`phase0`(launch PR 改为 `phase1`)

## 2. 5 项 Hard Blocker → 安全门禁

按 `workline-and-plugin-restructuring.md` §10.3 启动条件 + `legacy-cleanup-matrix.md` Phase 2 列强约束,launch PR 落地以下 5 项门禁:

| # | 启动条件 # | 落地形态 | 验证命令 | 当前状态 |
|---|---|---|---|---|
| 1 | runtime/orchestration 域独立落地 | `RuntimeReconciliationFacade` bridge + 9 处跨域 import 切到 facade | `uv run pytest tests/contracts/workline/ -q` + `ARCHITECTURE_PHASE=phase1 ./scripts/architecture-guardrails.sh` | ✅ launch PR commit `d5b88562` + `8eab4042` |
| 2 | 8 个 Phase 2 behavior contract gap TDD 同步 | `tests/contracts/workline/test_*.py` 8 文件 / +76 tests | `uv run pytest tests/contracts/workline/ -q` | ✅ launch PR commit `8602c33b` (107 passed, 2 xfailed) |
| 3 | Runtime ownership map + ADR 固化 | `docs/architecture/runtime-ownership-map.md` + `docs/architecture/adr/0001-phase2-runtime-ownership.md` | 阅读确认 entity/repo/service 三层归属 | ✅ launch PR commit `123f57c9` |
| 4 | InboundNormalizerRegistry async/thread safety | `src/app/runtime/inbound_normalizer_registry.py` 加 `threading.Lock` + double-check + 并发单测 | `uv run pytest tests/runtime/test_inbound_normalizer_registry_thread_safety.py -q` | ✅ launch PR commit `26452fb9` |
| 5 | guardrail 默认 phase1 + wlr allowlist 严格型 + R-I3c 5 域扩展 | `.githooks/pre-commit` + `scripts/install-git-hooks.sh` + `scripts/architecture-guardrails.sh` + `scripts/architecture-guardrails.allowlist` | `./scripts/git-quality-gate.sh --profile quality` | ✅ launch PR commit `2e7715a2` + `57de91ff` + `9bd29f03` |

## 3. 跨域 Import 修复路径(9 处,4 commit 镜像)

按用户决策,wlr(`src.workline_runtime`)严格型 allowlist 仅允许 consumer + tests + migrations,任何 production import 必须迁移到对应业务域或 runtime/orchestration 域。launch PR 通过 4 commit 完整修复 9 处跨域 import:

| # | 文件 | 原 import | 落地目标 | 决策 |
|---|---|---|---|---|
| 1 | `src/app/wms_integration/services/callback_normalizer.py:5` | `from src.workline_runtime.utils import JsonDict, resolve_first_str` | `src/app/wms_integration/utils/json_utils.py`(本地副本) | 镜像本地 utils,后续 Phase 3 再删 |
| 2 | `src/app/workline/services/device_command_gateway.py:11` | `from src.workline_runtime.enums import ...` | `src/app/runtime/orchestration/enums.py`(运行时域内对应模块) | 跨域 → 运行时域 |
| 3 | `src/app/workline/services/device_command_gateway.py:12` | `from src.workline_runtime.utils import ...` | `src/app/runtime/orchestration/utils.py` | 跨域 → 运行时域 |
| 4 | `src/app/device/services/device_command_service.py:182` | `from src.app.workline.services.runtime_reconciliation_service import ...` | `src/app/runtime/orchestration/services/runtime_reconciliation_facade.py` | 跨域 → facade |
| 5 | `src/app/device/services/device_command_service.py:393` | `from src.app.workline.services.runtime_reconciliation_service import ...` | `src/app/runtime/orchestration/services/runtime_reconciliation_facade.py` | 跨域 → facade |
| 6 | `src/app/callback/services/callback_normalizer.py` | `from src.workline_runtime.utils import ...` | `src/app/callback/utils/json_utils.py` | 镜像本地 utils |
| 7 | `src/app/callback/services/inbound_event_router.py` | `from src.workline_runtime.utils import ...` | `src/app/callback/utils/json_utils.py` | 镜像本地 utils |
| 8 | `src/app/callback/services/callback_security.py` | `from src.workline_runtime.utils import ...` | `src/app/callback/utils/json_utils.py` | 镜像本地 utils |
| 9 | `src/app/callback/services/callback_idempotency.py` | `from src.workline_runtime.utils import ...` | `src/app/callback/utils/json_utils.py` | 镜像本地 utils |

`RuntimeReconciliationFacade` 是 launch PR 关键 bridge:device 域调用时序无关、无副作用、不污染状态源,内部委托 `src/app/runtime/orchestration/services/runtime_reconciliation_service.py`(Phase 1 已存在)。后续 burn-down 阶段 5 替换为 Runtime API facade,facade 仅作过渡。

## 4. Guardrail 范围与严格型 Allowlist

### 4.1 R-I3c(Inbound Normalizer Port 归属)5 域扩展

`scripts/architecture-guardrails.sh` rule_ri3c SCAN_ROOTS 从 `(src/app/runtime, src/app/workline)` 扩展为:

```python
SCAN_ROOTS = (
    Path("src/app/runtime"),
    Path("src/app/workline"),
    Path("src/app/callback"),
    Path("src/app/wms_integration/services"),
    Path("src/app/device"),
)
```

**语义**:任何 Inbound Normalizer Port Protocol 的持有者必须在上述 5 域之一,且持有者必须从该域的 `inbound_normalizer_registry` 解析 port 实例。`EXCLUDED_FILES` 集合新增 `src/app/wms_integration/services/callback_normalizer.py`(Step 5 修复后会从 allowlist 移除)。

### 4.2 wlr(`src.workline_runtime`)严格型 Allowlist

按用户决策,wlr production import 仅允许:

| 类别 | 路径前缀 | 数量 |
|---|---|---|
| 入站消费者 | `src/app/runtime/orchestration/consumers/` | 单点入口(launch PR 不新增,Phase 2 T2 阶段 5 引入) |
| 测试 | `tests/` | 全部 |
| 数据迁移 | `migrations/` | 全部 Alembic 脚本 |

**严格型意味着**:launch PR 落地前 32 个 `src.workline_runtime` production import 中的 31 个都违规,Step 5 必须先把这 31 个迁移走,否则 guardrail 一启用即报错。Step 3(guardrail)+ Step 5(跨域 import 修复)必须在同一 PR 内合并,顺序不可调整。

### 4.3 ARCHITECTURE_PHASE 默认值

`scripts/git-quality-gate.sh` line 117-118 默认值从 `phase0` 改为 `phase1`,配合 `.githooks/pre-commit` 显式 `export ARCHITECTURE_PHASE=phase1` 双保险,`scripts/install-git-hooks.sh` 通过 `git config --add hook.env` 在 CI 中持久化。Phase 2 burn-down 完成后,默认改回 `phase2`。

## 5. 8 个 Behavior Contract Gap(Burn-down 安全网)

Phase 2 burn-down 重构 814 行代码时,8 个 contract 提供回归保护。每个 contract 独立、happy + error path、TDD 同步:

| # | Contract | 测试文件 | 测试数 | 锚点能力 / 主计划章节 |
|---|---|---|---|---|
| 1 | Inbox lifecycle(claim/process/retry/dead-letter) | `test_runtime_inbox_lifecycle_contract.py` | 7 | RuntimeInboxService.process_one / 主计划 §9.2 |
| 2 | Intent log dispatch/replay/hash mismatch | `test_runtime_intent_log_dispatch_contract.py` | 8 | RuntimeIntentLog.dispatch_effect / 主计划 §9.2 + §3.5.1 |
| 3 | Session advance / work item step | `test_runtime_session_advance_contract.py` | 6 | RuntimeSessionService.advance / 主计划 §7.5 |
| 4 | Timeline query(trace_id/correlation_id/event_type) | `test_runtime_timeline_query_contract.py` | 6 | RuntimeTimelineQueryService.query / 主计划 §9.2 |
| 5 | Hold scoped block/release(NARROW/WIDE scopes) | `test_runtime_hold_contract.py` | 7 | RuntimeHoldService.evaluate / 主计划 §7.5 C3 |
| 6 | Device command dispatch + ACK/result correlation | `test_device_command_dispatch_contract.py` | 24 | DeviceCommandPort.dispatch / 主计划 §7.5 C4 |
| 7 | WMS fulfillment request + ACK/status correlation | `test_wms_batch_ack_contract.py` | operation-specific | E08–E14 typed fulfillment contracts / 主计划 §5.1 + H4 |
| 8 | Manual replay from dead-letter / audit chain | `test_manual_replay_audit_contract.py` | 8 | RuntimeInboxService.process_one + H5 / 主计划 §9.2 |

**Mock 边界**:仅允许 `src/app/runtime/orchestration/` 内的 skeleton 实体(Phase 1 已 100% coverage),`tests/support/runtime_inbox_contract.py` + `tests/support/workline_contracts.py` 复用 fixture;不依赖 DB session / DB migration,确保 contract 在 burn-down 任何阶段都可独立运行。

**H4 反注入边界**:contract 6/7 用 `extra="forbid"` + `_FORBIDDEN_PARAM_KEYS` 阻断 10 个禁止字段(plc/plc_address/coordinate/coordinates/joint/joint_angle/axis/x_coord/y_coord/safety_loop),与 Phase 1 SPEC 顶部 H4 边界同步。

**H5 幂等键命名**:contract 2 验证 `WES-{OPERATION_KIND}-{HASH}` 命名 + 归一化 + 边界;contract 8 验证人工重放必须新建 inbox 记录 + 审计 (actor + reason 必填)。

## 6. Burn-down 执行顺序与门禁挂钩

launch PR 完成后,Phase 2 burn-down PR 必须按以下顺序执行,每个 PR 复用 launch PR 落地的安全门禁:

| 阶段 | 内容 | 关联门禁 | 验证 |
|---|---|---|---|
| 1 | Inbound Normalizer 切到 5 域 registry | R-I3c 5 域扩展 | `ARCHITECTURE_PHASE=phase1 ./scripts/architecture-guardrails.sh --phase phase1` |
| 2 | wlr 31 处 production import 全部迁移(按 capability 分批) | wlr allowlist 严格型 + RuntimeReconciliationFacade | 同上 + `tests/contracts/workline/` 全绿 |
| 3 | `src/workline_runtime/` 整目录删除 | wlr allowlist 严格型 | 同上 + `tests/contracts/workline/` 全绿 | ✅ **2026-06-30 (PR 阶段 3)：物理删除 178 个 wlr 源文件** |
| 4 | `src/app/workline/services/` 32 个 service 实际迁到 runtime/orchestration/services/ | ownership map service 层 + RuntimeReconciliationFacade 退役 | 阅读 ownership map | ✅ **2026-06-30 (PR 阶段 4,v0.10.2.0)：13 service + 5 phase4 capability 物理迁入** |
| 5 | Runtime API facade 替换 RuntimeReconciliationFacade | 同上 | 阅读 ownership map | ✅ **2026-06-30 (PR 阶段 5,v0.10.3.0)：`RuntimeReconciliationFacade` 类物理删除,0 调用方,impl 直连** |
| 6 | WorkLine 仅保留配置 CRUD + manifest + plane scene | ownership map + runtime/orchestration/ 全实体可用 | `tests/contracts/workline/` 全绿 | ✅ **2026-06-30 (PR 阶段 6,v0.10.3.0)：workline 域大规模物理瘦身 (~57 文件) + device_command_gateway 迁出 + 4 个 C2 incomplete cleanup dead test 清理** |

每个阶段单独 PR,每个 PR 内 commit 独立、含测试、Conventional Commits、**不写 Co-Authored-By**(CRITICAL)。阶段 1-3 阶段顺序敏感:wlr allowlist 严格型生效时,所有 31 处 production import 必须已迁移。

## 7. 验证命令(launch PR 收尾)

```bash
# 5 项 hard blocker 全绿
uv run pytest tests/contracts/workline/ -q                    # 107 passed, 2 xfailed
uv run pytest tests/runtime/test_inbound_normalizer_registry_thread_safety.py -v
uv run pytest tests/architecture/test_wlr_import_guardrail.py -v
ARCHITECTURE_PHASE=phase1 ./scripts/architecture-guardrails.sh --phase phase1
./scripts/git-quality-gate.sh --profile quality

# 主计划 §10.3 启动条件 #2 / #4 / #5 同步勾选
# 1. Phase 0 ✅ (PR #63)
# 2. Phase 1 全 Packet A/B/C/D ✅ (PR #64 #66)
# 3. 重新跑 autoplan CONDITIONAL-GO ✅ (autoplan 双 voice + DX 三 voice 决议)
# 4. legacy cleanup matrix 可归类(全部 delete / rebuild / move / keep-contract)
```

## 8. 不在本次 launch PR 范围

下列工作显式不在 launch PR 内,留给 Phase 2 burn-down PR:

- 814 rows cleanup matrix 实际迁移(Phase 2 burn-down 6 阶段)
- ~~`src/workline_runtime/` 整目录删除(Phase 2 T3)~~ 已于 2026-06-30 阶段 3 PR 完成,本条不再适用
- `src/app/workline/services/` 32 个 service 实际迁到 runtime(Phase 2 T2,仅修复跨域 import)
- Runtime API facade 迁移(Phase 2 burn-down 阶段 5)
- Phase 3 ENG-009 / ENG-011 / ENG-020(idempotency 完整 + inbox backpressure + scenario replay)
- Phase 5 tech-debt cleanup(debug endpoints 等)

## 9. 参考文档

- [`./workline-and-plugin-restructuring.md`](./workline-and-plugin-restructuring.md) §9.2 + §10.3 + §10.8 L2269
- [`./runtime-ownership-map.md`](./runtime-ownership-map.md) entity/repo/service 三层归属
- [`./adr/0001-phase2-runtime-ownership.md`](./adr/0001-phase2-runtime-ownership.md) Phase 2 launch PR ADR
- [`./reviews/phase2-coverage-baseline-2026-06-28.md`](./reviews/phase2-coverage-baseline-2026-06-28.md) P0-003 baseline 87%
- `~/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260628-121500.md` Final Approval Gate 决议
- `docs/architecture/legacy-cleanup-matrix.csv` 814 rows
- `docs/superpowers/archive/specs/2026-06-26-workline-restructuring-phase-1-spec.md` Phase 1 SPEC,引用 §3.5.1 + §7.5
