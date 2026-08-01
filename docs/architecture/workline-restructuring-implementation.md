> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: implementation = 原文件 §10 实施计划 + §11 执行规范 + §12 风险与对策。

---

## 10. 实施计划

Phase 0-5 六个阶段按 critical path 严格串行；Phase 内任务可并行。实施默认允许破坏性清理，不设置旧 API / 旧表 / 旧插件兼容目标。

### 10.0 实施进度快照（2026-07-07 同步）

| Phase | 状态 | 已合并 PR | 关键交付 | 待办 |
| --- | --- | --- | --- | --- |
| Target-state baseline（P0-001~P0-007） | ✅ **已完成** | [#63](https://github.com) `v0.9.0.0` (2026-06-25) | 7 项必做 + 行为契约 10 BC + legacy 清理矩阵（baseline 初始矩阵已发布；restructuring cleanup 后当前 636 条、0 pending-review / 0 空策略）+ stable architecture guardrails | — |
| Phase 1 Packet A Foundation（CEO-005/006/012 + AP5） | ✅ **已完成** | [#64](https://github.com) Packet A (2026-06-27) | `scope/authority/source/evidence_at` schema + Authority Matrix + SafetyZone + manifest version pin | — |
| Phase 1 Packet B ACL & WMS Ports（CEO-001 起步 + CEO-013 + H4 + ADR-0009） | ✅ **已完成** | [#64](https://github.com) Packet B (2026-06-27) | `WmsMasterDataPort` / `InventoryQueryOperationPort` / `WmsInventoryTransactionPort` 3 ports 起步 + `ExternalContractProfile` / `RuntimeCapabilityProfile` / `InboundNormalizerProfile` + provider simulator registry + H4 反注入白/黑名单 + ADR-0009 shared contracts package | 剩余 4 port（Document / Fulfillment / Event / ReconciliationQuery）→ Packet D |
| Phase 1 Packet C Runtime 骨架（CEO-007/008/010/011 + H5） | ✅ **已完成** | [#64](https://github.com) Packet C (2026-06-27) | 7 个 runtime core 实体（ExecutionSession / ExecutionCorrelation / ExecutionWorkItem / RuntimeInbox / RuntimeTimeline / RuntimeHold / RuntimeIntentLog + IdempotencyKey） + `ConveyorQueueMembership` + DeviceCommand ECS contract + device FK ring dissolve + H5 IdempotencyGuard + callback / handling / rack 接入 runtime/orchestration | — |
| Capability 边界 + WMS contract（CEO-009） | ✅ **已完成** | Packet D (2026-06-27) + 本分支 callback admission 收敛 | operation-specific document/fulfillment Definitions、WmsEventPort、WmsReconciliationQueryPort 落地 + InboundNormalizerRegistry + consumer-only `InboundNormalizerContext.get_inbound_normalizer()` + `INBOUND_NORMALIZER_OWNERSHIP` 静态扫描 + import-linter capability-isolation contract + callback API provider profile admission 热路径接入 | — |
| Phase 2 Runtime/Orchestration 迁移与 WorkLine 清空 | ✅ **restructuring cleanup 完成，运行态原生投影落地** | launch PR `8602c33b` + runtime migration cleanup（PR #70 `v0.10.2.1`）+ F-1/F-2 收尾（PR #71 `v0.10.3.0`）+ restructuring cleanup migration | runtime migration cleanup + F-1/F-2 收尾已完成 service/v1 router 域清空、facade 物理删除、device_command_gateway 迁出和 model/repository 迁入；restructuring cleanup 删除 WorkLine 运行态物理列，新增 `wes_runtime.workline_runtime_status_projections`，safety / START admission / query / trace 只通过 snapshot/readiness 暴露运行状态 | guardrail 与 migration smoke 负责防止 WorkLine 配置域重新承载运行态 |
| Phase 3 执行安全与恢复能力补全 | 🟡 **本地合同/门禁已补齐，开发/测试 MOCK closure 可通过** | [#73](https://github.com/kaizhoumasha/wes_backend/pull/73) `v0.10.4.0` (2026-07-02) + 本分支 production closure slice | callback body HMAC/nonce、RuntimeInbox 幂等/重放/backpressure、ReconciliationManager owner-scoped 决议、ActiveObjectRegistry、DeviceCommand lease、WMS fulfillment 状态机/typed evidence、WorkLine plane/manifest、ops contracts；本分支新增并接入 DeviceDispatchPolicy 到 DeviceCommandGateway 预检与 dispatch policy metrics、落地 DB-backed ConveyorQueueMembership writer service 与写入诊断结果、PostgreSQL `FOR UPDATE` active identity 锁语义和 opt-in unique race 合同、`wes_runtime.device_runtime_projections` 持久 DeviceRuntime 投影与 DeviceService 运行态同步，并补齐 ReconciliationManager 幂等登记入口、runtime reconciliation `TIMER_TIMEOUT` / dispatch ACK exhausted 热路径 claim、WMS fulfillment 幂等 opening 入口、RuntimeIntent `EXTERNAL_REQUEST` fulfillment 实际发起热路径 claim、RuntimeInbox device_event 幂等 claim、plane owner/superuser 行级过滤与 audit log、ScenarioRecorder/ReplayRunner active projection diff、IntegrationLab fixture runner、TraceQueryResult 生产录制源适配、RuntimeP0E2EGate 与 RuntimeP0E2EArtifactComposer 生产 E2E 证据门禁、RuntimeBenchmarkArtifactComposer 生产 benchmark evidence 组装门禁、RuntimeProductionClosureGate 总门禁、RuntimeObservabilityRegistry、RuntimeOpenTelemetryBridge、RuntimeToggleRegistry、RuntimeToggleReleaseGate、RuntimeBenchmarkGate profile/provenance/workload metadata gate、ExternalReferenceCatalog、timeout 转移、full-box / RACK_BIN exchange 合同、runtime toggle quality gate、external callback allow-list 矩阵、`tests/load` 轻量 benchmark 命令、benchmark artifact 合同、CI lightweight artifact 归档、resilience replay fixture 和 simulator replay fixture、OpenTelemetry backend 接线 | 本项目未发布，当前开发/测试默认使用 MOCK closure；生产发布前再显式运行 `--closure-profile production` 并提供真实 P0 E2E 与 production-scale benchmark artifact |
| Material-flow target capabilities | ✅ **SPEC 已写，read-model 与 runtime capability 已闭合，evidence profile gate 已落地** | material-flow 设计包 + runtime readiness 分支 | `phase4-design-with-residuals.md` + 5 份 material-flow SPEC；CellReservation / RuntimeLocationEvent / MaterialLocationQuery / WorklineActiveObjects 已进入本机合同验证；sorter inbound 与 SMT/NG/WMS reconciliation 已从 preview 语义推进到 production-capable runtime path builder；`site/production` evidence manifest gate 与 composer 已闭合 | Phase 1 callback admission 已关闭；当前开发/测试默认使用 MOCK closure；后续发布前仍需提供 material-flow evidence manifest 引用文件，并满足 Phase 3 production closure |
| Phase 5 Legacy 删除与收尾 | ✅ **全部重构完成** | [#78](https://github.com/kaizhoumasha/wes_backend/pull/78) `v0.13.0.0` (2026-07-07) + [#79](https://github.com/kaizhoumasha/wes_backend/pull/79) `v0.14.0.0` (2026-07-07, merge SHA `8c833610c08005005406b3a774c92519f69b7886`) + restructuring cleanup migration | `RuntimeCapabilityDispatcher` + runtime capability catalog + `RuntimeInbox -> InboundNormalizerRegistry -> RuntimeCapabilityDispatcher -> material-flow runtime service -> RuntimeIntent / EffectPort` 链路 + `tests/architecture/test_legacy_absence_guardrail.py` + `docs/architecture/legacy-cleanup-execution-plan.md`；旧 plugin runtime/import 框架、旧 registry、旧 template 目录已退出 `src/` / template 可 import 或 authoring 路径；旧 `BinTransitMembership/BinTransitQueue` production surface 已删除；runtime production closure + material-flow production evidence ledger 见 `docs/architecture/phase3-phase4-production-evidence-bundle.md`；business readiness gate 与 `scripts/check_business_legacy_absence_gate.py --mode final` 已通过；业务合同、context 与 SMT handoff 相关语义已收口到 material-flow runtime capability / contracts 目标态 | restructuring cleanup gate 与 absence guardrail 负责阻断旧表面回流 |

**Phase 3 / Phase 4 / Phase5 closure 状态校验（2026-07-07）**：

- 结论：本项目未发布，当前开发/测试默认使用 MOCK closure；`uv run python scripts/check_runtime_production_closure_gate.py` 无 artifact 即按 mock profile 通过。真实 artifact 不再作为当前开发/测试推进阻塞项。
- 生产发布 profile：`--closure-profile production` 仍要求真实 P0 E2E artifact、production-scale benchmark artifact，以及两类 artifact 引用 evidence 文件存在且内容一致；Phase4 `production` profile 仍要求 provider contract、trace、benchmark evidence manifest 文件存在且 hash 一致。
- 本轮 evidence bundle：`reports/phase3/phase3-p0-e2e.json`、`reports/phase3/phase3-production-benchmark.json` 与 `reports/phase4/runtime-evidence-production.json` 已由现有 evidence 重新生成并通过 production gates；tracked provenance 见 `docs/architecture/phase3-phase4-production-evidence-bundle.md`。
- 下一阶段边界：Phase 4 开发/测试能力已闭合；technical cleanup scope 已在 PR #78 合并。business legacy cleanup scope 携带 regenerated production/runtime artifacts 后已通过 runtime production closure、runtime evidence 与 business legacy absence gate；随后 business legacy absence gate 已通过 `uv run python scripts/check_business_legacy_absence_gate.py --mode final`，并在 PR #79 合并到 develop。2026-07-08 restructuring cleanup 已删除旧 handling 队列表面和 WorkLine 运行态物理列；当前提交前入口使用 `./scripts/git-quality-gate.sh --profile quality` 覆盖长期门禁。PR #79 未检测到 deploy workflow 且无生产 URL，线上 canary 由后续生产发布流程或 `/canary <url>` 补做。
- 轻量 benchmark 口径：`reports/benchmarks/runtime-benchmark.json` 的 `local-lightweight` / `lightweight` 结果可用于当前开发/测试 mock 验收；不得冒充 `--closure-profile production` 证据。

### 10.0.1 Phase1~4 residual ledger（2026-07-06）

| Phase | 遗留项 | 当前状态 | owner / guardrail | Phase5 前置 |
| --- | --- | --- | --- | --- |
| Target-state contracts | WMS 7 port、runtime core entity、ExecutionCorrelation、InboundNormalizerProfile 等目标态骨架已落地；剩余只是生产 profile evidence 的后续运行材料 | ✅ 架构残留关闭 | Target-state 合同测试与 capability/H4/H5 guardrail 保持边界 | 删除旧入口前必须保留 target-state 合同测试可通过，不能删除目标态 port/profile 证据 |
| Phase 2 | WorkLine 运行态物理字段已删除 | ✅ runtime/orchestration 原生投影 | `WorkLineRuntimeStatusProjectionService` 读写 `wes_runtime.workline_runtime_status_projections`；`tests/architecture/test_runtime_status_owner_guardrail.py` 禁止 WorkLine 域和 material-flow capability 直接写入或绕过 snapshot/readiness | restructuring cleanup migration 已完成回填；后续只允许 runtime/orchestration 扩展投影 |
| Phase 3 | production closure artifact、production-scale benchmark 和真实外部依赖 evidence 已具备可再生成 bundle | ✅ evidence bundle ready | `RuntimeProductionClosureGate` 区分 mock profile 与 production profile；mock 只允许开发/测试推进；production bundle 记录在 `docs/architecture/phase3-phase4-production-evidence-bundle.md` | business readiness 已使用 regenerated artifacts 验证通过；raw `reports/` artifacts 仍需从 restored field/CI evidence 重新生成 |
| Phase 4 | material-flow capability runtime path 已落地，production evidence manifest 引用文件和 hash profile 已具备可再生成 bundle | ✅ evidence bundle ready | material-flow capability 不能直接写 WorkLine 运行态；START admission 只读 runtime projection readiness；production bundle 记录在 `docs/architecture/phase3-phase4-production-evidence-bundle.md` | business readiness、business legacy absence gate 与 restructuring cleanup 均已通过 |

**Phase1~4 residual closure 验收记录（2026-07-07）**：

- Phase2：WorkLine 运行态物理列已由 restructuring cleanup migration 删除，`WorkLineRuntimeStatusProjectionService` 读写 runtime/orchestration 原生投影，safety / START admission / query / trace 通过 snapshot/readiness 使用运行状态。
- Phase3：development/mock closure 与行为合同已通过；production closure gate 已具备真实 P0 E2E artifact、production-scale benchmark artifact 与 evidence hash 校验；本轮 regenerated artifacts 已通过 production closure gate。
- Phase4：development-mock readiness 与 runtime capability / evidence profile gate 已通过；`site/production` profile 已要求 evidence manifest、文件存在与 hash 一致，并叠加 Phase3 production closure；本轮 regenerated artifact 已通过 production readiness gate。
- Phase5：technical cleanup scope 已由 PR #78 合并；旧 plugin runtime/import 框架、旧 `src.workline_plugin_registry` 和旧 `docs/templates/workline_plugin/` 已退出运行/模板路径；business legacy cleanup scope readiness 与 business legacy absence gate 已通过。业务执行合同迁入 `src/app/runtime/capabilities/material_flow/contracts/`，旧业务入口由 ledger 与 absence guardrail 阻断回流；restructuring cleanup 已删除旧 handling 队列表面和 WorkLine 运行态物理列。
- 历史验证曾包含 material-flow runtime evidence gate；测试所有权收敛后，该插件专属 gate 与 composer 已从核心退役。当前长期提交前入口为 `./scripts/git-quality-gate.sh --profile quality`，覆盖 runtime production closure、business legacy absence、process naming、architecture guardrails、测试所有权门禁与 import-linter。

**Task 1+2 收敛结论**：Phase2 的运行态字段遗留已由 restructuring cleanup 关闭；callback / WMS / benchmark / evidence / WorkLine restructuring gate 的生产证据项已由 `docs/architecture/phase3-phase4-production-evidence-bundle.md` 记录；business legacy cleanup readiness、business legacy absence gate 与 restructuring cleanup gate 均已进入 passed 状态。

**Phase 3 PR #73 已完成项（`v0.10.4.0`，2026-07-02 同步）**：

- External callback 从字段签名升级到 body 完整性签名，并补齐 nonce replay 防护、固定 TTL 原子消费、`X-Body-SHA256`、`API_PATH` 感知 callback 前缀和 fail-closed 路径。
- RuntimeInbox 支持 ACK-before-processing 后的 source event 幂等创建、payload hash 冲突检测、唯一冲突重读、死信/人工重放审计和 backpressure 策略。
- `ReconciliationManager` 落地 owner-scoped resolution decision、hold/freeze action 和人工恢复审计的最小合同，不直接覆盖 owner 终态。
- `ActiveObjectRegistry` 落地跨投影 active 归属仲裁读模型，覆盖多来源 active object 冲突 policy。
- DeviceCommand 落地可过期 lease 和 recovery 策略，为 per-device in-flight、重放/取消和后续 dispatch policy 提供基础。
- WMS fulfillment 落地 E08–E14 ACK/status/typed terminal result 状态收敛、终态保护、CB 语义和 typed evidence envelope；可选 callback hint 只唤醒 status query，不得覆盖成功或拒绝终态。
- WorkLine plane 落地 `PlaneSceneView` / `PlaneSnapshot` 读模型、scene/snapshot 分离 route 和 manifest activation validator。
- 运维合同新增 `docs/contracts/observability-contract.md` 与 `docs/contracts/runtime-toggle-governance.md`，明确 runtime observability signal、toggle owner/expiry/scope/default/rollback/test matrix 和安全绕过禁令。

**Phase 3 本分支新增合同与热路径实现（2026-07-03 同步）**：

- `DeviceDispatchPolicy` / `DeviceRuntimeProjection`：补齐 fresh IDLE 放行、过期/未知状态短退避、RUNNING deadline 有界等待、session HOLD/RECONCILING/CLOSED 冻结或取消的纯策略合同，并已接入 `DeviceCommandGateway.dispatch` 热路径与 `device_command.dispatch_policy` metrics；本分支新增 `wes_runtime.device_runtime_projections`、repository/writer service 与 Alembic 迁移，由 `DeviceService._update_runtime_state` 同事务同步运行态投影。fresh busy/hard-state 本地快照会短路，stale/unknown 本地快照继续走 ECS realtime status probe。
- `ConveyorQueueWriter`：补齐同 queue 幂等重放、跨 queue 冲突进入 RECONCILING、placeholder resolve、未知 queue strict-mode 阻断的写入决策合同，并新增 `ConveyorQueueMembershipRepository` / `ConveyorQueueMembershipWriterService`，覆盖 ACTIVE 创建、幂等复用、placeholder 原地解析、RECONCILING 标记、strict-mode unknown queue 阻断、唯一冲突后的 existing 重读和写入结果诊断；写入前 ACTIVE identity 查询在 PostgreSQL 方言下生成 `FOR UPDATE`，并新增 opt-in integration 测试验证真实 PostgreSQL partial unique index 并发冲突后的 existing 重读。
- `PlaneReadSecurityPolicy` / `PlaneReadPrincipal`：补齐 plane scene/snapshot 专用权限、`WORKLINE_LOCAL` scope、WorkLine 配置字段脱敏 deny-list、审计 action、owner/superuser 行级过滤与统一 audit log；route 权限依赖从硬编码字符串收敛为引用 policy，并在读取前把认证上下文传入 service 执行真实拦截。
- `ScenarioRecorder` / `ScenarioReplayRunner` / `IntegrationLabScenarioRunner`：补齐脱敏录制、deterministic replay、active projection diff、timeline/outbox/projection hash/reconciliation reason 断言合同；本分支新增 `tests/resilience/fixtures/runtime_replay_fixture.json`、`runtime_simulator_replay_fixture.json`、`runtime_integration_lab_fixture.json`、ECS external contract fixture set、TraceQueryResult 生产录制源适配与显式 resilience replay 测试；IntegrationLab runner 基于 `ExternalContractProfile` / `ProviderSimulatorRegistry` 验证 WMS/ECS sandbox profile、fixture case 覆盖、完整链路事件类型和乱序/重复/超时/拒绝/断网场景。
- `RuntimeP0E2EGate` / `RuntimeP0E2EArtifactComposer`：补齐生产 P0 E2E artifact 门禁、`scripts/check_runtime_production_e2e_gate.py` 校验脚本和 `scripts/compose_runtime_production_e2e_artifact.py` 组装脚本，要求真实 trace-query 来源、非 sandbox/lightweight 环境、WMS/ECS 依赖画像、端到端 P95 < 30s、manifest/session/inbox/intent/device/WMS/plane 事件组、DeviceCommand + WMS fulfillment effect evidence，以及 ECS timeout / WMS reject / callback out-of-order 三类异常路径均进入 `RECONCILING`；生产/预生产实际跑数 artifact 仍待补齐。
- `RuntimeObservabilityRegistry`、`RuntimeOpenTelemetryBridge`、`RuntimeOpenTelemetryHttpExporter`、`RuntimeToggleRegistry`、`RuntimeToggleReleaseGate`、`RuntimeBenchmarkGate`、`RuntimeBenchmarkArtifactComposer`：补齐稳定 attributes 校验、observer 发射入口、OpenTelemetry-style exporter fan-out、生产 backend adapter 接线、callback normalize instrumentation、WMS breaker transition instrumentation、WMS evidence persistence failure instrumentation、DeviceCommand ACK age instrumentation、DeviceCommand RESULT instrumentation、RuntimeInbox claim instrumentation、RuntimeIntent / Workline Outbox dispatch instrumentation、toggle owner/expiry/security-bypass 拦截、release toggle default-off/test_matrix evidence 发布阻塞和 Phase 3 benchmark 场景清单合同；runtime toggle release gate 已接入 `scripts/git-quality-gate.sh --check runtime-toggle-release` 和 quality profile；本分支补齐 `tests/load/test_runtime_inbox_claim_benchmark.py`、`test_conveyor_queue_writer_benchmark.py`、`test_ecs_status_command_benchmark.py`、`test_plane_snapshot_benchmark.py` 四个轻量 benchmark 命令，并新增 `tests/load/fixtures/runtime_benchmark_artifact.json`、`tests/load/runtime_benchmark_scenarios.py`、`scripts/run_runtime_benchmarks.py` 与 `Jenkinsfile.backend-ci` artifact 归档；queue writer benchmark artifact 已纳入 `integrity_conflict_recheck_count` 诊断指标；benchmark artifact profile metadata gate 已区分 lightweight 与 production-scale，并要求 production-scale 明确 PostgreSQL backend、外部依赖 profile、并发度和持续时间；本分支继续收紧 production-scale artifact provenance/workload gate，要求 RuntimeInbox/queue writer 来源为 PostgreSQL、ECS status/command 来源为 ECS HTTP、PlaneSnapshot 来源为 API HTTP，提供每个 scenario 的 evidence 路径，并声明 §8.3 基线规模 workload（RuntimeInbox 1000 pending/4 workers、queue writer 200 active memberships + identity collision、ECS status/command 调用量、PlaneSnapshot 1 WorkLine/10 queue/50 device/100 session/200 object）；本分支新增 `RuntimeBenchmarkArtifactComposer` 与 `scripts/compose_runtime_benchmark_artifact.py`，只能从四个真实场景 evidence JSON 组装 production-scale artifact 并复用 `RuntimeBenchmarkGate` 校验；当前开发/测试默认使用 MOCK closure，生产规模真实外部依赖压测延后到 production closure profile。
- `RuntimeProductionClosureGate`：补齐 runtime production closure 总门禁与 `scripts/check_runtime_production_closure_gate.py`；当前开发/测试默认使用 MOCK closure，无 artifact 可通过并标记 `MOCK_PRODUCTION_CLOSURE`。生产发布前显式切 `--closure-profile production` 时，仍要求生产 P0 E2E artifact 和 production-scale benchmark artifact 存在并通过各自 gate，并校验 artifact 引用的 trace、异常路径和 benchmark scenario evidence 文件真实存在且内容与 artifact 一致；缺任一 artifact、缺引用 evidence 文件、引用 evidence 内容不一致、benchmark 不是 `production-scale`，或 benchmark environment 属于 lightweight / sandbox，都不能通过 production closure profile。
- WMS fulfillment 状态机补齐 4 类 timeout 事件合同与 current-state-aware 可观察转移矩阵，避免 status/terminal result 越级改状态；并修正 circuit breaker open/half-open 只阻断出站请求、不覆盖已在途 fulfillment 状态；typed port 集成矩阵覆盖 OPEN fast-fail 与 HALF_OPEN trial-in-progress 二次 effect 不打 HTTP。
- WMS evidence 保留热表写入、脱敏、hash、查询与 ExternalReference drift；已删除无生产调用的 WMS
  专用 archive/retention 表面，记录保留服从项目统一 retention、运维和容量策略。
- External callback allow-list 补齐 Phase 3 矩阵：WMS 仅接收普通事件与 `WMS_EFFECT_STATUS_HINT`，EFFECT hint 只唤醒 status query；统一 external callback 入口拒绝未登记 `callback_type`，并校验 ECS/device 与 AGV source mismatch，避免跨 provider callback_type/source 混用。
- `ExternalReferenceCatalog` 补齐 typed external reference 与 `source_version` drift 分类合同；`WmsCallEvidence` request/response snapshot 升级为 JSONB 并声明 GIN 索引；`WmsCallEvidenceService.run_external_reference_drift_job()` 只读扫描 evidence envelope 并输出 drift report；`docs/contracts/evidence-catalog.md` 固化 schema、索引和 drift 分类口径。
- full-box / RACK_BIN exchange 行为合同从 strict xfail 转为真实合同：验证 exchange typed ACK/status/terminal result、reconciliation completion policy、生产 outbox 包络和本地冲突进入 ReconciliationManager / membership RECONCILING 投影的语义。
- ReconciliationManager 新增 `register_conflict_idempotent()` 生产登记入口，并已接入 runtime reconciliation `TIMER_TIMEOUT` 与 dispatch ACK exhausted 热路径：登记 owner-scoped decision 前先通过 `IdempotencyGuard` claim `operation_kind=reconciliation`，同 key 同 hash 返回 MATCH，同 key 不同 hash 抛 409 `IdempotencyConflict` 并暴露 reconciliation 域审计 payload；缺少 `correlation_id` 的 legacy 路径保持原行为。
- WMS fulfillment lifecycle 新增 `open_request_idempotent()` opening 入口，并已接入 RuntimeIntent `EXTERNAL_REQUEST` 实际发起热路径：创建 fulfillment outbox 前先通过 `IdempotencyGuard` claim `operation_kind=fulfillment`，同 key 同 hash 返回 MATCH，同 key 不同 hash 抛 409 并暴露 `wms_integration` 审计 payload；缺少 `correlation_id` 的 legacy 外部 HTTP 路径保持原行为。
- RuntimeInbox device_event 入站入口新增 `IdempotencyKey` claim：`COMMAND_RESULT` / `EVENT_PUSH` 等 canonical `device_event` 在具备 `correlation_id + source_event_id + payload_hash` 时同步 claim `operation_kind=device_event`，同 key 同 hash 复用，同 key 不同 hash 抛 409 `IdempotencyConflict` 并暴露 device 域审计 payload；缺少 correlation 的 legacy ACK 路径保持原行为。

**Phase 3 PR #73 回归证据**：

- `uv run pytest tests/ -q`：2929 passed, 35 skipped, 4 xfailed
- `uv run ruff format --check .`：754 files already formatted
- `uv run ruff check .`：All checks passed
- `uv run bandit -r src/ -q`：安全扫描通过
- `./scripts/git-quality-gate.sh --profile quality`：通过

**Phase 3 本分支验证证据（2026-07-02）**：

- `uv run pytest tests/ -q`：1544 passed, 5 skipped, 1 xfailed, 3 warnings
- `uv run pytest tests/runtime/orchestration/test_device_command_gateway.py -q`：10 passed
- `uv run pytest tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py -q`：7 passed
- `uv run pytest tests/api/test_workline_routes.py tests/workline/test_plane_read_model.py -q`：23 passed
- `uv run pytest tests/runtime/orchestration/test_runtime_recovery_policies.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py tests/runtime/orchestration/test_runtime_operational_contracts.py -q`：15 passed
- `uv run pytest tests/runtime/orchestration/test_runtime_operational_contracts.py tests/contracts/test_runtime_ops_contract_docs.py -q`：11 passed
- `uv run pytest tests/wms_integration/test_circuit_breaker.py tests/wms_integration/test_wms_client.py tests/wms_integration/test_fulfillment_state_machine.py -q`：49 passed
- `uv run pytest tests/wms_integration/test_fulfillment_state_machine.py tests/wms_integration/test_fulfillment_lifecycle_service.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py -q`：14 passed
- `uv run pytest tests/wms_integration/test_evidence.py tests/wms_integration/test_typed_evidence_envelope.py -q`：13 passed
- `uv run pytest tests/wms_integration/test_callback_normalizer.py -q`：33 passed
- `uv run pytest tests/handling/test_handling_operation_core.py -q`：处理作业核心行为通过
- `uv run pytest tests/api/test_callback_result_api.py -q`：8 passed
- `uv run pytest tests/api/test_callback_event_api.py -q`：17 passed
- `uv run pytest tests/api/test_callback_external_api.py -q`：34 passed
- `uv run pytest tests/architecture/test_git_quality_gate_architecture_profile.py -q`：2 passed
- `uv run pytest tests/load/ -q`：4 passed
- `uv run pytest tests/runtime/orchestration/test_phase3_p0_closure_contract.py -q`：7 passed
- `uv run pytest tests/runtime/orchestration/test_runtime_operational_contracts.py tests/deployment/test_runtime_inbox_celery_cutover.py -q`：RuntimeInbox SLI signal 与 Celery 批次发射合同
- `uv run pytest tests/runtime/orchestration/test_outbox_dispatch_observability.py -q`：1 passed
- `uv run pytest tests/runtime/orchestration/test_device_command_result_observability.py -q`：2 passed
- `uv run python scripts/run_runtime_benchmarks.py --output reports/benchmarks/runtime-benchmark.json --environment local-lightweight --generated-at 2026-07-02T12:00:00Z`：通过
- `uv run pytest tests/resilience/test_phase3_scenario_replay.py -q`：3 passed
- `./scripts/git-quality-gate.sh --check runtime-toggle-release`：通过
- `uv run ruff check .`：All checks passed
- `uv run ruff format --check .`：700 files already formatted
- `./scripts/architecture-guardrails.sh --phase phase1`：violations 0, warnings 0
- `./scripts/import-linter-check.sh`：Contracts: 1 kept, 0 broken
- `./scripts/git-quality-gate.sh --profile quality`：通过
- `npx gitnexus detect-changes --scope all`：tracked changes risk low，affected processes 0

**Phase 3 本分支增量验证证据（2026-07-03）**：

- `uv run pytest tests/runtime/orchestration/test_phase3_p0_closure_contract.py -q`：10 passed
- `uv run pytest tests/resilience/test_phase3_scenario_replay.py -q`：3 passed
- `uv run pytest tests/resilience/test_phase3_integration_lab.py -q`：2 passed
- `uv run pytest tests/runtime/orchestration/test_conveyor_queue_membership_writer_service.py -q`：8 passed
- `RUN_WORKLINE_INTEGRATION=1 ALLOW_SHARED_DEV_DB_INTEGRATION=1 INTEGRATION_DATABASE_URL=<local-postgres> INTEGRATION_REDIS_URL=<local-redis> uv run pytest tests/integration/test_phase3_conveyor_queue_membership_concurrency.py -q`：1 passed
- `uv run pytest tests/load/ -q`：4 passed
- `uv run pytest tests/runtime/orchestration/test_phase3_p0_closure_contract.py -q`：18 passed
- `uv run ruff check src/app/runtime/orchestration/p0_e2e_gate.py scripts/check_runtime_production_e2e_gate.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py`：All checks passed
- `uv run ruff format --check src/app/runtime/orchestration/p0_e2e_gate.py scripts/check_runtime_production_e2e_gate.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py`：3 files already formatted
- `uv run pytest tests/runtime/orchestration/test_phase3_p0_closure_contract.py tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py tests/runtime/orchestration/test_production_closure_evidence_gate.py tests/load/ -q`：33 passed
- `uv run ruff check src/app/runtime/orchestration/benchmark_gate.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py tests/runtime/orchestration/test_production_closure_evidence_gate.py`：All checks passed
- `uv run ruff format --check src/app/runtime/orchestration/benchmark_gate.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py tests/runtime/orchestration/test_runtime_benchmark_artifact_composer.py tests/runtime/orchestration/test_production_closure_evidence_gate.py`：4 files already formatted
- `uv run pytest tests/runtime/orchestration/test_production_e2e_artifact_composer.py tests/runtime/orchestration/test_phase3_p0_closure_contract.py -q`：21 passed
- `uv run ruff check src/app/runtime/orchestration/p0_e2e_artifact_composer.py scripts/compose_runtime_production_e2e_artifact.py tests/runtime/orchestration/test_production_e2e_artifact_composer.py`：All checks passed
- `uv run ruff format --check src/app/runtime/orchestration/p0_e2e_artifact_composer.py scripts/compose_runtime_production_e2e_artifact.py tests/runtime/orchestration/test_production_e2e_artifact_composer.py`：3 files already formatted
- `uv run pytest tests/runtime/orchestration/test_production_closure_evidence_gate.py -q`：7 passed
- `uv run ruff check src/app/runtime/orchestration/phase3_closure_gate.py scripts/check_runtime_production_closure_gate.py tests/runtime/orchestration/test_production_closure_evidence_gate.py`：All checks passed
- `uv run ruff format --check src/app/runtime/orchestration/phase3_closure_gate.py scripts/check_runtime_production_closure_gate.py tests/runtime/orchestration/test_production_closure_evidence_gate.py`：3 files already formatted
- `uv run python scripts/run_runtime_benchmarks.py --output reports/benchmarks/runtime-benchmark.json --environment local-lightweight --generated-at 2026-07-03T12:00:00Z`：通过
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q`：4 passed
- `uv run pytest --collect-only -q -o addopts='' | tail -5`：1636 tests collected
- `./scripts/git-quality-gate.sh --profile quality`：通过

**回归基线**（2026-06-27 在 develop @ `5b67797` 验证）：

- `uv run pytest`：2668 passed, 33 skipped, 2 xfailed in 251.21s
- `uv run ruff format --check .`：721 files already formatted
- `uv run ruff check .`：All checks passed
- Phase 0 / Phase 1 全部行为契约测试与架构护栏测试绿灯

### 10.1 Phase 0: 目标态锁定（7 项必做） — ✅ 已完成（PR #63）

**目标**：锁定 P0 系统目标和目标态边界，防止后续实现被旧 WorkLine/plugin 形态反向约束。

| 顺序 | Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- | --- |
| 1 | **P0-001 目标态契约文档** | M | 本文档 + `docs/architecture/target-state-contract.md` | 明确业务能力语义、域边界、状态所有权、允许破坏性删除范围 |
| 2 | **P0-002 Legacy 清理矩阵** | M | `docs/architecture/legacy-cleanup-matrix.md` | 每个旧模块标记 delete / rebuild / move / keep-contract |
| 3 | **P0-003 行为契约测试基线** | L | `tests/` | 覆盖 start admission、runtime snapshot、handoff、resource projection、粗分机正常入库、满箱交换前置分流、分拣机入库三场景业务基线的目标语义 |
| 4 | **P0-004 ExecutionCorrelation 迁移矩阵** | L | `docs/architecture/session-correlation-matrix.md` | per-file 迁移路径发布；跨域 FK 收敛策略明确 |
| 5 | **P0-005 ECS 设备接入边界合同** | M | `docs/integration/third_party_integration_whitepaper.md`, `docs/architecture/device-command-contract.md` | 明确 WES 只经 ECS API 下发业务命令；Event_Push 只 ACK；不与 PLC 通讯；RUNNING 有界等待；ERROR/OFFLINE 短退避 |
| 6 | **P0-006 IntegrationLab / 外部合同联调基线** | M | `docs/architecture/integration-lab-and-simulator.md`, `docs/contracts/external-contract-profile.md` | WMS/ECS simulator、sandbox provider profile、contract fixture、scenario runner 基线发布；只允许走正式 port contract |
| 7 | **P0-007 Architecture guardrails 基线** | M | `docs/architecture/architecture-guardrails-spec.md`, `scripts/architecture-guardrails.sh`, `tests/architecture/` | §7.5 核心 5 条 + I3 capability 注入/import 边界扫描、跨域 FK 扫描、DeviceCommand 字段白名单、schema 字段校验、RuntimeInbox 状态机契约测试可运行 |

**Phase 0 完成门禁**（2026-06-25 PR #63 全绿）：

- [x] `target-state-contract.md` 发布，不含旧 API/旧表兼容承诺
- [x] `legacy-cleanup-matrix.md` 发布，旧 WorkLine/plugin/runtime 每个入口都有处理策略
- [x] 行为契约测试可运行，保护业务语义而非旧代码形态
- [x] 粗分机正常入库、满箱交换前置分流、分拣机入库三场景基线被描述为目标态能力，不引用旧 plugin 接口、旧 context schema 或 fake allocator
- [x] `session-correlation-matrix.md` 发布
- [x] `device-command-contract.md` 发布，字段与白皮书 Command-Ack-Callback 一致
- [x] `external-contract-profile.md` 与 `integration-lab-and-simulator.md` 发布，明确 simulator/sandbox/replay/profile 不进入生产 fallback、不绕过正式 port contract
- [x] `architecture-guardrails-spec.md` 发布，逐条映射 §7.5 核心 5 条不变量 + I3 capability 注入/import 边界到脚本/测试/失败示例
- [x] `scripts/architecture-guardrails.sh` 可本地运行并纳入后续 Phase 门禁；核心 5 条不变量 + I3 capability 注入/import 边界有自动检查路径

**Phase 0 实际落地证据**：

- 文档：`docs/architecture/target-state-contract.md`、`legacy-cleanup-matrix.md`（707 entries，0 pending-review / 0 empty strategy）、`session-correlation-matrix.md`、`device-command-contract.md`、`integration-lab-and-simulator.md`、`architecture-guardrails-spec.md`
- 行为契约：`ed6e7b5 test(workline): P0-003 行为契约测试基线 (10 BC, 28 pass + 3 strict xfail)`
- 护栏脚本：`scripts/architecture-guardrails.sh` + `scripts/architecture-guardrails.allowlist`（phase-aware enforcement）
- 护栏测试：stable architecture boundary guardrail tests、`test_capability_dependency_guardrail.py`、`test_legacy_matrix_contract.py`

### 10.2 Phase 1: 目标态骨架与 WMS ACL — ✅ Packet A/B/C + Packet D 完成（PR #64 + PR Packet D + 本分支 callback admission 收敛）

**目标**：先建立目标态骨架和 runtime/orchestration 最小运行骨架，不迁移旧执行入口。Phase 1 的完成标准是“runtime 能独立接收 inbox、记录 intent、关联 correlation”，不是 P0 最小可运行闭环，也不是旧 WorkLine/plugin/runtime 已经清空。

| Task | Effort | 关联文件 | 验证 | 状态 |
| --- | --- | --- | --- | --- |
| **CEO-001** 整理 `wms_integration/` 并补齐 WMS 能力面 ports | M | `src/app/wms_integration/ports/` | 能力面 port 单元测试；覆盖 `wms_rcs_interface_requirements.md` P0 接口映射；内部业务域无 WMS DTO/client import（callback ACL 域和 legacy 标注豁免） | 🟡 3/7（MasterData/InventoryQuery/InventoryTransaction 已落，Document/Fulfillment/Event/ReconciliationQuery → Packet D） |
| **CEO-002** 4 方案决策表归档 | S | 本文档 §3.8 | 已归档 | ✅ 已完成 |
| **CEO-005** 查询响应 schema 增加 `scope/authority/source/evidence_at` 强制字段 | S | `src/app/*/schemas/` | schema 校验 + 测试；外部权威 QueryPort response 含 `source_version` | ✅ Packet A |
| **CEO-006** Authority Matrix 文档发布 | S | `docs/architecture/authority-matrix.md` | 11 类事实类型 + 权威来源（对齐 `target-state-contract.md` §4） | ✅ Packet A |
| **CEO-007** runtime/orchestration 最小骨架 | M | `src/app/runtime/orchestration/`, `docs/architecture/runtime-orchestration-spec.md` | ExecutionSession / ExecutionCorrelation / ExecutionWorkItem / RuntimeInbox / RuntimeTimeline / RuntimeHold / RuntimeIntentLog 7 个 runtime core 实体类型分离；对象级 work item 不被 session 串行锁阻塞；最小 worker + 单元测试 | ✅ Packet C |
| **CEO-008** `ConveyorQueueMembership` 目标模型 | M | `src/app/runtime/orchestration/`, `src/app/workline/` | manifest queue_code 校验 + active 唯一约束测试 | ✅ Packet C |
| **CEO-009** `RuntimeCapabilityContext` / `CapabilityPortRegistry` | M | `src/app/runtime/`, `src/app/contracts/external_contract_profile.py` | capability 只能拿到 query/effect port contract；静态检查拒绝 `wms_integration` / `device` service、HTTP client、DTO、provider exception、service locator、`WmsEventPort`、`DeviceEventPort`、`RuntimeInbox` consumer | ✅ Packet D + callback admission 收敛（capability_port_registry + InboundNormalizerRegistry + `INBOUND_NORMALIZER_OWNERSHIP` 静态扫描 + import-linter capability-isolation contract + 7/7 WMS ports 全部落地；callback API normalizer admission 已接入热路径） |
| **CEO-010** `DeviceCommand` ECS API contract + manifest concurrency limit | M | `src/app/device/`, `docs/architecture/device-command-contract.md` | command_code 幂等、dispatch 前 IDLE 校验、RUNNING 有界等待、ERROR/OFFLINE 短退避、Event_Push 只 ACK、缺 event_id 不推进、in-flight 限制测试；DeviceRuntime 状态快照 TTL 与 DeviceDispatchPolicy 纳入 manifest/schema 设计并通过 validator 测试；`awaiting_command_id` 迁移为 `awaiting_device_command_code`（值为 `DeviceCommand.command_code`，无 device FK），移除 device ↔ session FK 环并验证 Alembic upgrade/downgrade | ✅ Packet C（`3a6a7e29 feat(device): Phase 1 DeviceCommand 接入 ExecutionCorrelation + H4 反注入` + `9b74b6e6 feat(migrations): Phase 1 AP2 device FK ring dissolve`） |
| **CEO-011** WorkLine manifest version pin | M | `src/app/workline/`, `src/app/runtime/orchestration/` | RUNNING session 固定 manifest_version；新 manifest 只影响新 session；activation-time validator 测试 | ✅ Packet A |
| **CEO-012** WorkLine SafetyZone / shared-device manifest schema | M | `src/app/workline/`, `src/app/device/` | shared device 影响范围、required/optional role、SafetyZone validator 测试 | ✅ Packet A |
| **CEO-013** ExternalContractProfile + provider simulator registry | M | `src/app/contracts/`, `src/app/wms_integration/`, `src/app/device/`, `docs/contracts/external-contract-profile.md` | ExternalContractProfile / RuntimeCapabilityProfile / InboundNormalizerProfile 生产路径位于 `src/app/contracts/` 共享层；WMS/ECS provider profile、contract tests、fixture set 与 simulator registry 可运行；adapter/normalizer 不泄漏外部 DTO | ✅ Packet B（`src/app/contracts/external_contract_profile.py` + `src/app/wms_integration/provider_simulator_registry.py` + ADR-0009） |

**Phase 1 Packet A/B/C 落地快照**（2026-06-27 PR #64）：

- [ ] `wms_integration` MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / ReconciliationQuery 7 个目标 port 全部实现 🟡 3/7
- [ ] `wms_rcs_interface_requirements.md` P0 基础数据、业务指令、回调事件均映射到目标 port 🟡 已映射 MasterData / InventoryQuery / InventoryTransaction；其余 4 → Packet D
- [x] 内部业务域无代码直接 import WMS 类型；callback ACL 域和 legacy matrix 标注豁免项按 drop_phase 继续受 allowlist 管控（`WMS_INTEGRATION_BOUNDARY` 护栏测试通过）
- [ ] Runtime capability 注入仅暴露 query/effect port contract，不暴露 `wms_integration` / `device` service、HTTP client、DTO、provider exception、service locator、`WmsEventPort`、`DeviceEventPort` 或 `RuntimeInbox` consumer；inbound normalizer 不进入业务 capability 🟡 类已实现，静态扫描入 Packet D
- [x] Authority Matrix 文档发布（Packet A）
- [x] runtime/orchestration 最小骨架完成，RuntimeIntentLog 含 effect ledger 字段并支持崩溃重放（Packet C + H5 IdempotencyGuard）
- [x] DeviceCommand 只面向 ECS API，不包含 PLC/坐标/关节/安全回路字段；dispatch 前必须校验 ECS 设备状态为 IDLE（`DEVICE_COMMAND_BOUNDARY` 护栏测试通过）
- [x] `awaiting_command_id` 已迁移为 `awaiting_device_command_code`（值为 `DeviceCommand.command_code`，无 device FK），device ↔ session FK 环已消解且 Alembic upgrade/downgrade 通过（`9b74b6e6` + `ede4a2ca`）
- [x] DeviceRuntime 状态快照 TTL 与 DeviceDispatchPolicy 已纳入 manifest/schema 设计
- [x] ExecutionSession 已 pin `manifest_version`（Packet A）
- [x] 动态队列 membership 模型替代旧 8 enum 方案（Packet C `conveyor_queue_membership.py`）
- [x] WorkLine manifest 能表达 SafetyZone、共享设备和影响范围；WES 不包含 PLC 直连字段（Packet A）
- [x] ExternalContractProfile 覆盖 WMS/ECS 初始 provider，contract fixture 可被 simulator 与 adapter contract tests 复用（Packet B）
- [ ] provider 未声明的 query/effect 能力无法进入 `RuntimeCapabilityContext` 🟡 Profile 类已定义，静态拒绝检查入 Packet D
- [x] provider 未声明的 callback/event/result normalizer 能力无法进入 callback API；`WmsEventPort` / `DeviceEventPort` / `RuntimeInbox` consumer 不会注入业务 capability（callback API result/event/external 热路径接入 provider profile admission）

**Phase 1 Packet D 完成门禁**（PR Packet D 2026-06-27 落地）：

- [x] `wms_integration` 7/7 目标 port 全部实现（MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / ReconciliationQuery）
- [x] `wms_rcs_interface_requirements.md` P0 基础数据、业务指令、回调事件全部映射到目标 port
- [x] Runtime capability 注入仅暴露 query/effect port contract；inbound normalizer（WmsEventPort / DeviceEventPort / RuntimeInbox）不进入业务 capability，RuntimeInbox entity/repository/service 仅按 guardrail 逐文件例外持有
- [x] provider 未声明的 query/effect 能力无法进入 `RuntimeCapabilityContext`（`CAPABILITY_IMPLEMENTATION_IMPORT` 静态扫描 + `RuntimeCapabilityContext` provider profile admission 落地）
- [x] provider 未声明的 callback/event/result normalizer 能力无法进入 callback API；`InboundNormalizerProfile` 3 Pydantic model_validator 拒绝裸字符串 event_type / source_provider 不一致 / 非枚举 correlation_resolution，callback API result/event/external 热路径调用 `ExternalContractProfile.ensure_inbound_normalizer_declared()` 并在未声明时拒绝写入 inbox。
- [x] `INBOUND_NORMALIZER_OWNERSHIP` 静态扫描器覆盖 `src/app/runtime` + `src/app/workline`，inbound normalizer 类型持有者 0 违规
- [x] import-linter `capability-isolation` contract 启用，`capability_port_registry` 不得 import wms_integration/device/callback/orchestration 子模块

**Phase 1 Packet D 实际落地证据**（PR Packet D 提交列表 9 atomic commit）：

- `e42f551 chore(deps): add import-linter dependency for capability-isolation contract`
- `c453e5f`：历史上新增粗粒度 document protocol；当前已由 operation-specific document Definitions 取代
- `051f4aa`：历史上新增粗粒度 fulfillment protocol 与 typed data classes；当前已由 operation-specific contracts 取代
- `8a6cc52 feat(wms-ports): add WmsEventPort protocol with 4 normalizers + InboundEventPort base`
- `72e1f7f feat(wms-ports): add WmsReconciliationQueryPort protocol + 1 typed data class`
- `8749ef3 feat(contracts): harden InboundNormalizerProfile with injection boundary validators`
- `7682313 feat(runtime): add InboundNormalizerRegistry + consumer-only InboundNormalizerContext`
- `f0be12e feat(architecture): enforce R-I3c inbound normalizer port guardrail + import-linter capability-isolation contract`
- `81b6491 fix(architecture): extend rule_ri3c exclusion to cover full orchestration layer`
- `tests/architecture/test_wms_7_ports_contract.py` — 7 ports 全部 contract 测试通过
- `tests/architecture/test_inbound_normalizer_profile_validation.py` — 3 Pydantic validators 6 测试通过
- `tests/architecture/test_runtime_capability_context_routing.py` — Registry + 3-step 错误优先级 7 测试通过
- `tests/architecture/test_inbound_normalizer_ownership_guardrail.py` — `INBOUND_NORMALIZER_OWNERSHIP` 扫描器 + import-linter 测试通过

**Phase 1 Packet A/B/C 实际落地证据**（PR #64 提交列表）：

- `aa6f6c99 feat(workline): Phase 1 Packet A Foundation (CEO-005/006/012 + AP5)`
- `68805a7f` / `0043ed22 feat(workline): Phase 1 Packet B 部分交付 (AP1 + AP2 + H4 + ADR-0009)`
- `f0724894 feat(workline): Phase 1 Packet B CEO-013 ExternalContractProfile + simulator registry`
- `2e7d7cf0 feat(wms_integration): Phase 1 Packet B CEO-001 三大 port 起步 (WmsMasterData / WmsInventoryQuery / WmsInventoryTransaction)`
- `5ccdefba feat(workline): Phase 1 Packet C 起步 (CEO-007 ExecutionSession + ExecutionCorrelation + ExecutionWorkItem)`
- `7eaf5ae0 feat(workline): Phase 1 Packet C RuntimeInbox + RuntimeIntentLog`
- `d8eadbc5 feat(workline): Phase 1 Packet C 7/7 实体 + H5 idempotency_keys`
- `a7f64880 feat(runtime): Packet C 7/7 实体补 FK/Index 完整化 + BC-02 RuntimeSnapshotAdmission`
- `181363d2 refactor(workline): service 层接入 runtime/orchestration (Phase 1)`
- `85b160e6 refactor(cross-domain): callback / handling / rack 接入 runtime/orchestration`
- `9b74b6e6 feat(migrations): Phase 1 AP2 device FK ring dissolve + runtime table`
- `ede4a2ca fix(migration): FK ring dissolve 动态发现 + correlation_id 回填 + conveyor queue`
- `25dbe826 feat(runtime): 实现 H5 IdempotencyGuard 最小语义 (Phase 1)`
- `3a6a7e29 feat(device): Phase 1 DeviceCommand 接入 ExecutionCorrelation + H4 反注入`
- `165711fd fix(callback): 外部回调 H4 边界 WMS 协议白名单扩展 + 子层守卫`
- `9c790d53 fix(guardrails): 修复 C4 scanner 误报 H4 反注入实现`

### 10.3 Phase 2: Runtime/Orchestration 迁移与 WorkLine 清空 — ✅ restructuring cleanup 完成，WorkLine 只保留配置域

**目标**：在 Phase 1 新 runtime/orchestration 骨架已独立可运行后，把旧 WorkLine/plugin/runtime 的执行状态、inbox、timeline、hold、effect dispatch 迁出或删除。旧执行入口不做兼容转发。

**2026-07-08 收敛进展**：`WorkLineRuntimeStatusProjectionService` 已成为 runtime/orchestration 原生投影入口。RuntimeHold release、resource reconciliation、callback deadline reconciliation、START admission、safety estop 与 dispatch ACK exhausted 路径均通过该服务投影；safety 接收校验、START admission、query/trace 展示均通过 snapshot/readiness 读取运行状态。restructuring cleanup migration 已完成回填并删除 WorkLine 运行态物理列。

**启动条件**（满足全部才能启动 Phase 2）：

- [x] Phase 0 全部 7 项完成（PR #63 `v0.9.0.0` 2026-06-25）
- [x] Phase 1 全部任务完成。Packet A/B/C（PR #64 2026-06-27）+ Packet D（PR #66 `v0.9.1.0` 2026-06-27）主体已合并；callback API 热路径 provider profile admission 已在 §10.2 Packet D 完成门禁关闭。
- [x] 重新跑 autoplan 或同等深度评审，确认 B 方案可执行（autoplan CONDITIONAL-GO 2026-06-28，见 `~/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260628-121500.md`）

#### 10.3.1 B 方案暂停/回退条件（EXECUTION_CORRELATION_BOUNDARY 回归）

Phase 2 启动前必须执行 go/no-go 评审。以下任一条件成立时，暂停 B 方案，不进入 XL 级重建：

- Phase 0 P0-003 行为契约测试未覆盖关键业务语义，核心路径覆盖率低于 70%。
- Phase 1 CEO-007 runtime/orchestration 最小骨架无法在不污染状态源的前提下落地。
- 重新评审发现 2 个及以上 P0 阻塞项，或发现需要重新定义 WES/WMS/ECS 边界的基础假设错误。
- legacy cleanup matrix 中存在无法归类为 delete / rebuild / move / keep-contract 的核心入口。

**回退路径**：

| 路径 | 保留资产 | 追加成本 | 目标 |
| --- | --- | --- | --- |
| B -> C | 保留 `wms_integration` 7 port、External callback 鉴权、idempotency、行为契约测试 | 2-3 周 | 先完成 ACL 与外部边界，暂缓 runtime/orchestration 全量拆分 |
| B -> D | 保留 WorkLine manifest、动态队列模型、DeviceCommand ECS contract | 3-5 周 | 暂时保留 workline 单体运行入口，但删除旧 plugin 扩展方式 |
| B 暂停 | 保留 Phase 0/1 文档、测试和 schema 骨架 | 1 周内出复盘 | 重新评审目标态边界，不继续投入 Phase 2 |

回退不代表恢复旧 API/旧表兼容；只代表缩小重构范围，优先保留已验证的目标态契约和新边界。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| Runtime/Orchestration 完整迁移 | XL | `src/app/runtime/orchestration/` | Timeline / Hold / EffectPort / worker / replay / dead-letter 补齐，旧 session 语义迁移到 ExecutionCorrelation |
| WorkLine 执行逻辑清空 | L | `src/app/workline/` | WorkLine 仅保留配置 CRUD、manifest、plane scene |
| Legacy 执行入口删除 | M | `src/app/workline/services/`, `src/workline_runtime/` | 旧入口被删除或替换；行为契约测试仍通过 |

**Phase 2 完成门禁**：

- [x] `runtime/orchestration` 域独立落地（launch PR commit `d5b88562` facade bridge + `8eab4042` 跨域 import 修复）
- [x] WorkLine 不再拥有运行状态。阶段 6 / F-1 / F-2 已完成 service/v1 router 域清空、facade 物理删除、device_command_gateway 迁出、14 model + 10 repository 物理迁入 `runtime/orchestration/{models,repositories}/`；restructuring cleanup 已删除 WorkLine 运行态物理列，写入口集中到 `WorkLineRuntimeStatusProjectionService` 的 runtime/orchestration 投影，WorkLine 域和 material-flow capability 不再直接写入。
- [x] legacy 行为契约测试通过（launch PR commit `8602c33b`：`tests/contracts/workline/` 107 passed, 2 xfailed）

**Launch PR 8 commit 落地清单（feature/phase2-launch, 8602c33b）**：

| # | Commit | 内容 | 关联门禁 |
|---|---|---|---|
| 1 | `2e7715a2` | `chore(guardrails): pre-commit 默认 ARCHITECTURE_PHASE=phase1 + env 注入` | guardrail 默认 phase1 |
| 2 | `26452fb9` | `fix(runtime): InboundNormalizerRegistry async/thread safety + 并发单测` | async safety |
| 3 | `57de91ff` | historical commit: src.workline_runtime production import guardrail + allowlist | legacy runtime import boundary |
| 4 | `9bd29f03` | historical commit: inbound normalizer guardrail scope 扩展到 callback/wms_integration/services/device | inbound normalizer ownership boundary |
| 5 | `ca1fe853` `aef4366c` `d5b88562` `8eab4042` | 跨域 import 修复 4 commit（callback/utils mirror → 4 callback services 切到 callback.contracts/utils → RuntimeReconciliationFacade bridge → device_command_service 跨域切到 facade） | 9 处跨域 import |
| 6 | `123f57c9` | `docs(architecture): Runtime ownership map + ADR-0001` | ownership map + ADR |
| 7 | `8602c33b` | `test(contracts): 8 个 Phase 2 behavior contract gaps (TDD 同步)` | 8 contract gap |
| 8 | ✅ 已落地 | `docs(architecture): legacy-runtime-migration-spec.md + Phase 2 §10.3 同步` | migration spec（`legacy-runtime-migration-spec.md` 已存在）+ §10.3 启动条件 |

详见 [`./legacy-runtime-migration-spec.md`](./legacy-runtime-migration-spec.md) §2 / §3 / §5 / §6。

### 10.4 Phase 3: 执行安全与恢复能力补全 — 🟡 PR #73 部分完成

**目标**：补全支持 WES 作业期可信恢复、对账、安全的子能力。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| **ENG-002** `ReconciliationManager` + RECONCILING 决议模型 | M | `src/app/reconciliation/` | owner-scoped resolution decision + 5/30 分钟升级 + unit test |
| **ENG-003** WorkLine 启动 manifest validator | M | `src/app/workline/` | 集合差检测 + RuntimeHold 拒写 |
| **ENG-004** 11 态机补 4 条 timeout + CB `BLOCKED_BY_CB` | M | `src/app/wms_integration/state_machine.py` | 4 timeout 转移 + CB 出站阻塞集成测试；CB `open/half-open` 期间 late callback 仍写 inbox/evidence 并进入幂等合并或 RECONCILING；RuntimeInbox 热路径迁移另列剩余缺口 |
| **ENG-006** `ActiveObjectRegistry` 跨投影 active 归属仲裁读模型 | M | `src/app/active_objects/` | 3 路 UNION 冲突 policy 单元测试 |
| **ENG-008** External callback body HMAC + nonce TTL + allow-list | M | `src/app/callback/`, `src/app/wms_integration/`, `src/app/device/`, `src/core/api_security.py` | WMS ordinary event/status hint、ECS/device 与 AGV 重放 + 篡改 + 时钟偏差 + allow-list 测试 |
| **ENG-009** idempotency_key 复合主键 + request_hash + session 审计 | M | `src/app/runtime/`, `src/app/wms_integration/`, `src/app/device/` | callback / fulfillment / device_command / device_event / reconciliation 409 + 安全审计事件 |
| **ENG-010** typed `ExternalReference` + typed evidence envelope + WMS drift job | M | `src/app/wms_integration/evidence/`, `docs/contracts/evidence-catalog.md` | GIN 索引 + drift 分类 |
| **ENG-011** RuntimeInbox backpressure + DeviceCommand lease | M | `src/app/runtime/`, `src/app/device/` | inbox 积压降级、死信/人工重放、per-device in-flight 限制、过期 lease 重放/取消测试 |
| **ENG-012** DeviceDispatchPolicy + DeviceRuntime TTL | M | `src/app/device/`, `src/app/runtime/`, WorkLine manifest | 设备能力选择、优先级、deadline、RUNNING 有界等待、状态快照过期重查 ECS、HOLD/RECONCILING 取消测试 |
| **ENG-013** RECONCILING 现场隔离语义 | M | `src/app/reconciliation/`, `src/app/runtime/` | 进入 RECONCILING 后禁发新 effect、冻结 projection 写入、人工恢复审计测试 |
| **ENG-016** Conveyor queue writer 并发幂等与诊断 | M | `src/app/runtime/orchestration/`, `src/app/workline/`, `src/app/reconciliation/` | PostgreSQL 锁/upsert、IntegrityError 重读、placeholder resolve、严格模式测试 |
| **ENG-017** 满箱/换箱/换架 callback + reconciliation 语义 | M | `src/app/handling/`, `src/app/reconciliation/` | FULL_BOX/RACK_BIN exchange 外部成功但本地冲突进入 RECONCILING 的合同测试 |
| **ENG-018** WMS evidence retention + breaker observability | M | `src/app/wms_integration/`, observability 配置 | retention/archive、breaker OPEN/HALF_OPEN、evidence 写入失败指标测试 |
| **ENG-019** Runtime worker / queue / ECS HTTP benchmark gate | M | `tests/load/`, docs | RuntimeInbox claim、queue writer、status GET、command POST 基准报告 |
| **ENG-020** ScenarioRecorder / ScenarioReplayRunner | M | `src/app/runtime/`, `src/app/reconciliation/`, `tests/resilience/` | 脱敏场景录制、deterministic replay、projection diff、timeline/outbox/reconciliation 断言 |
| **ENG-021** Observability contract + OpenTelemetry attributes | M | observability 配置, `docs/contracts/observability-contract.md` | span/metric/log name 与稳定 attributes 通过契约测试；禁止临时日志字段替代观测合同 |
| **ENG-022** Typed ops/release toggle governance | S | `src/core/`, `docs/contracts/runtime-toggle-governance.md` | toggle owner/expiry/scope/default/rollback/test matrix 校验；过期 toggle 阻塞发布；禁止绕过安全/幂等/evidence |
| **DESIGN-001..005** 5 项 design 修复（schema_version、scene/snapshot 独立、目标态枚举、label/code 分离、极态清单） | 4×S + 1×M | `src/app/workline/v1/plane.py`, `src/app/workline/schemas/` | 单元测试 |

**Phase 3 任务状态同步（2026-07-03）**：

| 任务 | 状态 | PR #73 / 本分支完成范围 |
| --- | --- | --- |
| ENG-002 | ✅ 已完成 | `ReconciliationManager` owner-scoped 决议、hold/freeze action、人工恢复审计最小合同 |
| ENG-003 | ✅ 已完成 | WorkLine manifest activation validator，防止已知 queue_code typo 污染 active projection |
| ENG-004 | ✅ 已完成 | 11 态状态机、终态保护、CB / late callback 合同已落地；本分支补齐 4 类 timeout 与 current-state-aware 可观察转移矩阵，并覆盖 CB open/half-open 只阻断出站 effect、不覆盖在途状态、OPEN fast-fail 与 HALF_OPEN trial-in-progress 二次 effect 不打 HTTP 的集成矩阵 |
| ENG-006 | ✅ 已完成 | `ActiveObjectRegistry` 跨投影 active 归属仲裁读模型 |
| ENG-008 | ✅ 已完成 | callback body HMAC、nonce 原子消费、body hash、`API_PATH` 前缀和 fail-closed 已落地；WMS 仅保留普通事件与 `WMS_EFFECT_STATUS_HINT`，EFFECT hint 不写终态；统一 external callback 入口校验 WMS、ECS/device 与 AGV allow-list/source mismatch，并物理关闭 WMS↔CTU 逐箱入站族 |
| ENG-009 | ✅ 已完成 | RuntimeInbox source event 幂等、payload hash 冲突、唯一冲突重读和审计已覆盖；本分支补齐 `IdempotencyOperationSpec` canonical/alias 审计矩阵、`IdempotencyConflict` 409 payload、ReconciliationManager `register_conflict_idempotent()`、runtime reconciliation `TIMER_TIMEOUT` / dispatch ACK exhausted 热路径 claim、WMS fulfillment `open_request_idempotent()` opening 入口、RuntimeIntent `EXTERNAL_REQUEST` fulfillment 实际发起热路径 claim 与 RuntimeInbox device_event `IdempotencyKey` claim |
| ENG-010 | ✅ 已完成 | typed evidence envelope 已落地；本分支补齐 typed `ExternalReference` catalog、source-version drift 分类合同、`WmsCallEvidence` JSONB GIN 索引、只读 WMS drift job 和 `docs/contracts/evidence-catalog.md` |
| ENG-011 | ✅ 已完成 | RuntimeInbox backpressure、死信/人工重放审计、DeviceCommand 可过期 lease 和 recovery 策略 |
| ENG-012 | ✅ 已完成 | 本分支补齐 `DeviceDispatchPolicy` 纯策略合同并接入 `DeviceCommandGateway.dispatch` 热路径：fresh busy/hard-state 本地快照短路，stale/UNKNOWN 保留 ECS status probe，RUNNING deadline 到期暴露 `runtime_hold_required` decision detail，RECONCILING session freeze/cancel；状态快照 TTL 与全量 gateway 集成矩阵已由回归测试覆盖；`device_command.dispatch_policy` 生产 metrics 已纳入 observability contract；新增 `wes_runtime.device_runtime_projections` 持久 DeviceRuntime 投影、repository/writer service、Alembic 迁移，并由 `DeviceService._update_runtime_state` 同事务同步 |
| ENG-013 | ✅ 已完成 | RECONCILING 软件禁发、投影冻结和 owner-scoped 人工恢复审计合同 |
| ENG-016 | 🟡 代码合同完成，生产 profile 证据延后 | 本分支补齐 `ConveyorQueueWriter` 写入决策合同，并新增 runtime DB-backed `ConveyorQueueMembershipWriterService` / repository，覆盖幂等重放、placeholder resolve、跨队列 RECONCILING、strict-mode unknown queue、IntegrityError existing 重读、结果诊断和 `integrity_conflict_recheck_count` lightweight benchmark artifact 口径；本分支继续补齐 PostgreSQL `FOR UPDATE` active identity 锁语义和 opt-in PostgreSQL unique-race existing 重读合同；当前开发/测试默认使用 MOCK closure，生产规模高并发真实基准数据延后到 `--closure-profile production` 发布前补齐 |
| ENG-017 | ✅ 已完成 | 本分支将 full-box / RACK_BIN exchange 合同从 strict xfail 转为真实合同，覆盖 callback+reconciliation completion policy、exchange outbox 包络、外部履约 evidence 与本地冲突进入 ReconciliationManager / membership RECONCILING 投影 |
| ENG-018 | ✅ 已完成 | typed evidence envelope 与 observability contract 已落地；本分支补齐 WMS breaker OPEN/HALF_OPEN/CLOSED transition instrumentation、typed port `trace_id` 透传、`wms_evidence.persistence_failure` 指标事件；WMS evidence 保留热表与 drift 能力，不维护专用 archive/retention，统一服从项目 retention、运维和容量策略 |
| ENG-019 | 🟡 代码合同完成，生产 profile 证据延后 | 本分支新增 `RuntimeBenchmarkGate` 必需场景清单合同，覆盖 RuntimeInbox claim、queue writer、ECS status command 和 plane snapshot；并补齐对应 `tests/load/` 轻量 benchmark 脚本、共享 scenario runner、structured artifact fixture、`validate_artifact()` gate、CLI artifact writer 与 `Jenkinsfile.backend-ci` 归档；本分支继续补齐 benchmark artifact profile metadata gate，区分 lightweight 与 production-scale，并要求 production-scale 明确 PostgreSQL backend、外部依赖 profile、并发度和持续时间；本分支继续收紧 production-scale artifact provenance/workload gate，禁止缺少 PostgreSQL / ECS HTTP / API HTTP 场景来源证据或 §8.3 基线规模 workload metadata 的 artifact 通过；本分支新增 `RuntimeBenchmarkArtifactComposer` / `scripts/compose_runtime_benchmark_artifact.py`，现场只能从四个真实场景 evidence 文件组装 production-scale artifact；当前开发/测试默认使用 MOCK closure，生产规模性能数据和真实外部依赖压测延后到 production closure profile |
| ENG-020 | ✅ 已完成 | 本分支新增 `ScenarioRecorder` / `ScenarioReplayRunner` 脱敏录制和 deterministic replay 合同，并补齐 `tests/resilience/fixtures/runtime_replay_fixture.json`、`phase3_simulator_replay_fixture.json`、TraceQueryResult 生产录制源适配 + `tests/resilience/test_phase3_scenario_replay.py` 显式回放；本分支继续补齐 replay result active projection diff、`IntegrationLabScenarioRunner`、`phase3_integration_lab_fixture.json` 与 ECS external contract fixture set，覆盖 WMS/ECS sandbox profile、完整链路事件类型和乱序/重复/超时/拒绝/断网场景 |
| ENG-021 | ✅ 已完成 | `docs/contracts/observability-contract.md` 与契约测试已落地；本分支新增 `RuntimeObservabilityRegistry` 稳定 attributes 校验、observer 发射入口、`RuntimeOpenTelemetryBridge` exporter fan-out、`RuntimeOpenTelemetryHttpExporter` backend adapter 与 FastAPI lifespan 配置接线，并接入 callback normalize、WMS breaker transition、evidence persistence failure、DeviceCommand ACK age、DeviceCommand RESULT、RuntimeInbox claim 与 RuntimeIntent / Workline Outbox dispatch 运行时 instrumentation |
| ENG-022 | ✅ 已完成 | `docs/contracts/runtime-toggle-governance.md` 与契约测试已落地；本分支新增 `RuntimeToggleRegistry` owner/expiry/security-bypass validator、`RuntimeToggleReleaseGate`、`RUNTIME_TOGGLES` typed catalog 和 `scripts/check_runtime_toggle_release_gate.py`；发布阻塞已接入 `scripts/git-quality-gate.sh --check runtime-toggle-release` 与 quality profile |
| DESIGN-001..005 | ✅ 已完成 | plane scene/snapshot 独立读模型、schema version、label/code 分离和极态展示合同 |

**Phase 3 完成门禁**：

- [ ] P0 最小可运行闭环：以「分拣机入料 1 个料箱 → ECS 扫码 → WES 决策投箱 → 通知 WMS PKG 绑定 → PlaneSnapshot 可观察」为验证锚点，跑通 WorkLine manifest -> ExecutionSession -> RuntimeInbox -> RuntimeIntentLog -> DeviceCommand / WMS fulfillment -> PlaneSnapshot -> RECONCILING 链路；端到端 P95 < 30s；任一异常路径（ECS 超时、WMS 拒绝、callback 乱序）必须落入 RECONCILING 而非静默失败。🟡 PR #73 已完成关键基础合同；本分支新增 deterministic P0 closure 合同测试、ECS timeout / WMS reject 进入 reconciliation 的合同、IntegrationLab fixture-level WMS/ECS 完整链路 runner，以及 `RuntimeP0E2EGate` / `RuntimeP0E2EArtifactComposer` / `scripts/check_runtime_production_e2e_gate.py` / `scripts/compose_runtime_production_e2e_artifact.py` 生产 trace artifact 门禁；`RuntimeProductionClosureGate` / `scripts/check_runtime_production_closure_gate.py` 已把 P0 E2E artifact 与 production-scale benchmark artifact 合并为生产发布 profile 总门禁；当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项。
- [x] RECONCILING 不再是黑洞状态；owner-scoped resolution decision 有测试覆盖，且 ReconciliationManager 不直接写 owner 状态。
- [x] WorkLine 启动时已知 queue_code typo 不会污染 active projection。
- [x] 11 态机覆盖所有可观察转移。PR #73 已覆盖 11 态枚举、终态保护和核心 CB / late callback 语义；本分支补齐 4 类 timeout 与 current-state-aware 可观察转移矩阵。
- [x] CB `open/half-open` 只阻断出站 effect；late callback 不得被标记为 `BLOCKED_BY_CB`，必须经 RuntimeInbox 幂等合并 evidence，冲突时进入 RECONCILING。RuntimeInbox 已成为唯一事实源：callback/device/internal/timer producer 统一持久化后即时 enqueue，Celery 顺序 claim 并走三阶段 processor；旧 WorklineInbox 表、InboxBatchProcessor、RuntimeInboxConsumer facade、`enqueue_workline_inbox` 与 lifecycle-only 兼容路径均已物理删除，不保留双写或兼容 shim。
- [x] External callback 鉴权从"字段级"升级为"body 完整性级"，覆盖 WMS ordinary event/status hint、ECS/device 与 AGV 的统一校验路径。
- [x] idempotency 跨域语义统一，覆盖 callback / fulfillment / device_command / device_event / reconciliation。PR #73 已覆盖 callback / RuntimeInbox source event 幂等与 payload hash conflict；本分支补齐 canonical/alias 审计矩阵、409 audit payload、ReconciliationManager 幂等登记入口、runtime reconciliation `TIMER_TIMEOUT` / dispatch ACK exhausted 热路径 claim、WMS fulfillment 幂等 opening 入口、RuntimeIntent `EXTERNAL_REQUEST` fulfillment 实际发起热路径 claim 与 RuntimeInbox device_event claim。
- [x] RECONCILING 具备软件禁发、投影冻结和人工恢复审计。
- [x] DeviceCommand lease 与 RuntimeInbox backpressure 已覆盖。
- [x] DeviceDispatchPolicy、DeviceRuntime TTL 与持久 DeviceRuntime 投影已覆盖。本分支补齐 DeviceDispatchPolicy 纯策略合同、DeviceRuntime 状态快照 TTL、ECS status probe、dispatch 热路径全量矩阵回归测试，以及 `DeviceRuntimeProjectionWriterService` / `DeviceService` 同步合同。
- [ ] Conveyor queue writer 并发、幂等、诊断和严格模式已覆盖 PostgreSQL 语义。🟡 本分支补齐 writer 决策合同、DB-backed 写入、IntegrityError 重读、结果诊断、lightweight benchmark artifact 诊断口径、PostgreSQL `FOR UPDATE` active identity 锁合同和 opt-in PostgreSQL unique-race 测试；benchmark profile metadata gate 已阻止 lightweight artifact 冒充 PostgreSQL 生产基准，production artifact composer 已要求四个真实场景 evidence 文件输入；当前开发/测试默认使用 MOCK closure，生产规模高并发真实基准数据延后到 production closure profile。
- [x] 满箱/换箱/换架不再按普通 trusted callback 完成处理。本分支将 full-box / RACK_BIN exchange 合同转为真实合同，覆盖 exchange completion policy、outbox 包络、callback evidence 与 RECONCILING 投影。
- [x] WMS breaker/evidence、DeviceCommand ACK age、RuntimeInbox/Outbox 等关键指标已纳入观测口径。PR #73 已落地 typed evidence envelope 与 observability contract；本分支补齐 callback normalize instrumentation、WMS breaker/evidence instrumentation、retention/archive、exporter bridge、生产 backend adapter 接线、DeviceCommand ACK age instrumentation、DeviceCommand RESULT instrumentation、RuntimeInbox claim instrumentation 和 RuntimeIntent / Workline Outbox dispatch instrumentation。
- [x] IntegrationLab 能跑通 WMS/ECS simulator 的完整链路和乱序、重复、超时、拒绝、断网 fixture。本分支新增 fixture-level `IntegrationLabScenarioRunner`、WMS/ECS sandbox provider profile 校验、ECS external contract fixture set 和完整链路 replay 断言。
- [x] ScenarioReplayRunner 能 deterministic 复现关键异常并断言 active projection diff、timeline、outbox/effect 幂等和 reconciliation 结果。本分支补齐纯 replay 合同、resilience fixture、simulator fixture 回放、TraceQueryResult 生产录制源适配、active projection diff 输出和 IntegrationLab replay runner。
- [x] Observability contract 覆盖 WMS/ECS HTTP、callback normalize、RuntimeInbox claim、RuntimeIntentLog dispatch、DeviceCommand dispatch policy / ACK / RESULT 和 replay runner。
- [x] Typed toggle 清单无过期项；任何 toggle 都不能绕过 IDLE 准入、HMAC、idempotency、evidence 和 RuntimeHold。PR #73 已落地治理合同；本分支补齐 runtime validator、release gate、typed catalog 和 quality profile 自动门禁接线。

### 10.5 Material-flow target capabilities

**目标**：补全 WES 作业期完整业务语义。Material-flow capability 不阻塞 Phase 3 的 P0 技术闭环上线验证，但阻塞完整业务能力上线；任何仍承载未重建业务语义的 legacy 不能在对应 material-flow 能力验收前删除。

**范围调整（2026-07-05）**：本项目未发布，当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项。sorter inbound 与 SMT/NG/WMS reconciliation 后续目标是 **production-capable runtime path**，而不是把业务代码绑定到某个真实外设。WES 只面向 provider contract、RuntimeIntent、RuntimeInbox、idempotency、timeout/retry、evidence 与 RuntimeHold/Reconciliation；外部是真设备、sandbox、MOCK 还是 simulator，由部署 wiring 与 evidence profile 区分，不进入业务代码分支。Phase1 callback admission 已关闭；发布前必须显式运行 `--closure-profile production`，并通过 material-flow evidence profile gate。

**设计包（2026-07-03）**：

- `docs/superpowers/specs/2026-07-03-phase4-design-with-residuals.md`：Phase 4 umbrella design + Phase 1/2/3 Residual Readiness Register。
- `docs/architecture/cell-reservation-spec.md`：CellReservation P0 前置、现有 `WorklineBinCellReservation` 复用/演进、目标语义与现有状态映射、RECONCILING 持久化门禁。
- `docs/architecture/material-location-query-spec.md`：6 个查询入口、5 类来源优先级、ExternalReference/evidence 口径。
- `docs/architecture/workline-active-objects-spec.md`：`ActiveObjectRegistry` 协同、active/current view 归一化、冲突展示与 RECONCILING。
- `docs/architecture/sorter-inbound-capability-spec.md`：粗分机、满箱交换、分拣机入库目标态流程、CellReservation、CTU 批次状态与 terminal 成员 evidence 查询视图。
- `docs/architecture/smt-ng-wms-reconciliation-spec.md`：NG evidence、WMS 确认/拒绝、目标箱回写失败、版本冲突恢复、RuntimeHold 解除条件。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| `CellReservation` P0 前置 | M | `cell-reservation-spec.md` | 目标语义与现有 `WorklineBinCellReservation` 状态映射、唯一约束、TTL、RECONCILING 持久化门禁 |
| `MaterialLocationQuery` 查询服务 | M | `material-location-query-spec.md` | 6 入口 + 5 来源位置优先级 |
| `WorklineActiveObjects` / `WorklineCurrentWorkView` 查询服务（配合 ActiveObjectRegistry） | M | `workline-active-objects-spec.md` | 3 路 UNION 归一化 |
| 粗分机/满箱交换/分拣机入库能力目标态重建 | L | `sorter-inbound-capability-spec.md` | 按行为契约重建粗分机正常入库、满箱交换前置分流、分拣机入库、对象级流水并发、WMS 校验、箱格分配/预约、滚筒线路由、NG、投箱、本地物理事实先落与 WMS 同步对账；不复用旧 plugin 入口 |
| SMT/NG/WMS 对账闭环 | L | `smt-ng-wms-reconciliation-spec.md` | NG evidence、WMS 确认/拒绝、目标箱回写失败、版本冲突恢复测试 |

**SPEC 进度同步（2026-07-04 验收）**：

| SPEC / 能力 | 当前状态 | 已验证范围 | 未闭合项 / evidence profile |
| --- | --- | --- | --- |
| Umbrella design | ✅ 设计包已完成 | `phase4-design-with-residuals.md` 已纳入 Phase 1/2/3 residual gates，并声明 Phase 4 设计可以先行 | 不代表 Phase 4 业务能力完成；后续按 production-capable runtime path 与 evidence profile gate 验收 |
| `cell-reservation-spec.md` | ✅ P0 开发/测试闭合 | 复用 `WorklineBinCellReservation`，保留 `PLANNED/CONSUMED/RELEASED/CANCELLED` 内部命名，新增 `RECONCILING`，覆盖 active/frozen 唯一约束、claim/consume/release、TTL 只释放 `PLANNED`、reservation_key/evidence 包含 `correlation_id`、`source_event_id`、provider/source_version | 投放运行路径证据随 sorter inbound evidence profile 验收 |
| `material-location-query-spec.md` | ✅ 开发/测试闭合 | service 层与 API facade 覆盖 material identity、package/bin、rack/side、workline active object、ExternalReference、correlation_id 6 类入口，并按本地事实、ActiveObjectRegistry、CellReservation、WMS snapshot、legacy evidence 聚合 | 生产 WMS snapshot 证据仍按 gated profile 接入 |
| `workline-active-objects-spec.md` | ✅ 本机合同验证已通过 | 聚合 ActiveObjectRegistry、ExecutionWorkItem、ConveyorQueueMembership、RuntimeHold/ReconciliationRecord、MaterialLocationQuery；覆盖 `OK/TRANSIENT/RECONCILING`、hold scope、limit/truncated | 读模型 SLA 与大规模 benchmark 随 evidence profile 验收 |
| `sorter-inbound-capability-spec.md` | ✅ runtime capability 与 evidence profile gate 已落地 | `tests/mock/material_flow` 可表达 sorter inbound provider contract 语义基线；`Phase4SorterInboundPreviewService` 保留纯 preview 边界；`Phase4SorterInboundRuntimeService` 已输出 `RuntimeIntent`、`wms.fulfillment.notify_pkg_binding@v1`、`wms.inventory.confirm_inbound@v1`、CellReservation/RuntimeLocationEvent evidence 与 join gate object-scope reconciliation plan；`site/production` manifest 已要求 provider contract、effect dispatch trace、RuntimeIntentLog/DeviceCommand/WMS fulfillment 证据 | 实际 evidence 文件属于 `reports/`、CI 或部署验收产物，不提交 git；不得复用旧 plugin 入口 |
| `smt-ng-wms-reconciliation-spec.md` | ✅ runtime capability 与 evidence profile gate 已落地 | `tests/mock/material_flow` 可表达 SMT/NG/WMS 对账 provider contract 语义基线；`SmtNgWmsReconciliationPreviewService` 保留纯 preview 边界；`SmtNgWmsReconciliationRuntimeService` 已输出 RuntimeInbox 上游 callback evidence、重复 callback 幂等合并、WMS reject/source_version drift 等 RuntimeHold plan 与 scope-only release plan；`site/production` manifest 已要求 provider contract、RuntimeInbox worker trace、RuntimeHold/ReconciliationRecord trace | 实际 evidence 文件属于 `reports/`、CI 或部署验收产物，不提交 git |
| Runtime evidence readiness gate | ⏹ 已从核心退役 | 该 gate 只验证具体 material-flow/插件行为；二次开发插件改由自身 CI 和 evidence 流程负责 | 核心只保留通用 WES 可靠性与所有权门禁 |
| RCS/AGV/CTU direct provider adapter | ✅ YAGNI 保持未触发 | 当前仍由 WMS 中转统一履约；未写 `fulfillment-provider-adapter-spec.md`，未预留代码骨架 | 仅当客户明确要求绕过 WMS，或 WMS 实测无法满足实时性需求时再启动独立 SPEC |

**按需触发任务**（YAGNI 隔离，不在 Phase 4 时间表内强制执行）：

| Task | 触发条件 | 关联文件 | 备注 |
| --- | --- | --- | --- |
| RCS/AGV/CTU 直连 provider adapter 设计 | 生产前默认不触发；仅当 (1) 客户明确要求绕过 WMS 直连；或 (2) WMS 履约经实测无法满足实时性需求时触发 | `src/app/rcs_integration/` / `src/app/agv_integration/` / `src/app/ctu_integration/`、`fulfillment-provider-adapter-spec.md` | 当前阶段 §2.3 不做 #9 已锁定 RCS/AGV/CTU 调度由 WMS 中转统一执行；该能力是后续扩展，不进入 Phase 4 常规交付，不预先写 SPEC、不预留代码骨架 |

**Phase 4 完成门禁**：

- [x] `MaterialLocationQuery` 6 入口全部支持（service 层与 API facade 均已覆盖）
- [x] `WorklineActiveObjects` 与 `ActiveObjectRegistry` 协同（已通过本机合同测试；SLA/benchmark 随 evidence profile 验收）
- [x] `CellReservation` 目标模型、唯一约束、TTL 释放、投放成功转占用和失败释放/RECONCILING 测试全部通过（开发/测试范围已覆盖；投放运行路径证据随 sorter inbound gate 验收）
- [x] 粗分机正常流通过本机 MOCK 行为契约测试：入料机械臂扫码/测量 -> WMS GRN 绑定与测量校验 -> 入料机械臂投流水线 -> 粗分机流水线到出料口 -> 出料格位分配/预约 -> 必要时 WMS 补空箱货架 -> 出料机械臂投格 -> 本地位置事实与格位占用落库 -> WMS PKG 绑定/库存事务通知；WMS 失败进入同步 hold/reconciliation，不抹掉本地物理事实；入料机械臂在当前对象进入流水线后即可处理下一个对象
- [x] 满箱交换前置分流通过本机 MOCK 行为契约测试：粗分机移出单层货架 -> 满箱交换区或交换决策点 -> 无满箱需求进入分拣机 STATION/排队区；有满箱需求创建 `FULL_BOX_EXCHANGE` -> 按 `rack_code + rack_side` 分批 -> 必要时 `CHANGE_RACK_FACE` 独立履约 -> 满箱物料箱级入库完成/同步，剩余未满箱物料才进入分拣机逐件流程
- [x] 满箱交换区与分拣机 `STATION A/B` 不得混用；满箱交换完成前，分拣机北向机械臂不得对该单层货架取料（本机 MOCK 通过 `station_admission_blocked_until_exchange_completed` 合同表达）
- [x] 分拣机入库正常流通过本机 MOCK 行为契约测试：STATION A/B 与 FIVE STATION admission -> WMS E12 批量投箱履约 + ECS 逐箱物理事件 -> SCAN1 授权料箱 resolve / 未授权 NG -> SCAN2/SCAN3 路由与退料线 -> 北向机械臂取料到扫码平台 -> 扫码后格位分配/预约 -> 必要时换箱/等待 -> 南向机械臂投料 -> 本地位置事实与格位占用落库 -> WMS PKG 绑定/库存事务通知；WMS 失败进入同步 hold/reconciliation，不抹掉本地物理事实
- [x] 已满箱交换入库的物料不得再次进入分拣机逐件分拣候选集；剩余未满箱料箱的物料可继续进入 `STATION A/B` 或排队区（本机 MOCK 已覆盖 full-box object 与 sorting candidate 集合互斥）
- [x] 分拣机物料 work item 与料箱 work item 的 join 条件明确：南向机械臂投料前必须同时满足目标料箱处于滚筒线工作位、目标格位可预约、`CellReservation` 创建成功、相关等待有 deadline 或换箱触发条件（本机 MOCK 已覆盖 allowed/rejected gate）
- [x] CTU 批量履约通过父批次、逐对象 evidence 和批次完成收敛测试；缺子项、乱序、重复或投影冲突必须进入 `RECONCILING`（本机 MOCK 已覆盖父成功但子项未收敛）
- [x] CTU 批次查询视图必须聚合冻结 ACK、status 与 typed terminal `items[]`，展示成员缺失、乱序、部分失败和批次收敛结果；禁止运维界面只显示批次成功
- [x] 北向下一次取料只由上一物料的 `southbound_pick_acknowledged`（南向 `PICK ACK`）解锁；扫码平台占用状态和南向投放 `COMMAND_RESULT` 均不作为取料准入条件，也不存在额外并发旁路
- [x] 本地物理完成与 WMS 同步状态显式拆分：`LOCAL_PHYSICAL_COMPLETED` 不等于业务完全完成；WMS 通知或库存事务失败时进入 `WMS_SYNC_PENDING` 或 `RECONCILING`（本机 MOCK 已覆盖）
- [x] `RuntimeHold` 具备 object/device/resource/queue scope；单对象异常不得默认停整条 WorkLine，人工解除只释放声明的 `allowed_next_effect_scope`（本机 MOCK 已补 `runtime-hold-release-preview` scope-only release 合同）
- [x] 分拣机/粗分机入库能力开发/测试 preview 按目标态 capability 边界沉淀：`Phase4SorterInboundPreviewService` 不访问 DB、不发 WMS/ECS effect、不复用旧 plugin 入口，覆盖粗分机、分拣机 join gate、满箱交换和换面；CTU 只保留批次 status/terminal 合同
- [x] 分拣机/粗分机入库能力 production-capable runtime path builder：`Phase4SorterInboundRuntimeService` 输出 `RuntimeIntent`、effect contract、CellReservation/RuntimeLocationEvent evidence 与 object-scope reconciliation plan，不根据外部 provider 类型分支
- [x] 分拣机/粗分机入库能力 evidence profile：`site/production` manifest 已要求 provider contract 证据、effect dispatch trace、RuntimeIntentLog/DeviceCommand/WMS fulfillment 证据；实际 evidence 文件由 `reports/`、CI 或部署验收产物提供
- [x] SMT/NG/WMS 对账闭环不复制 WMS/NG/PDA 主数据，只保留 evidence、ExternalReference 和 RuntimeHold 解除条件（本机 MOCK 已覆盖冲突矩阵与 scope-only release）
- [x] SMT/NG/WMS 对账开发/测试 preview 按目标态 capability 边界沉淀：`SmtNgWmsReconciliationPreviewService` 不访问 DB、不发 WMS/NG/PDA effect、不复用旧 plugin 入口，覆盖 NG evidence、本地事实缺失、WMS reject、目标箱回写失败、重复/乱序 callback、source_version drift 与 RuntimeHold scope-only release
- [x] SMT/NG/WMS 对账 production-capable runtime path builder：`SmtNgWmsReconciliationRuntimeService` 输出 RuntimeInbox 上游 callback evidence、重复 callback 幂等合并、WMS reject/source_version drift 等 RuntimeHold plan 与 scope-only release plan，不根据外部 provider 类型分支
- [x] SMT/NG/WMS 对账 evidence profile：`site/production` manifest 已要求 provider contract 证据、RuntimeInbox worker trace、RuntimeHold/ReconciliationRecord 证据；实际 evidence 文件由 `reports/`、CI 或部署验收产物提供
- [x] 按需触发任务未达到触发条件时保持不实施，不写预先 SPEC、不预留代码骨架

### 10.6 Phase 5: Legacy 删除与收尾

**目标**：根据 Phase 0 清理矩阵删除旧 WorkLine/plugin/runtime 残留，确保新代码中没有旧插件框架、旧队列 enum、旧 API 兼容转发。

**执行状态（2026-07-07）**：

- `workline-technical` scope 已完成并合并 PR #78（`v0.13.0.0`）。生产 import 已替换到目标态 runtime/capability/normalization/domain 路径，旧 `src.app.workline.plugins.*`、`src.workline_plugin_registry`、`src/workline_plugins/*` 和 `docs/templates/workline_plugin/*` 已退出运行或模板 authoring 路径。
- `src/workline_plugins/*` 与旧模板仅作为历史资料归档在 `docs/archive/legacy-workline-plugins/`，不得回流到 `src/` 可 import 路径。
- `workline-business` business scope 已通过 runtime production closure、runtime evidence 与 business legacy absence gate。携带 regenerated production/runtime artifacts 的目标态验证记录保存在 `docs/architecture/phase3-phase4-production-evidence-bundle.md`。
- business legacy absence gate 已执行并通过 `uv run python scripts/check_business_legacy_absence_gate.py --mode final`，并已随 PR #79（`v0.14.0.0`，merge SHA `8c833610c08005005406b3a774c92519f69b7886`）合并到 develop。业务合同、context、SMT handoff route / reason / usage policy 等目标态实现收口到 `src/app/runtime/capabilities/material_flow/contracts/` 与 material-flow runtime services；旧业务承载入口由 `docs/architecture/business-legacy-absence-ledger.csv` 和 absence guardrail 阻断回流。
- 2026-07-08 restructuring cleanup 已执行 schema/data migration：旧 handling 队列表面删除，WorkLine 运行态物理列迁入 runtime projection；production evidence 与业务审计数据不作为 cleanup 删除对象。

**启动条件（双 scope）**：

- **技术残留清理 scope**：必须先通过 runtime owner guardrail、RuntimeInbox authority tests、mock closure 或等价开发/测试门禁，以及 technical scope 行为契约测试集；当前提交前统一入口为 `./scripts/git-quality-gate.sh --profile quality`。通过后只允许删除无业务语义的旧 plugin 框架、旧队列 enum、旧 API 兼容转发和 dead code。
- **业务承载 legacy scope**：必须先通过 runtime production closure、runtime evidence production profile、material-flow capability / port / contract tests、business legacy absence gate 和 `legacy-cleanup-matrix.md` 中业务承载项关闭状态；未通过前只能冻结入口并保留 characterization tests，不得提前 drop 承载业务语义的数据或代码。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| **ENG-014** Legacy 路径列 `src/{workline_runtime,workline_plugins}` 5 子目录清理矩阵 | S | `docs/architecture/legacy-cleanup-matrix.md`, `docs/architecture/legacy-cleanup-matrix.csv` | ✅ 当前矩阵 636 条，由 `scripts/generate_legacy_matrix.py` 可复现；0 pending-review / 0 空策略；technical cleanup scope 后 `src/workline_plugins` 与旧 template 路径为 0 runtime/template entries |
| 技术残留删除 PR | M | `src/workline_runtime/`, `src/workline_plugins/`, `src/app/workline/`, `src/app/runtime/` | ✅ PR #78 已完成；quality profile 与 absence guardrail 阻断 legacy import 回流；unknown capability 不 fallback 到 `null_plugin`；provider capability 按 `ExternalContractProfile` fail closed |
| 业务承载 legacy 删除 PR | L | `src/app/workline/`, `src/app/runtime/capabilities/material_flow/`, production evidence artifacts | ✅ PR #79 已合并；business readiness gate 与 business legacy absence gate 已通过；104 条 phase4 carrier 在 `business-legacy-absence-ledger.csv` 中关闭或保留为目标态测试证据；WorkLine 运行态物理字段 restructuring cleanup 已完成 |

**Effort 估算**：technical cleanup scope 已完成；business readiness、business legacy absence gate 与 restructuring cleanup 已合并落地；WorkLine restructuring 无剩余数据库清理阻塞项。

### 10.7 总 Effort 估算

| Phase | Effort | Human-team | CC + gstack |
| --- | --- | --- | --- |
| Phase 0 | M | ~1-2 周 | ~2-3 天 |
| Phase 1 | M-L | ~5-7 周 | ~1.5-2.5 周 |
| Phase 2 | XL | ~8-10 周 | ~2-3 周 |
| Phase 3 | L | ~12-14 周 | ~2.5-3.5 周 |
| Phase 4 | L | ~6 周 | ~1 周 |
| Phase 5 | L | ~1-3 周 | ~2-4 天 |
| **总计** | **XL** | **~33-44 周** | **~8-12 周** |

该估算已包含 P0-006/P0-007、CEO-013、ENG-020/021/022。RCS/AGV/CTU 直连 provider adapter 是条件触发扩展，不计入常规交付总量；触发后需单独评估。

### 10.8 实施阶段依赖图

```text
Phase 0 ──────────────────────────────────────┐
  ├── P0-001 目标态契约                      │
  ├── P0-002 Legacy 清理矩阵                  │
  ├── P0-003 行为契约测试                    │
  ├── P0-004 ExecutionCorrelation 矩阵        │
  ├── P0-005 ECS 设备边界合同                │
  ├── P0-006 IntegrationLab/合同基线          │
  └── P0-007 Architecture guardrails          │
                                              │
Phase 1 ──────────────────────────────────────┤
  ├── CEO-001 wms_integration 能力面 ports   │
  ├── CEO-005 scope/authority schema          │
  ├── CEO-006 Authority Matrix                │
  ├── CEO-007 runtime/orchestration 7 core entities │
  ├── CEO-008 动态队列 membership             │
  ├── CEO-010 DeviceCommand ECS contract      │
  ├── CEO-011 manifest version pin            │
  ├── CEO-012 SafetyZone/shared-device         │
  ├── CEO-013 ExternalContractProfile          │
  └── CEO-002 4 方案决策表归档                │
                                              │
Phase 2 ──────────────────────────────────────┤
  └── Runtime 迁移 / WorkLine 清空            │
      (条件性: Phase 0+1 完成 + 重新评审)   │
                                              │
Phase 3 ──────────────────────────────────────┤
  ├── ENG-002 ReconciliationManager           │
  ├── ENG-003 manifest validator             │
  ├── ENG-004 11 态机 + CB                   │
  ├── ENG-006 ActiveObjectRegistry           │
  ├── ENG-008 External callback HMAC         │
  ├── ENG-009 idempotency 复合主键           │
  ├── ENG-010 typed ExternalReference         │
  ├── ENG-011 inbox backpressure + lease      │
  ├── ENG-012 dispatch policy + runtime TTL  │
  ├── ENG-013 RECONCILING 现场隔离            │
  ├── ENG-016 queue writer 并发诊断           │
  ├── ENG-017 满箱交换对账语义                │
  ├── ENG-018 WMS evidence/CB observability   │
  ├── ENG-019 worker/HTTP benchmark           │
  ├── ENG-020 scenario recorder/replay        │
  ├── ENG-021 observability contract          │
  ├── ENG-022 typed toggle governance         │
  └── DESIGN-001..005 plane 修复             │
                                              │
Phase 4 ──────────────────────────────────────┤
  ├── MaterialLocationQuery                  │
  ├── WorklineActiveObjects                  │
  ├── 入库能力目标态重建                     │
  └── SMT/NG/WMS 对账闭环                    │
      (RCS/AGV/CTU direct provider: 条件触发扩展)
                                              │
Phase 5 ──────────────────────────────────────┘
  ├── ENG-014 Legacy 清理矩阵: 已同步 636 条 │
  ├── 技术残留清理 scope: 已合并 PR #78     │
  │   旧 plugin runtime/import 框架已退出 src│
  └── 业务承载 legacy scope + restructuring cleanup: 已通过
      WorkLine 运行态投影迁移和旧队列表面删除已完成
```

---

## 11. 执行规范

### 11.1 TDD 纪律

- **行为契约测试建立（P0-003）**：`uv run pytest tests/workline_runtime tests/resource tests/handling tests/wms_integration` 建立旧能力语义样本；测试名称表达业务能力，不绑定旧 service 内部实现。
- **不保护旧代码形态**：`runtime_query_service.py`、`smt_inbound_handoff_service.py`、`workline_service.py`、`start_admission_service.py` 可删除或重建；只要求目标态业务语义测试通过。
- **新 capability 测试矩阵**：每个新 capability 必须有 unit + integration + regression 三层覆盖；可逆 schema migration 必须有 upgrade + downgrade + 结构断言，数据重塑必须有 dry-run 和行数校验

### 11.2 迁移规范

- **可逆 Alembic upgrade + downgrade 都必须可执行**——任何新表/字段必须包含完整 downgrade；不可逆清理必须依赖快照回滚，不写假 downgrade
- **Schema 兼容性自动校验**：每个 schema migration PR 必须生成并校验目标态 DDL 快照、SQLModel/DTO schema snapshot 和 Alembic upgrade/downgrade 结构差异；破坏性变更允许不兼容旧 schema，但必须证明新 model、repository、service、contract tests 与目标态一致。
- **破坏性迁移默认允许**：表名、字段名、API 路径、enum、包路径都可按目标态重建。
- **不保留旧兼容入口**：例如 `session_id: str` → `workline_session_id: int` 不保留 string 兼容入口；其他类似迁移同样执行。
- **过渡脚本必须短生命周期**：若数据搬迁需要临时脚本，必须在同一 Phase 给出清理 PR。

### 11.3 评审制度

- **autoplan 评审存档**：CEO/Design/Eng 评审全文在 `docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`；28 个 auto-decision 在 `docs/architecture/reviews/decision-audit-trail.md`
- **关键决策进 ADR**：B 方案目标态重写、11 态机、typed envelope、plane RBAC、idempotency、HMAC、ExecutionCorrelation、Authority Matrix 共 8 项已记录到 `docs/architecture/adr/workline-restructuring/`
- **B 方案进入需重新评审**：完成 Phase 0 + Phase 1 后，B 方案启动前必须重新跑 autoplan 或同等深度的 CEO/Eng 评审
- **改动顶层设计需重跑评审**：本设计 §1 §2 §3 §4 §5 §6 §7 §8 §9 任一改动需重新 CEO/Eng 评审（不是 PR-level review）

### 11.4 命名规范

- **目标态命名优先**：旧 `BinTransitMembership` / `BinTransitQueue` 不冻结；目标态使用 `ConveyorQueueMembership` + manifest `queue_code`
- **跨域 correlation key 替代 session FK**：不允许新代码用 `execution_session.id` 作为强 FK 跨域引用；必须用 `ExecutionCorrelation.correlation_id`
- **Runtime vs Execution 前缀（M8 回归）**：包名使用 `runtime/orchestration`；会话聚合根使用 `ExecutionSession`；跨域关联使用 `ExecutionCorrelation`；运行时记录使用 `RuntimeInbox` / `RuntimeTimeline` / `RuntimeHold`；effect ledger 只使用 `RuntimeIntentLog`，出站副作用通过 `EffectPort` dispatcher 派发。
- **禁止 WorklineSession 前缀**：新代码不得新增 `WorklineSession` / `WorkLineRuntimeSession` / `WorklineExecution` 等会把配置域和执行域混在一起的命名。
- **typed Pydantic 模型替代裸字符串/裸 JSON**：`ExternalReference`、`EvidenceEnvelope`、`PlaneSceneView`、`PlaneSnapshot` 等必须用 Pydantic BaseModel，不允许裸 dict
- **WMS DTO 不出 wms_integration 域**：内部域不允许 import WMS 类型；只允许通过
  `WmsMasterDataPort` / operation-specific document QUERY / `InventoryQueryOperationPort` /
  `WmsInventoryTransactionPort` / operation-specific fulfillment contracts / `WmsEventPort` /
  `WmsReconciliationQueryPort` 访问；其中 `WmsEventPort` 属于 `InboundEventPort`，只写 `RuntimeInbox`，
  不进入 `RuntimeIntentLog` / `EffectPort`；`WmsReconciliationQueryPort` 属于 QueryPort，只读拉取
  drift snapshot，不进入 `RuntimeIntentLog` / `EffectPort`
- **设备 callback DTO 不出 device 域**：`DeviceEventPort` 属于 `InboundEventPort`，只负责 ECS/device callback normalizer 与 typed evidence 生成；业务 capability 不允许直接依赖设备 callback DTO、provider exception 或 RuntimeInbox consumer。
- **Runtime capability 注入 contract**：capability/plugin 只接收 `RuntimeCapabilityContext` 中的 query/effect port contract；不允许接收 `wms_integration` / `device` service、HTTP client、service locator、DTO、provider exception、`WmsEventPort`、`DeviceEventPort` 或 `RuntimeInbox` consumer

### 11.5 Legacy 清理规范

#### 实际路径（autoplan ENG-028 修正）

实测旧代码不在 `src/app/`，而在 `src/` 根：

| 旧路径 | 实测 LOC | 清理目标 |
| --- | --- | --- |
| `src/workline_runtime/` | 10,241 | 提取业务语义后删除或重建为 `src/app/runtime/orchestration/` |
| `src/workline_runtime/plugins/` | (待测) | 不作为新架构基础；业务事实提炼为 runtime capability 后删除 |
| `src/workline_runtime/sessions/` | (待测) | 迁入 `ExecutionSession`；correlation key 化 |
| `src/workline_runtime/inbox/` | (待测) | 迁入 `RuntimeInbox`；correlation key 化 |
| `src/workline_plugins/` | 3,085 | 不再追加新插件能力；YAML manifest 转交 `workline` 配置域 |
| `src/app/workline/` | 32,979 | 删除执行能力和旧插件入口；只保留目标态配置 CRUD / manifest / plane scene |

旧 plugin 中直接 import `src.app.wms_integration.*`（例如 WMS 查询 DTO、异常、typed service）的代码是明确清理目标。迁移时先抽取业务决策语义，再改为通过 `RuntimeCapabilityContext.readonly_facts` 或 `RuntimeIntentLog` + EffectPort 使用外部能力。

#### 清理顺序

1. **业务语义提取**：用 characterization tests 固化旧能力中仍需要的业务语义。
2. **目标态骨架落地**：先建立 `runtime/orchestration`、`material`、`ConveyorQueueMembership`、WMS ACL ports。
3. **旧入口分类**：先按清理矩阵标记技术残留、业务承载 legacy、一次性迁移脚本；不做转发兼容。
4. **技术残留删除**：无业务语义的旧入口、旧 enum、旧 plugin 框架和 dead code 进入 technical cleanup scope；前置条件是 runtime owner guardrail、RuntimeInbox authority tests、mock closure 或等价开发/测试门禁通过。
5. **业务承载 legacy 延迟删除**：仍承载 material-flow 业务语义的代码或数据进入 business legacy cleanup scope；只能在 runtime production closure、runtime evidence production profile、business legacy absence gate 与 material-flow 合同测试通过后 drop。
6. **数据迁移与 drop**：迁移必要 evidence 后 drop 旧表/旧 enum/旧字段；不可逆 drop 必须有快照点和清理矩阵勾选。
7. **全局校验**：确认 WorkLine 只保留配置职责，新代码不 import 旧 plugin/runtime 包。

### 11.6 工具与命令规范

- **包管理**：`uv sync --dev`（所有命令走 `uv run ...`，不依赖其它 shell 激活环境）
- **测试**：`uv run pytest` + `uv run pytest --cov=...`
- **Lint/Format**：`uv run ruff format . && uv run ruff check .`
- **migration**：可逆 schema migration 执行 `uv run alembic upgrade head` / `uv run alembic downgrade -1`；data reshape / destructive cleanup 执行 dry-run、快照校验、upgrade，不要求 downgrade
- **architecture guardrails**：`scripts/architecture-guardrails.sh` 必须聚合 §7.5 核心 5 条 + I3 capability 注入/import 边界自动检查，并可被 pre-commit / CI 复用
- **不在 main 上直接开发**：日常单任务开发从 `develop` 切 `feature/*`、`fix/*`、`chore/*` 等分支

### 11.7 IntegrationLab / simulator / replay 规范

- **正式 port contract 优先**：WMS/ECS simulator、sandbox profile、scenario runner 只能通过正式 port contract、callback normalizer 和 RuntimeInbox 进入系统；禁止测试专用 service 直写 runtime 状态。
- **合同 profile 固定**：每次联调 session 必须记录 `provider_code + contract_version + fixture_set`；RUNNING session 不允许热切 profile。
- **回放可断言**：ScenarioReplayRunner 必须校验 active projection diff、RuntimeTimeline 顺序、RuntimeIntentLog 幂等、DeviceCommand 终态和 ReconciliationRecord；禁止只看日志判断通过。
- **RuntimeInbox 人工重放**：人工重放只能面向 `DEAD_LETTER` 或明确标记为可重放的 `FAILED` 记录；必须生成新的 `replay_inbox_id`、记录 `replay_of_inbox_id`、操作者、原因、payload_hash、source_event_id 和 idempotency_key；不得原地修改历史 payload、attempt_count、status 或 source evidence。
- **联调数据可清理**：recording、fixture、sandbox evidence 必须有环境标签、保留期和脱敏规则，不得混入生产 evidence 统计。

### 11.8 Toggle 管理规范

- **只允许 typed toggle**：toggle 只用于 release/ops 调试、provider version、adapter path 或调度策略切换；禁止裸字符串配置改变状态机语义。
- **必须短生命周期**：每个 toggle 必须声明 owner、expiry、scope、default、rollback 和测试矩阵；过期 toggle 在同一 Phase 清理。
- **不能绕过安全门禁**：toggle 不得关闭 HMAC、nonce、idempotency、ECS IDLE 准入、RuntimeHold、evidence 写入或 reconciliation 隔离。

### 11.9 Observability contract 规范

- **稳定命名**：WMS/ECS HTTP、callback normalize、RuntimeInbox claim、RuntimeIntentLog dispatch、DeviceCommand ACK/RESULT、ScenarioReplayRunner 必须有稳定 span name、metric name、log event name。
- **稳定属性**：`trace_id`、`correlation_id`、`provider_code`、`contract_version`、`operation_kind`、`command_code`、`source_event_id`、`workline_code`、`execution_session_id?` 使用统一命名，不随 provider DTO 改动。
- **日志不是合同**：临时 debug log 不能替代 metric/span/evidence；生产问题复盘必须能从 trace、metric、evidence 和 replay scenario 交叉验证。

---

## 12. 风险与对策

### 12.1 4 个 CRITICAL gap（autoplan 识别）

| ID | 风险 | 防线 |
| --- | --- | --- |
| F0 | plan 早期"22.6K 数字"是事实错误 | 决策 #1 措辞修订：760 行 + 5 套新 port；实测 `wms_integration` = 2,649 LOC |
| F5 | RECONCILING 黑洞状态无恢复决议 | Phase 3 ENG-002（ReconciliationManager + owner-scoped resolution decision + 5/30 分钟升级） |
| F8 | 32,979 LOC 改造无行为契约测试 | Phase 0 P0-003；关键业务语义 characterization + contract tests |
| F10 | `GET /worklines/{id}/plane` 全员可读全量运营数据 | Phase 3 plane read model；RBAC + 行级 + 脱敏 + 审计 |

### 12.2 队列模型决策

旧 `BinTransitMembership` + 8 个 `BinTransitQueue` enum 不进入目标态。目标态采用 `ConveyorQueueMembership`：

1. 队列编码来自 WorkLine manifest，不是系统级 enum。
2. `queue_role` 是 manifest role 快照，用于展示和审计，不作为写死的业务流程。
3. active 唯一约束按 WorkLine + bin/placeholder 维度表达业务语义。
4. 旧表可通过 migration 搬迁必要 evidence 后删除。

### 12.3 事实修正

- **F0 修正**：`wms_integration` 实测 2,649 LOC（不是 22.6K），其中 typed_ports.py 609 行 + models/ports.py 151 行 = 760 行 typed port

### 12.4 3 个 cross-phase themes（autoplan 双 voice 独立命中）

1. **外部 ACL 应补全并清理**（CEO F1 + Eng F14）→ Phase 1 CEO-001 + ADR 0001
2. **RuntimeIntentLog / ExecutionSession / ExecutionCorrelation 显式拆分**（CEO F7 + Eng F3/F4）→ Phase 1 CEO-007 + ADR 0007
3. **过早在命名/schema 画死**（CEO F4 + Eng F5/F12）→ 队列改为 manifest 动态配置 + typed envelope（Phase 3 ENG-010）

### 12.5 现状 → 目标态对比

| 维度 | 现状 | 目标 |
| --- | --- | --- |
| 域结构 | workline 混合"配置 + 执行 + 插件"（32,979 LOC），runtime 在 `src/workline_runtime/`（10,241 LOC）独立，plugin 在 `src/workline_plugins/`（3,085 LOC）独立 | workline 仅配置；runtime/orchestration 独立域；plugin 能力在 runtime 域以 port/capability 形式重建 |
| 域间引用 | 16+ 文件含 `session_id` / `execution_session_id` 跨域 FK | `ExecutionCorrelation.correlation_id` 作为跨域 correlation key |
| WMS 集成 | 仅有旧 `WmsInventoryPort` 能力且 query/mutation 混杂 | WMS 能力面 ports 全部实现，库存拆为 Query / Transaction |
| RuntimeIntentLog / ExecutionSession / ExecutionCorrelation | 同段自相矛盾 | 显式拆分 |
| conveyor queue | `BinTransitMembership` + 8 个系统级 enum | `ConveyorQueueMembership` + manifest 动态队列 |
| RECONCILING | 黑洞状态无恢复决议 | `ReconciliationManager` + owner-scoped resolution decision |
| Plane 接口 | 全员可读全量运营数据 | 拆 scene + snapshot 独立接口 + RBAC + 行级 + 脱敏 + 审计 |
| idempotency_key | 4 处分散实现，无统一命名空间 | 复合主键 + immutable `request_hash` |
| External callback 鉴权 | `signature + timestamp` 或可选 token，未覆盖全部 provider | HMAC-SHA256 body 签名 + 5 分钟 nonce TTL + provider/source/callback_type allow-list |
| 测试基线 | 32,979 LOC 改造无行为契约基线 | 关键业务语义 characterization + 新 contract tests |
| Legacy 路径 | 实际在 `src/{workline_runtime,workline_plugins}` | 业务语义提取后删除或重建 |

---

## 13. 附录

### 13.1 实施细节 SPEC 触发清单

实施细节（字段定义、状态机转移表、HMAC 合同、typed envelope schema、PlaneSceneView/Snapshot schema 等）**不在本文展开为独立 SPEC**。当对应 Phase 启动前或启动时，按需生成独立 SPEC：

- **Phase 0 启动时** → 写 `external-contract-profile-spec.md`（provider_code、contract_version、runtime_capabilities、inbound_normalizers、field mapping、timeout/retry、fixture set、unsupported actions）、`integration-lab-and-simulator-spec.md`（WMS/ECS simulator、sandbox provider profile、scenario runner、contract fixture 与环境隔离）、`architecture-guardrails-spec.md`（§7.5 核心 5 条不变量 + I3 capability 注入/import 边界的脚本入口、测试目录、失败示例、CI/pre-commit 接入方式）
- **Phase 1 单 PR 的 Packet B / CEO-001 代码实现前** → 写 `wms-integration-ports-spec.md`（MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / ReconciliationQuery 各 port 详细字段，并引用 `docs/integration/wms_rcs_interface_requirements.md` 的 P0/P1 接口清单）
- **Phase 1 单 PR 的 Packet C / AP3 前** → ✅ 已写 `runtime-orchestration-spec.md`（ExecutionSession / ExecutionCorrelation / ExecutionWorkItem / RuntimeInbox / RuntimeTimeline / RuntimeHold / RuntimeIntentLog 7 个 runtime core 实体最小骨架；统一 §4.1 与 §9.2 的字段、索引、lease、deadline、idempotency 和对象级 work item 与 session 的并发边界）
- **Phase 2 启动时** → 写 `legacy-runtime-migration-spec.md`（旧 WorkLine/plugin/runtime 执行能力迁移、删除和 WorkLine 清空顺序）
- **Phase 3 启动时** → 写 `fulfillment-state-machine-spec.md`（11 态机完整转移图 + 4 timeout 时长表 + BLOCKED_BY_CB 出站阻塞 + CB 恢复期 late callback 入站 evidence 合同）、`reconciliation-manager-spec.md`（触发矩阵 + 隔离动作 + owner-scoped resolution decision + 5/30 分钟升级）、`plane-read-model-spec.md`（PlaneSceneView/Snapshot 字段 + 容量上限 + RBAC 矩阵）、`external-callback-auth-spec.md`（HMAC canonical + nonce TTL + allow-list）、`device-dispatch-policy-spec.md`（能力选择 + deadline + 状态快照 TTL）、`scenario-replay-spec.md`（录制、脱敏、deterministic replay、断言矩阵）、`observability-contract.md`（span/metric/log 命名与稳定 attributes）、`runtime-toggle-governance.md`（typed toggle 分类、owner/expiry/scope/default/rollback/test matrix）
- **Phase 4 启动时** → ✅ 已写 `cell-reservation-spec.md`、`material-location-query-spec.md`、`workline-active-objects-spec.md`、`sorter-inbound-capability-spec.md`（展开粗分机正常流、满箱交换前置分流、分拣机正常流、满箱交换区/分拣机 STATION 边界、`rack_code + rack_side` 批次分组、`CHANGE_RACK_FACE` 独立履约、已交换物料排除逐件分拣、`CellReservation`、授权料箱 resolve、南向 `PICK ACK` 串行门禁及 manifest validator、物料 work item 与料箱 work item join 条件、本地物理事实先落与 WMS 同步/对账状态、CTU 批次 status/terminal 成员 evidence 查询视图）、`smt-ng-wms-reconciliation-spec.md`；同时写入 `docs/superpowers/specs/2026-07-03-phase4-design-with-residuals.md` 记录 Phase 1/2/3 residual gates。各 SPEC 当前实施、MOCK 验收与生产门禁状态以 §10.5 的 SPEC 进度同步表为准。`fulfillment-provider-adapter-spec.md` 仅在 §10.5 RCS/AGV/CTU 直连触发条件满足时生成，生产前默认不写
- **Phase 5 启动时** → ✅ 已写 `legacy-cleanup-execution-plan.md`；PR #78 已执行 technical cleanup scope，逐文件列出 delete / rebuild / move / keep-contract、是否承载 material-flow 业务语义、对应 capability/port/contract tests、允许 drop 的前置条件和回滚边界。runtime production closure + material-flow production evidence ledger 已补齐，business legacy cleanup scope readiness 已通过；business legacy absence gate 已由 PR #79 和 `business-legacy-absence-ledger.csv` 关闭；2026-07-08 restructuring cleanup 已关闭旧 handling 队列和 WorkLine 运行态 schema/data 残留。

**为何不在本文展开**：

- 字段定义、状态机表等在 Phase 启动时容易发现新约束（实测发现数 = 实际编码冲突）
- 预先 SPEC 容易过早画死（autoplan 决策 #4 教训）
- Phase 内 review 更轻，spec 与实现同步

### 13.2 关键决策（ADR 索引）

| # | 决策 | ADR |
| --- | --- | --- |
| 1 | B 方案目标态重写 + 不做向后兼容 | [`0001-b方案选择与capability-freeze.md`](adr/workline-restructuring/0001-b方案选择与capability-freeze.md) |
| 2 | 外部履约 11 态机 + 4 timeout + BLOCKED_BY_CB | [`0002-外部履约-11态机加timeout.md`](adr/workline-restructuring/0002-外部履约-11态机加timeout.md) |
| 3 | typed `ExternalReference` + `EvidenceEnvelope` | [`0003-typed-external-reference-evidence.md`](adr/workline-restructuring/0003-typed-external-reference-evidence.md) |
| 4 | plane 接口 RBAC + 容量上限 + 极态 | [`0004-plane-rbac-bounded-snapshot.md`](adr/workline-restructuring/0004-plane-rbac-bounded-snapshot.md) |
| 5 | idempotency_key 复合主键 + request_hash | [`0005-idempotency-composite-key.md`](adr/workline-restructuring/0005-idempotency-composite-key.md) |
| 6 | External callback body HMAC + nonce TTL | [`0006-wms-callback-hmac.md`](adr/workline-restructuring/0006-wms-callback-hmac.md) |
| 7 | ExecutionCorrelation correlation key | [`0007-execution-correlation-key.md`](adr/workline-restructuring/0007-execution-correlation-key.md) |
| 8 | Authority Matrix | [`0008-authority-matrix.md`](adr/workline-restructuring/0008-authority-matrix.md) |

**ADR 编号约定**：ADR 是"已做出的决策记录"，不预先占位编号。Phase 1 CEO-013 与 Phase 3 ENG-020 / ENG-021 / ENG-022 任务完成时会产生对应 ADR，ID 在写入时按下一个可用编号分配，不在本表预留行。

### 13.3 现有相关文档

- [`docs/superpowers/archive/specs/2026-06-19-workline-multi-object-state-machine-design.md`](../superpowers/archive/specs/2026-06-19-workline-multi-object-state-machine-design.md) — 历史状态机子设计
- [`docs/superpowers/archive/specs/2026-06-23-workline-c0-resource-projection-foundation.md`](../superpowers/archive/specs/2026-06-23-workline-c0-resource-projection-foundation.md) — 历史 C0 子基础
- [`docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`](adr/2026-05-13-wes-wms-rcs-resource-boundary.md) — 现有 ADR
- [`docs/architecture/adr/2026-05-26-wms-integration-domain.md`](adr/2026-05-26-wms-integration-domain.md) — 现有 ADR
- [`docs/architecture/ARCHITECTURE_EVOLUTION_ROADMAP.md`](ARCHITECTURE_EVOLUTION_ROADMAP.md) — 季度级演进路线图
- [`docs/architecture/REPOSITORY_GUIDE.md`](REPOSITORY_GUIDE.md) — 通用 Repository 使用指南
- [`docs/architecture/SRS.md`](SRS.md) — 软件需求规格说明书

### 13.4 外部参考来源

| 来源 | 用于本设计的约束 |
| --- | --- |
| AutoStore, [Warehouse Execution System: Enhancing Efficiency](https://www.autostoresystem.com/insights/warehouse-execution-system-enhancing-efficiency) | WES 位于 WMS 与现场自动化之间，承担执行编排、自动化衔接和效率优化；支撑本设计中 WMS 权威 + WES 编排 + ECS 执行边界 |
| Blue Yonder, [Warehouse Execution](https://blueyonder.com/solutions/warehouse-management/warehouse-execution) | WES 需要做实时执行、资源协调和自动化设备协同；支撑 DeviceDispatchPolicy、Runtime/Orchestration 和 plane snapshot 的目标 |
| Warehouse Automation, [Warehouse Execution Software Implementation](https://www.warehouseautomation.org/2024/10/01/warehouse-execution-software-implementation/) | WES 实施应在上线前使用 emulator 做高性能测试和缺陷暴露；支撑 IntegrationLab、WMS/ECS simulator、scenario runner 与现场联调前门禁 |
| Conveyco, [Horizon WES Incorporates Emulators to Reduce Implementation Time & Cost](https://www.conveyco.com/news/horizon-warehouse-execution-software-wes-incorporates-emulators-to-reduce-implementation-time-cost/) | WES emulator 可在安装前测试系统并支持系统修改、容量增长、SKU 增长等场景；支撑硬件/WMS 未稳定时的 sandbox 联调与 replay fixture |
| Martin Fowler, [Feature Toggles](https://martinfowler.com/articles/feature-toggles.html) | Feature toggle 可支持持续交付和运行时切换，但必须分类和治理；支撑 typed release/ops toggle、owner/expiry/scope/default/rollback/test matrix |
| OpenTelemetry, [Trace Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/general/trace/) | OpenTelemetry 为 span 和跨技术操作定义通用语义属性；支撑 WMS/ECS/provider 调用、callback、runtime、device command 和 replay 的 observability contract |
| GS1, [EPCIS and CBV Implementation Guideline](https://www.gs1.org/standards/epcis-and-cbv-implementation-guideline/current-standard) | 可观察对象事件应表达 what/where/when/why/source；支撑 `RuntimeLocationEvent` 与位置事实投影契约 |
| Webhooks.fyi, [Replay Prevention](https://webhooks.fyi/security/replay-prevention) | 外部 callback 应使用 timestamp/nonce/body hash 防重放；支撑 External callback HMAC、nonce TTL 和 payload hash |
| Software Engineering Institute, [Architecture Tradeoff Analysis Method (ATAM)](https://www.sei.cmu.edu/our-work/projects/display.cfm?customel_datapageid_4050=21328) | 架构评审应基于场景驱动覆盖 maintainability/performance/scalability/security 多维度质量属性；支撑 §8 非功能性设计、§7.5 不变量分级和 §10.4 Phase 3 完成门禁的具体场景 |
| Martin Fowler, [Strangler Fig Application](https://martinfowler.com/bliki/StranglerFigApplication.html) | 通过逐步替换遗留能力降低一次性重写风险；支撑 §3.8 目标态契约、§10.3.1 B 方案回退路径与 §11.5 Legacy 清理规范的分阶段破坏性切换 |
| Microsoft Architecture Center, [Anti-Corruption Layer pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/anti-corruption-layer) | ACL 通过 facade/adapter 隔离不同子系统模型，防止外部模型污染内部领域；支撑 §3.4 Authority Matrix、§3.5 capability 注入边界与 §7.5 stable guardrails |
| Refactoring.Guru, [Refactoring Techniques](https://refactoring.guru/refactoring/techniques) | 大型重构必须分阶段、可逆、用测试保护行为；支撑 §10 五阶段 critical path、§10.3.1 B 方案回退路径、§11.1 行为契约测试 |
| Spotify Engineering, [How We Improved Developer Productivity](https://engineering.atspotify.com/2023/03/how-we-improved-developer-productivity-for-our-deployment-engineers) | 架构评审应聚焦避免过早抽象、对齐团队容量、降低实施摩擦；支撑 §3.1 KISS/YAGNI 应用、§10.7 Effort 估算与 §3.5.1 ExternalContractProfile 的"按需触发"原则 |
| OpenTelemetry, [Sampling](https://opentelemetry.io/docs/concepts/sampling/) | trace sampling 策略需在数据量、可观测性与成本间平衡；支撑 §8.4 现场运行默认 100%、压测/replay 可独立采样、异常 trace 必留的 Phase 3 决策点 |

### 13.5 评审存档

- [`docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`](reviews/autoplan-workline-restructuring-2026-06-23.md) — autoplan CEO/Design/Eng 评审全文
- [`docs/architecture/reviews/decision-audit-trail.md`](reviews/decision-audit-trail.md) — 28 个 auto-decision 记录
- [`docs/architecture/reviews/workline-restructuring-v4-review-2026-06-23.md`](reviews/workline-restructuring-v4-review-2026-06-23.md) — 外部 v4 评审报告；C1-C3 / M1-M10 已回归到本文主体章节

### 13.6 外部 v4 评审回归矩阵

| ID | 回归结果 | 本文落点 |
| --- | --- | --- |
| C1 | 选择破坏性方案 B：`ConveyorQueueMembership` + `conveyor_queue_memberships`，旧 enum/table 删除 | §3.8 |
| C2 | 补 B 方案暂停/回退条件，不恢复旧兼容，只缩小重构范围 | §10.3.1 |
| C3 | 补 WES 内部 idempotency key 命名空间与 operation_kind 细分 | §5.4 |
| M1 | plane 实时性分级：scene 1 Hz，snapshot SSE + 250ms fallback，events SSE | §5.2 |
| M2 | `BLOCKED_BY_CB` 定义为系统侧延迟状态，不计入履约 P95 | §6.3 |
| M3 | 明确 `DRAINING`/`HOLD`/`VALIDATING` 分属 WorkLine/session/activation | §9.1 |
| M4 | 补传感器抖动、通信丢包、重复上报三类物理异常 | §6.4 |
| M5 | 冲突仲裁扩展到 rack/pkg/command 维度 | §6.6 |
| M6 | `DeviceState` 增加 `UNKNOWN` / `MAINTENANCE` | §9.6 |
| M7 | 外部系统 ACL 镜像命名，禁止 `src/app/external/` 父目录 | §3.5 |
| M8 | Runtime/Execution 命名前缀规则 | §11.4 |
| M9 | `work_position_code` 是 WES 内部工作位，与 WMS `location_code` 映射 | §9.4 |
| M10 | drift SLA 与 WMS 可用性分离，WMS 不可用时抑制告警风暴 | §6.5 |

### 13.7 外部 v4 待澄清项决策

| ID | 决策 | 本文落点 |
| --- | --- | --- |
| Q1 | 旧 capability 只保留行为不变量，不保留代码形态 | §2.2 / §11.1 |
| Q2 | P0 plane 面向操作员终端、工程调试台和只读大屏，不面向公开报表或 WMS 全局查询 | §5.2 |
| Q3 | RECONCILING 告警分为 info/warn/critical；外部通知 provider 后续扩展 | §6.4 |
| Q4 | B 方案失败时按 §10.3.1 暂停或降级到 C/D，不恢复旧兼容 | §10.3.1 |
| Q5 | required 设备 OFFLINE/UNKNOWN/MAINTENANCE 阻塞 WorkLine 启动；optional 设备只降级能力 | §9.6 |
| Q6 | 物理现场 RECONCILING 采用 ECS/device push + WES pull 双通道 | §6.4 |
| Q7 | RCS/AGV/CTU 直连仅作条件触发扩展；触发后在 provider adapter 层差异化，不扩散到 runtime/handling | §3.5 / §10.5 |
| Q8 | WES 内部 idempotency key 使用 `WES-{OPERATION_KIND}-{HASH}` 命名空间 | §5.4 |
| Q9 | Event_Push 响应体固定 ACK，响应拦截器拒绝 command-like 字段 | §7.1 / §7.5 |
| Q10 | Legacy 清理矩阵移入 Phase 5，Phase 4 聚焦后续子领域能力 | §10.5 / §10.6 |

### 13.8 状态所有权图（详细 ASCII）

```text
WorkLine 配置
  + ConveyorLine / PipelineQueue / EntryPoint / ExitPoint / Device(role)
  |
  | manifest/config pin
  v
inbound callback lane
  WMS ordinary event / EFFECT status hint / ECS-device callback / AGV result
        |
        v
  InboundEventPort (WmsEventPort / DeviceEventPort normalizer)
        |
        v
  RuntimeInbox

runtime/orchestration
  +-- ExecutionSession (session aggregate / PK owner)
  |     +-- RuntimeInbox        <- ordinary event / status hint / device callback evidence
  |     +-- RuntimeTimeline
  |     +-- RuntimeHold
  |     +-- ExecutionCorrelation
  |     +-- ConveyorQueueMembership
  |
  +-- RuntimeIntentLog (effect proposal / outbox log, NOT state)
        |
        v
      EffectPort
        +--> handling owner service
        |      +-- HandlingOperation / HandlingMove (correlation_id, 无 FK)
        |
        +--> resource projection writer
        |      +-- RackPlacement / RackBinMount / BinMaterialMount
        |      +-- ResourceStateEvent (ExternalReference + EvidenceEnvelope)
        |
        +--> wms_integration (ACL)
        |      +-- operation-specific fulfillment contracts
|      +-- operation-specific ACK/status/terminal result
        |
        +--> device / DeviceCommandPort
               +-- DeviceCommand -> ECS/device upper system

material (WES 根实体)
  +-- material_units.current_session_correlation_id (correlation key)

reconciliation (RECONCILING 冲突决议模型)
  +-- ReconciliationManager
        +-- 触发矩阵: 投影冲突 / External callback 不一致 / 设备状态矛盾 / drift
        +-- 强制动作: evidence + RuntimeHold + 通知
        +-- 决议输出: resolution_decision + owner_scope + allowed_next_effect_scope
        +-- owner 自行转移: fulfillment / handling / session / projection / device
```
