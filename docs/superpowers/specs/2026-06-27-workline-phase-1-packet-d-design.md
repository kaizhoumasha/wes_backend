# Phase 1 Packet D — Capability Boundary 设计 SPEC

> **状态**：✅ 已批准（用户 2026-06-27 通过 ExitPlanMode 批准 plan）
> **作者**：Claude（brainstorming skill 驱动）
> **依据**：`docs/architecture/workline-and-plugin-restructuring.md` 主计划 + `docs/superpowers/specs/2026-06-26-workline-restructuring-phase-1-spec.md` Phase 1 SPEC

---

## 1. 背景与目标

### 1.1 当前状态

Phase 1 目标态骨架已落 Packet A/B/C（PR #64 merged to develop）。剩 Packet D 唯一缺口：

- **CEO-009** Capability 注入/import 边界静态扫描器（主计划 §3.5 + §9.2）
- **4 remaining WMS ports**：`WmsDocumentPort` / `WmsFulfillmentPort` / `WmsEventPort` / `WmsReconciliationQueryPort`（主计划 §5.1 + Phase 1 SPEC §139-140）
- **InboundNormalizer 静态校验**（主计划 §3.5.1 + H2 黑名单扩展）

### 1.2 目标交付

单 PR 合并后，Phase 1 全部 14 门禁（含 Packet D 5 项）达到"绿灯"状态：

1. ✅ Packet D-1: 4 remaining ports 全部落地（Phase 1 CEO-001 完成 7/7）
2. ✅ Packet D-2: CEO-009 capability 静态扫描器（R-I3a/R-I3b/R-I3c 三层覆盖）
3. ✅ Packet D-3: InboundNormalizer 静态校验（Pydantic + Registry + RuntimeCapabilityContext 路由）
4. ✅ H2 type guard 拒绝业务 capability 持有 `WmsEventPort` / `DeviceEventPort` / `RuntimeInbox consumer`
5. ✅ H3 import-linter capability-isolation contract 接入 git-quality-gate

### 1.3 关键决策（用户已澄清）

| # | 决策点 | 选择 |
|---|------|------|
| 1 | PR 拆分 | 单 PR 一次全部落地 |
| 2 | Port 方法集 | 完整覆盖 20 个方法 + 完整 docstring |
| 3 | Scanner 实现 | 双层叠加（architecture-guardrails.sh + import-linter + 测试） |
| 4 | InboundNormalizer 校验 | 三层（Pydantic + Registry + RuntimeCapabilityContext 路由） |

---

## 2. 架构决策

### 2.1 文件布局（沿用 Packet B 约定）

新增 4 ports 文件，与现有 `master_data.py` / `inventory_query.py` / `inventory_transaction.py` 同包同模式：

```
src/app/wms_integration/ports/
├── master_data.py             # 已有 (#1)
├── inventory_query.py         # 已有 (#3)
├── inventory_transaction.py   # 已有 (#4)
├── document.py                # 新增 (#2, WmsDocumentPort + 5 数据类)
├── fulfillment.py             # 新增 (#5, WmsFulfillmentPort + 2 数据类)
├── event.py                   # 新增 (#6, InboundEventPort 基协议 + WmsEventPort + 4 normalizer 数据类)
└── reconciliation_query.py    # 新增 (#7, WmsReconciliationQueryPort + 1 数据类)
```

**Event port 双协议模式**：
- `WmsEventPort(Protocol)`：入站回调 normalizer 抽象（4 normalizer）
- `InboundEventPort(Protocol)`：共享基协议（**不导出**到业务 capability）
- `WmsEventPort` 走 `InboundNormalizerRegistry` 路径，业务 capability 不可注入（被 H2 黑名单拒绝）

### 2.2 Port 方法集（20 方法）

| Port | 方法数 | 方法清单 |
|------|-------|---------|
| WmsDocumentPort | 6 | `get_grn` / `list_grn_items` / `get_pick_order` / `get_outbound_order` / `get_wave` / `get_task_snapshot` |
| WmsFulfillmentPort | 7 | `request_rack_supply` / `request_rack_transport` / `change_rack_face` / `full_box_exchange` / `move_bin_to_conveyor_entry` / `move_bin_to_conveyor_exit` / `notify_pkg_binding` |
| WmsEventPort | 4 normalizer | `WMS_GRN_RECEIVED` / `WMS_PALLET_ARRIVED` / `WMS_RACK_ARRIVED` / `WMS_TRANSPORT_COMPLETED` |
| WmsReconciliationQueryPort | 3 | `check_bin_drift` / `check_rack_drift` / `check_full_drift`（全部只读） |

### 2.3 静态扫描器双层叠加（CEO-009）

**Layer 1：扩展 `scripts/architecture-guardrails.sh`** —— 新增 `rule_ri3c()`，拒绝业务 capability 持有 `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` / `RuntimeInbox` / `RuntimeInboxConsumer` 的 import 或 type hint。

**Layer 2：启用 import-linter** —— 新增 `.import-linter.ini`，`capability-isolation` contract 按 Phase 1 SPEC §279 列出全部 forbidden_modules（wms_integration.* services/models/clients/providers/schemas/dto/dtos/exceptions + 7 个 port 子包中的 inbound_event/callback/result/event + device.* 全部 + callback.services + runtime.orchestration.consumers）。

**Layer 3：增量测试** —— 新增 `tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py` 覆盖上述全部禁止项。

集成入口：`scripts/git-quality-gate.sh` 在 ruff 之后、pytest 之前增加 import-linter 检查步骤。

### 2.4 InboundNormalizer 静态校验三层

| Layer | 实现位置 | 职责 |
|------|---------|------|
| 1 | `InboundNormalizerProfile._normalizer_injection_boundary` (Pydantic model_validator) | `event_type` 必须以 `WMS_/ECS_/DEVICE_` 开头；`source_provider` 与 event_type 前缀匹配；`correlation_resolution` 必为 `manual/auto/hybrid` |
| 2 | 新增 `InboundNormalizerRegistry` (`src/app/runtime/inbound_normalizer_registry.py`) | 与 CapabilityPortRegistry 严格分离：只允许 inbound normalizer 注册到本表，业务 capability 不可注入 |
| 3 | 扩展 `RuntimeCapabilityContext.get_inbound_normalizer()` | 路由检查：`caller_module` 必须以 `src.app.runtime.orchestration.consumers` 开头，否则抛 `PermissionError` |

---

## 3. 模块边界与文件职责

| 文件 | 类型 | 职责 | 依赖 |
|------|------|------|------|
| `src/app/wms_integration/ports/document.py` | 新增 | WmsDocumentPort Protocol + 5 Pydantic 数据类（get_grn/list_grn_items/get_pick_order/get_outbound_order/get_wave/get_task_snapshot） | pydantic, typing.Protocol |
| `src/app/wms_integration/ports/fulfillment.py` | 新增 | WmsFulfillmentPort Protocol + 2 Pydantic 数据类（7 effect 方法） | pydantic, typing.Protocol |
| `src/app/wms_integration/ports/event.py` | 新增 | InboundEventPort 基协议 + WmsEventPort + 4 normalizer 数据类 | pydantic, typing.Protocol |
| `src/app/wms_integration/ports/reconciliation_query.py` | 新增 | WmsReconciliationQueryPort Protocol + 1 Pydantic 数据类（3 只读方法） | pydantic, typing.Protocol |
| `src/app/wms_integration/ports/__init__.py` | 修改 | docstring 更新：4 ports 全部落地（Phase 1 CEO-001 7/7） | — |
| `src/app/runtime/inbound_normalizer_registry.py` | 新增 | InboundNormalizerRegistry 类（约 100 行） | — |
| `src/app/runtime/capability_port_registry.py` | 修改 | RuntimeCapabilityContext.get_inbound_normalizer() 新增方法 + 接受 inbound_registry 参数 | InboundNormalizerRegistry |
| `src/app/contracts/external_contract_profile.py` | 修改 | InboundNormalizerProfile._normalizer_injection_boundary model_validator | — |
| `scripts/architecture-guardrails.sh` | 修改 | rule_ri3c() + phase1 调用链 hook + 注释更新 | — |
| `.import-linter.ini` | 新增 | capability-isolation contract（按 SPEC §279 forbidden_modules） | import-linter |
| `scripts/import-linter-check.sh` | 新增 | 包装 import-linter CLI | import-linter |
| `scripts/git-quality-gate.sh` | 修改 | ruff 之后增加 import-linter 检查步骤 | — |
| `pyproject.toml` | 修改 | [dependency-groups] dev 加 import-linter = ">=2.0" | — |

---

## 4. 状态流与错误码

### 4.1 InboundNormalizerRegistry 注册流程

```
register(port_protocol, factory)
  ├─ 防御性检查: port_protocol.__name__ ∈ _INBOUND_NORMALIZER_TYPE_NAMES
  │   → 抛 ValueError("拒绝注册 normalizer 类型作为 capability port")
  ├─ 否则: 存入 _factories[port_name] = factory
  └─ 返回
```

### 4.2 RuntimeCapabilityContext.get_inbound_normalizer 路由流程

```
get_inbound_normalizer(port_protocol, *, caller_module)
  ├─ 路由检查: caller_module.startswith("src.app.runtime.orchestration.consumers")
  │   ├─ True: self._inbound_registry.get(port_protocol)
  │   └─ False: 抛 PermissionError("业务 capability 不可注入 inbound normalizer, 仅 RuntimeInboxConsumer 可调用")
  └─ 返回 normalizer 实例
```

### 4.3 Pydantic model_validator 错误码

```
InboundNormalizerProfile 校验失败时抛 ValueError, 包含三类:
  1. "event_type 必须以 ('WMS_', 'ECS_', 'DEVICE_') 之一开头"
  2. "source_provider={x} 与 event_type={y} 前缀不一致"
  3. "correlation_resolution 必为 manual/auto/hybrid 之一"
```

### 4.4 architecture-guardrails.sh R-I3c 违规输出

```
[R-I3c] R-I3c violation
  file: src/app/workline/services/<file>.py:<line>
  reason: 业务 capability 持有 inbound normalizer Protocol, 违反主计划 §3.5 I3
  fix: 业务 capability 只能 import query/effect port contract (WmsMasterDataPort 等)
```

---

## 5. 数据字段（新增 Pydantic 数据类清单）

### 5.1 WmsDocumentPort 数据类（5 个）

| 数据类 | 字段 |
|--------|------|
| `WmsGrnInfo` | grn_id, grn_type, status, received_at, total_items, warehouse_code |
| `WmsGrnItem` | grn_id, material_code, quantity, batch_no, package_id |
| `WmsPickOrder` | pick_order_id, wave_id, status, lines, priority |
| `WmsOutboundOrder` | outbound_order_id, customer_code, status, lines, ship_date |
| `WmsWave` | wave_id, status, pick_order_ids, scheduled_at |
| `WmsTaskSnapshot` | task_id, task_type, status, payload, correlation_id |

### 5.2 WmsFulfillmentPort 数据类（2 个）

| 数据类 | 字段 |
|--------|------|
| `WmsFulfillmentResult` | request_id, accepted, reason, warehouse_code |
| `WmsPalletBindingResult` | package_id, pallet_id, bound_at, station_code |

### 5.3 WmsEventPort 数据类（4 normalizer + InboundEventEnvelope 共享）

| 数据类 | 字段 |
|--------|------|
| `InboundEventEnvelope` (共享基类) | source_event_id, provider_code, occurred_at, raw_payload |
| `WmsGrnReceivedEvent` | grn_id, warehouse_code, items |
| `WmsPalletArrivedEvent` | pallet_id, warehouse_code, arrived_station |
| `WmsRackArrivedEvent` | rack_id, warehouse_code, station_code |
| `WmsTransportCompletedEvent` | request_id, completed_at, result_code |

### 5.4 WmsReconciliationQueryPort 数据类（1 个）

| 数据类 | 字段 |
|--------|------|
| `WmsDriftItem` | entity_type, entity_id, wes_state, wms_state, drift_kind, detected_at |

---

## 6. 验收标准与测试场景

### 6.1 单元测试（每个 port 文件）

| 测试 | 覆盖内容 |
|------|---------|
| `test_wms_document_port_protocol_signatures` | 6 方法签名（参数类型 + 返回类型）匹配 docstring |
| `test_wms_fulfillment_port_protocol_signatures` | 7 方法签名匹配 |
| `test_wms_event_port_normalizer_signatures` | 4 normalizer 方法签名匹配 |
| `test_wms_reconciliation_query_port_signatures` | 3 只读方法签名匹配 |
| `test_wms_7_ports_protocols_are_abstract` | 7 port 类都是 `typing.Protocol` 子类 |
| `test_wms_7_ports_have_docstrings` | 7 port 类和所有方法都含 docstring |

### 6.2 架构护栏测试

| 测试 | 覆盖内容 |
|------|---------|
| `test_ri3c_inbound_normalizer_blacklist_in_capability` | `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` 在 `src/app/runtime` 和 `src/app/workline` 不出现 |
| `test_ri3c_runtime_inbox_not_in_capability` | `RuntimeInbox` / `RuntimeInboxConsumer` 在 capability 路径不出现 |
| `test_ri3c_inbound_normalizer_registry_rejects_blacklist` | `InboundNormalizerRegistry.register(WmsEventPort, ...)` 抛 `ValueError` |
| `test_ri3c_runtime_capability_context_routing_allows_consumer` | 允许路径 (`src.app.runtime.orchestration.consumers`) 调用 `get_inbound_normalizer` 正常 |
| `test_ri3c_runtime_capability_context_routing_blocks_business_capability` | 业务 capability 调用 `get_inbound_normalizer` 抛 `PermissionError` |

### 6.3 Pydantic 校验测试

| 测试 | 覆盖内容 |
|------|---------|
| `test_inbound_normalizer_profile_rejects_unknown_event_type` | `event_type="FOO_BAR"` 抛 ValueError |
| `test_inbound_normalizer_profile_rejects_prefix_mismatch` | `source_provider="wms"` 但 `event_type="ECS_..."` 抛 ValueError |
| `test_inbound_normalizer_profile_rejects_invalid_correlation` | `correlation_resolution="foo"` 抛 ValueError |
| `test_inbound_normalizer_profile_accepts_valid` | 三类合规输入均通过 |

### 6.4 端到端验证

```bash
# 每个 commit 后立即验证（最小化回归）
uv run pytest tests/architecture/ -v

# PR 前最终全量
uv run pytest --cov=src
uv run ruff format . && uv run ruff check .
./scripts/architecture-guardrails.sh --phase phase1
./scripts/import-linter-check.sh
./scripts/git-quality-gate.sh --profile quality

# 影响范围检查（commit 前必做）
npx gitnexus detect_changes --scope all
```

---

## 7. 风险与回滚

| 风险 | 应对策略 |
|------|---------|
| import-linter forbidden_modules 与现有 allowlist 冲突 | 先 dry-run import-linter 列出冲突；最坏情况下 scope 缩到 `src.app.runtime.capability_*` 即可（最小可行 contract） |
| InboundNormalizerRegistry 与 CapabilityPortRegistry 功能重复 | 保留两者（语义分离：query/effect vs inbound），不抽取基类（KISS） |
| 单 PR 过大导致 gitnexus impact HIGH | 先 dry-run `npx gitnexus detect_changes --scope all`；若 HIGH，按用户决策（单 PR）必须完成；必要时把 docs 同步放最后独立 commit |

---

## 8. 不在本次范围内（Phase 2+ 才做）

- 旧 `services/transport_contract.py` 的彻底迁移（Phase 1 SPEC §125 标注 Phase 1 清理但 Phase 2 才正式迁移）
- `services/callback_normalizer.py` 提升为 normalizer 部分的具体实现（本次仅建协议骨架）
- `SecurityProfile` Phase 3 完整实现（HMAC canonical string、secret_kid 必填）
- RuntimeInbox 状态机具体 consumer 实现（仅保留端口边界）

---

## 9. 实施步骤（推荐 1 个 PR 内 8 个 atomic commit）

每个 commit 独立、含测试、Conventional Commits、**不写 Co-Authored-By**：

1. **`chore(deps): add import-linter dependency`** —— `pyproject.toml` 加 import-linter；`uv sync --dev`
2. **`feat(wms-ports): add WmsDocumentPort protocol + 5 typed data classes`** —— `ports/document.py` + `ports/__init__.py` 注释更新
3. **`feat(wms-ports): add WmsFulfillmentPort protocol + 2 typed data classes`** —— `ports/fulfillment.py`
4. **`feat(wms-ports): add WmsEventPort protocol with 4 normalizer + InboundEventPort base`** —— `ports/event.py`
5. **`feat(wms-ports): add WmsReconciliationQueryPort protocol + 1 typed data class`** —— `ports/reconciliation_query.py`
6. **`feat(contracts): harden InboundNormalizerProfile with injection boundary validators`** —— `external_contract_profile.py` model_validator + 测试
7. **`feat(runtime): add InboundNormalizerRegistry + RuntimeCapabilityContext.get_inbound_normalizer`** —— `inbound_normalizer_registry.py` + `capability_port_registry.py` + 路由测试
8. **`feat(architecture): enforce R-I3c inbound normalizer port guardrail + import-linter capability-isolation contract`** —— `architecture-guardrails.sh` rule_ri3c + `.import-linter.ini` + `import-linter-check.sh` + `git-quality-gate.sh` hook + `test_ri3c_inbound_normalizer_port_guardrail.py` + `test_wms_7_ports_contract.py`
9. **`docs(architecture): sync Phase 1 Packet D completion status`** —— `workline-and-plugin-restructuring.md` §10.2 状态表 + `file_index.md`

---

## 10. 参考文档（不修改，仅校对一致性）

- `docs/superpowers/specs/2026-06-26-workline-restructuring-phase-1-spec.md` —— Phase 1 总 SPEC
- `docs/architecture/workline-and-plugin-restructuring.md` —— 主计划
- `docs/integration/wms_rcs_interface_requirements.md` —— WMS 接口真实语义参考
- `docs/integration/third_party_integration_whitepaper.md` —— 回调 normalizer 原则
- `docs/integration/callback_event_validation_principles.md` —— 回调事件校验原则
- `docs/integration/wms_caller_checklist.md` —— WMS 调用方 checklist