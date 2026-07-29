---
status: Phase 0 联调实验室
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/archive/specs/2026-06-25-workline-restructuring-phase-0-spec.md
related: docs/contracts/external-contract-profile.md
note: |
  本文件定义 IntegrationLab 的 simulator、sandbox、fixture、scenario runner 基线。
  Phase 0 只定义能力合同和 fixture 骨架；simulator 实现留 Phase 1 CEO-013。
  simulator/sandbox 不得进入生产 fallback，不绕过正式 port contract。
---

# IntegrationLab 与 Simulator 基线（P0-006）

> 父设计：主计划 §3.5.1 外部合同支撑（IntegrationLab / ScenarioRecorder / ScenarioReplayRunner）
> 外部合同：`docs/contracts/external-contract-profile.md`

## 1. 编写目的

在硬件未到位、WMS 未稳定、现场联调前，提供 WMS/ECS simulator、sandbox provider profile、contract fixture 和 scenario runner，验证 Runtime capability、callback normalizer 和 effect dispatch 在合同约束下可工作。

## 2. 能力清单

| 能力 | 要求 |
| --- | --- |
| WMS simulator | P0/P1 WMS query/effect/callback fixture |
| ECS simulator | status、Receive Command、result/event callback fixture |
| sandbox provider profile | 仅用于联调和测试，不进入生产 fallback |
| scenario runner | 支持正常流、拒绝、超时、重复事件、缺 event_id |
| 环境隔离 | simulator/sandbox 不允许被 production profile 引用 |

## 3. WMS simulator

| 接口 | simulator 行为 |
| --- | --- |
| `WmsMasterDataPort.get_material` / `list_materials` | 返回 fixture 物料主数据 |
| `wms.document.get_grn@v1` / `wms.document.list_grn_packages@v1` | 返回 typed fixture GRN 与料盘归属 |
| `wms.inventory.query_inventory@v1` | 返回 typed fixture 库存快照（带 `source_version`） |
| `wms.fulfillment.request_load_unit_transport@v1` | 接受请求，按 scenario 配置延迟形成 status 终态 |
| `WmsInventoryTransactionPort.reserve_inventory` | 返回预留结果 |
| `WmsReconciliationQueryPort.check_bin_drift` | 返回 drift snapshot |
| `WmsEventPort`（callback） | 主动推送四类普通事件或 `WMS_EFFECT_STATUS_HINT` 到 external callback API |

**约束**：
- simulator 只能通过正式 port contract 进入系统；不得引入测试专用 domain service
- simulator 响应必须带 `scope` / `authority=WMS` / `source` / `evidence_at` / `source_version`
- simulator 不复制为 WMS 主数据；只返回 fixture evidence

## 4. ECS simulator

| 接口 | simulator 行为 |
| --- | --- |
| `GET /api/v1/device/status` | 返回设备状态（IDLE/RUNNING/ERROR/OFFLINE/UNKNOWN/MAINTENANCE） |
| `POST /api/v1/device/command`（Receive Command） | 接受命令，返回 `200 Accepted` |
| `/api/v1/callback/result` | 按场景延迟回传 `command_code` 结果 |
| `/api/v1/callback/event` | 推送 `DEVICE_RESULT` / `DEVICE_EVENT` / `SCAN_COMPLETED` 等 |

**约束**：
- ECS simulator 模拟 Command-Ack-Callback 异步闭环（白皮书 §1.3.3）
- 缺 `event_id` 的 event fixture 用于验证"ACK 但不推进归属"（BC-08）
- 不模拟 PLC/坐标/关节控制（WES 不与 PLC 通讯）

## 5. sandbox provider profile

| 项 | 规则 |
| --- | --- |
| `environment` | 只能是 `sandbox` |
| 用途 | 联调、contract test、scenario replay |
| 禁止 | 被 `staging` / `production` profile 引用；作为生产 fallback |

**sandbox profile 注册**：sandbox provider profile 通过 `ExternalContractProfile`（`environment=sandbox`）注册到 provider registry；Runtime capability admission 和 callback normalizer 按 sandbox profile 合同工作，与生产 profile 路径一致。

## 6. scenario runner

| 场景 | 覆盖目标 |
| --- | --- |
| 正常流 | happy path query/effect/callback 闭环 |
| 拒绝 | provider 返回 `REJECTED`（无可用货架、无空箱位、目标不合法） |
| 超时 | query/effect 超时（验证 timeout_retry 退避） |
| 重复事件 | 同 `source_event_id + payload_hash` 重复 callback（验证幂等合并） |
| 缺 `event_id` | ECS event 缺 `event_id`（验证 ACK 但不推进归属，BC-08） |
| 乱序事件 | 迟到 callback 必须进入 RECONCILING，不覆盖已推进状态 |
| 断网 | ECS/WMS 链路不可用必须进入 RECONCILING，不静默成功 |

**scenario runner 约束**：
- scenario 必须基于 fixture，不依赖生产数据
- scenario replay 必须验证 active projection diff、RuntimeTimeline 顺序、outbox/effect 幂等和 ReconciliationRecord 结果（主计划 §3.5.1 联调不变量）
- 场景回放必须 deterministic（同输入同输出）
- Phase 3 runner 使用 `IntegrationLabScenarioRunner`：先通过 `ExternalContractProfile` / `ProviderSimulatorRegistry` 校验 WMS/ECS sandbox profile 与 fixture case，再交给 `ScenarioRecorder` / `ScenarioReplayRunner` 断言完整链路 replay。

## 7. ScenarioRecorder / ScenarioReplayRunner（来源主计划 §3.5.1）

| 能力 | 用途 |
| --- | --- |
| `ScenarioRecorder` | 从 RuntimeInbox、RuntimeIntentLog、DeviceCommand、WMS fulfillment、projection evidence 脱敏录制联调场景 |
| `ScenarioReplayRunner` | 支持 deterministic replay，断言 projection diff / timeline 顺序 / outbox 幂等 / ReconciliationRecord 结果 |

**禁止**：用人工数据库改数替代事件回放。

## 8. 联调不变量（来源主计划 §3.5.1）

- simulator 与 sandbox provider 只能通过正式 port contract 进入系统；不得引入测试专用 domain service
- contract version 必须写入 evidence、trace attributes 和 callback envelope；同一 execution session 固定 provider profile，不热切
- 场景回放必须验证 active projection diff、RuntimeTimeline 顺序、outbox/effect 幂等和 ReconciliationRecord 结果
- toggle 默认关闭，必须有 owner、expiry、影响范围、回滚方式和测试矩阵；过期 toggle 必须在同一 Phase 清理

## 9. 环境隔离

| 约束 | 规则 |
| --- | --- |
| simulator 不得进入生产 fallback | production profile 不得引用 sandbox/simulator provider |
| sandbox 不绕过正式 port contract | simulator 走 port contract，不直接注入 domain service |
| fixture 脱敏 | ScenarioRecorder 录制时脱敏（`pkg_code` 后 4 位掩码、`bin_code` 前缀掩码） |
| 环境标记 | fixture 必须声明 `environment=sandbox`，Pydantic 校验拒绝生产环境引用 sandbox fixture |

## 10. 验收（SPEC P0-006）

1. ✅ simulator 和 sandbox 只能走正式 port contract（§3/§4/§5）
2. ✅ 不允许业务代码直接依赖 simulator 实现（simulator 只通过 port contract 注册）
3. ✅ fixture 可被 adapter contract tests 复用（§6 scenario + `tests/fixtures/external_contracts/wms/default`）
4. ✅ Phase 3 `IntegrationLabScenarioRunner` 可跑通 WMS/ECS fixture-level 完整链路，覆盖正常、乱序、重复、超时、拒绝、断网场景，并断言 active projection diff、timeline、outbox/effect 幂等和 reconciliation 结果。

## 11. 后续 Phase

| Phase | 任务 | 本基线锁定项 |
| --- | --- | --- |
| Phase 1 CEO-013 | ExternalContractProfile + provider simulator registry | WMS/ECS simulator、sandbox profile、contract fixture、scenario runner 可运行 |
| Phase 3 | `scenario-replay-spec.md` | ScenarioRecorder / ScenarioReplayRunner / IntegrationLabScenarioRunner 录制、脱敏、deterministic replay、断言矩阵 |
