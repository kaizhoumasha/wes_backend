---
adr_id: 0009
title: src/app/contracts/ 共享 contract 层承载 ExternalContractProfile / RuntimeCapabilityProfile / InboundNormalizerProfile
status: Accepted (Phase 1 AP1)
date: 2026-06-26
deciders: architect lead, security engineer
phase: Phase 1 Foundation (AP1 prerequisite for CEO-013)
related:
  - ../../superpowers/specs/2026-06-26-workline-restructuring-phase-1-spec.md
  - ../../contracts/external-contract-profile.md
  - ../../../architecture/workline-and-plugin-restructuring.md (主计划 §5.1 + §3.5.1)
supersedes: null
---

# ADR-0009: 共享 contract 层选 `src/app/contracts/` 而非 `src/app/wms_integration/models/`

## Context

Phase 0 P0-006 把 `ExternalContractProfile` 落在 `tests/support/external_contract_profile.py`（测试专用）。Phase 1 CEO-013 必须把它升级到生产路径供 `wms_integration` / `device` / `runtime` 三域共享。

Phase 1 SPEC 评审（autoplan security-engineer 维度）指出风险：

> CEO-013 升级路径：建议落 `src/app/contracts/` 共享层（避免 R-I3b 误报）

原因：`tests/support/external_contract_profile.py` 是 Pydantic BaseModel，按主计划 §3.4 + §3.5.1 共享类型。如果升级到 `src/app/wms_integration/models/`，则：
- `device` 域 import `wms_integration.models` 触发 R-I3b
- `runtime` 域 import `wms_integration.models` 触发 R-I3b
- 反向 ACL：wms_integration 内部域本应独立，但被 runtime 依赖

候选方案评估：

| 方案 | 落地路径 | R-I3b 误报 | 跨域引用 | 维护 |
| --- | --- | --- | --- | --- |
| A. `src/app/wms_integration/models/` | 低（路径短） | 严重（device + runtime 域均触发） | 反向 | 散落到不同域需要同步 |
| B. `src/app/contracts/` | 中（新目录） | 无（不在任何业务域下） | 单向 | 集中 |
| C. `src/core/` | 中（复用现有 src/core 模式） | 无 | 单向 | 与现有 AuthorityMetadata 同行 |
| D. 保留 tests/support/ 不升级 | 零 | 无 | 测试 import 跨域 | 不满足 CEO-013 |

## Decision

**选 B（src/app/contracts/）**，理由：

1. **避免 R-I3b 误报**：`scripts/architecture-guardrails.sh:154` 扫描 `from src.app.wms_integration.(services|models|clients|providers).* import`，将 `ExternalContractProfile` 放 `wms_integration/models/` 会导致 `device` 域和 `runtime` 域一旦 import 就被 R-I3b 标记。共享 contract 层路径 (`src/app/contracts/`) 不在任何业务域下，device/runtime 域 import 共享 contract 不会触发 R-I3b。
2. **路径语义清晰**：`src/app/contracts/` 目录命名直接表达"跨域共享合同"，与 `src/app/wms_integration/models/`（域内 DTO）和 `src/core/`（基础设施）三层职责分明。
3. **不与 src/core/ 混用**：`src/core/` 已承载 `authority_metadata.py`（C3 共享）与 response 基础设施；新增 contracts 共享层会扩大 src/core 职责，未来 src/core 会与 `architecture-guardrails.sh` 排除的 `src/app/*/services|models` 混淆。独立目录隔离更清晰。
4. **符合 Phase 0 → Phase 1 路径**：Phase 0 P0-006 把 schema 落 `tests/support/` 已明确"测试专用，Phase 1 升级到生产路径"。升级到 `src/app/contracts/` 是这一约定的自然演进。
5. **可扩展性**：Phase 1 之后可继续加 `src/app/contracts/inbound_event.py` / `src/app/contracts/typed_envelope.py` 等共享类型，不影响域结构。

## Consequences

**正面**：
- `device` 域 + `runtime` 域可 import `src/app/contracts/` 不触发 R-I3b
- 三个合同 DTO 集中维护，CEO-013 升级 `ExternalContractProfile` 与 CEO-009 设计 `RuntimeCapabilityProfile` / `InboundNormalizerProfile` 可同步评审
- 跨域一致性：主计划 §3.5.1 ExternalContractProfile + §3.5 InboundNormalizer + §9.2 RuntimeCapability 三者统一一个 namespace

**中性**：
- 新增一个 src/app/ 子目录，需在主计划 §3.2 域结构表登记 `contracts/` 为共享层（域外）
- `tests/support/external_contract_profile.py` 后续可以删（CEO-013 完成后），或保留为 SDK 兼容入口（建议保留 1 个 deprecation 版本再删）

**负面**：
- 跨域引用方向增加：之前 8 个域各自 import 自己的 models；现在 device + runtime 都会 import `src/app/contracts/`，未来需 R-I3a/b guardrail 考虑是否 contracts 也需 type guard
- `src/app/contracts/` 不能有运行时副作用（必须纯 typed DTO）；如未来有副作用逻辑，需移回各域

## 落地清单

| 项 | Owner | Phase | 验收 |
| --- | --- | --- | --- |
| `src/app/contracts/__init__.py` | architect | 1b | 包初始化导出 |
| `src/app/contracts/external_contract_profile.py` | architect | 1b | ExternalContractProfile / SecurityProfile / RuntimeCapabilityProfile / InboundNormalizerProfile 4 个 typed DTO |
| 删除 `tests/support/external_contract_profile.py` | architect | 1b 完成后 | 旧 tests 仍 import (需 deprecation) |
| `src/app/contracts/inbound_event_envelope.py` (Phase 1c 扩展) | runtime | 1c | inbound event envelope typed 共享 |
| 填 H1 外部权威 QueryPort response 注册表 | CEO-005 | 1b | `src/core/authority_registry.py` 含 WMS MasterData/Document/InventoryQuery/ReconciliationQuery |

## Verification

- `bash scripts/architecture-guardrails.sh --phase phase1` 通过：seed allowlist 不需新增 src/app/contracts/ 相关 seed（共享层不触发任何规则）
- `uv run pytest tests/architecture/ tests/contracts/` 全过：ExternalContractProfile Pydantic schema 验证 + Phase 0 BC-09 行为契约测试继续通过
- CEO-013 升级 `tests/support/` 引用到 `src/app/contracts/`，删除旧文件后所有 contract test 仍过

## References

- 主计划 §3.5.1 外部合同支撑 + §5.1 ExternalContractProfile + §3.4 Authority Matrix
- Phase 0 P0-006 `docs/contracts/external-contract-profile.md`
- Phase 0 P0-002 `legacy-cleanup-matrix.csv` (R-I3a/R-I3b seed 31 条)
- Phase 1 SPEC `2026-06-26-workline-restructuring-phase-1-spec.md` AP1 + H1
