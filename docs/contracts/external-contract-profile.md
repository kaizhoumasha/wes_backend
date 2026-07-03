---
status: Phase 0 外部合同 profile
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md
related: docs/architecture/target-state-contract.md, docs/architecture/integration-lab-and-simulator.md
note: |
  本文件定义 WMS/ECS provider 的外部合同 profile。
  Pydantic 校验模型在 tests/support/external_contract_profile.py（Phase 0 测试专用，
  禁止 src/app/ import；Phase 1 CEO-013 升级到 src/app/wms_integration/models/）。
  security_profile（HMAC canonical）Phase 0 只占位，留 Phase 3 external-callback-auth-spec.md。
---

# 外部合同 Profile（P0-006）

> 父设计：主计划 §3.5.1 外部合同支撑、§5.1 ExternalContractProfile
> 目标态合同：`target-state-contract.md` §6 WMS/RCS 集成边界

## 1. 编写目的

按 `provider_code + contract_version` 描述 WMS/ECS/RCS provider 的能力、字段映射、超时、重试、fixture set 和不支持动作，使 Runtime capability admission 和 callback normalizer 能在合同约束下工作，不依赖供应商 DTO/SDK。

## 2. ExternalContractProfile 字段表

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `provider_code` | string | yes | 稳定 provider ID，如 `WMS`、`ECS` |
| `contract_version` | string | yes | 合同版本，建议 ISO date 或 semver |
| `environment` | enum | yes | `sandbox` / `staging` / `production`；Phase 0 fixture 只能用 `sandbox` |
| `runtime_capabilities.query` | string[] | yes | 只能列 query port method，例如 `WmsMasterDataPort.get_material` |
| `runtime_capabilities.effect` | string[] | yes | 只能列 effect port method，例如 `WmsFulfillmentPort.request_transport` |
| `inbound_normalizers.event` | string[] | yes | provider 允许的 event type |
| `inbound_normalizers.result` | string[] | yes | provider 允许的 result/callback type |
| `field_mapping` | object | yes | event/result 到 typed envelope 字段的映射 |
| `timeout_retry.query_timeout_seconds` | int | yes | query 超时，必须大于 0 |
| `timeout_retry.effect_timeout_seconds` | int | conditional | effect port 存在时必填 |
| `timeout_retry.retry_backoff_seconds` | int[] | yes | 递增短退避数组 |
| `timeout_retry.cache_ttl_seconds` | int | conditional | query cache 存在时必填；0 表示禁用 |
| `fixture_set.path` | string | yes | `tests/fixtures/external_contracts/<provider>/<profile>` |
| `fixture_set.required_cases` | string[] | yes | 至少覆盖 success、reject、timeout、duplicate、missing_event_id 中适用场景 |
| `unsupported_actions` | string[] | yes | 未支持动作，例如 `direct_rcs_dispatch` |
| `security_profile` | object | optional | Phase 0 只允许占位，不展开 HMAC canonical |
| `notes` | string | optional | 仅记录合同解释，不允许写实现 workaround |

## 3. YAML 示例

```yaml
provider_code: WMS
contract_version: "2026-06-25"
environment: sandbox
runtime_capabilities:
  query:
    - WmsMasterDataPort.get_material
    - WmsInventoryQueryPort.query_inventory
  effect:
    - WmsFulfillmentPort.request_transport
inbound_normalizers:
  event:
    - WMS_GRN_RECEIVED
    - WMS_TRANSPORT_COMPLETED
  result: []
field_mapping:
  WMS_GRN_RECEIVED:
    source_event_id: data.event_id
    external_ref: data.grn_id
timeout_retry:
  query_timeout_seconds: 10
  effect_timeout_seconds: 30
  retry_backoff_seconds: [1, 2, 4]
  cache_ttl_seconds: 30
fixture_set:
  path: tests/fixtures/external_contracts/wms/default
  required_cases: [success, reject, timeout, duplicate, missing_event_id]
unsupported_actions:
  - direct_rcs_dispatch
security_profile:
  phase3_spec: external-callback-auth-spec.md
  placeholder: true
```

## 4. schema 校验方式

| 校验项 | 要求 |
| --- | --- |
| 格式 | YAML 或 JSON 均可，但必须由 Pydantic model 校验 |
| Phase 0 测试 model 路径 | `tests/support/external_contract_profile.py`（仅供 fixture 校验与 contract tests import；**禁止** `src/app/` 下任何模块 import） |
| Phase 1 生产 model 路径 | `src/app/contracts/external_contract_profile.py`（Phase 1 CEO-013 实施，从 `tests/support/` 升级；Phase 0 不创建此文件） |
| fixture 校验 | 每个 fixture 必须声明 `provider_code`、`contract_version`、`case_id`、`expected_port` |
| unsupported action | provider 未声明能力时，runtime capability 和 callback API 必须拒绝 |

## 5. 合同版本规则（来源主计划 §3.5.1）

- WES 内部域只识别 typed port contract；外部合同变化只能落在 `ExternalContractProfile` 和 adapter/normalizer
- 合同 profile 可破坏性替换，不保留旧中台兼容入口；但同一 `ExecutionSession` 固定 `provider_code + contract_version`，不在 RUNNING 期间热切
- 每个 provider profile 必须配套 contract tests、sample callback、error fixture 和 replay scenario
- 未声明的 `runtime_capabilities` 不得进入 `RuntimeCapabilityContext`；未声明的 `inbound_normalizers` 不得被 callback API 接收
- profile 未声明的字段不得进入 runtime capability；必须被 normalizer 丢弃或写入诊断 evidence
- `field_mapping` 不承载业务分支、计算规则或流程决策；复杂转换必须在 adapter/normalizer 代码中实现，并由 contract tests 覆盖

## 6. fixture schema

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `case_id` | string | yes | 稳定测试用例 ID |
| `provider_code` | string | yes | 必须与 profile 一致 |
| `contract_version` | string | yes | 必须与 profile 一致 |
| `expected_port` | string | yes | `Port.method` |
| `direction` | enum | yes | `query` / `effect` / `event` / `result` |
| `raw_request` | object | conditional | 出站 query/effect 必填 |
| `raw_response` | object | conditional | 出站 query/effect 必填 |
| `raw_callback` | object | conditional | 入站 event/result 必填 |
| `expected_typed` | object | yes | adapter/normalizer 的 typed output |
| `expected_error` | object | optional | reject/timeout/invalid case 的错误模型 |

## 7. 验收（SPEC P0-006）

1. ✅ Pydantic schema 落在 `tests/support/external_contract_profile.py`，未污染生产 import path
2. ✅ `unsupported_actions` 在 provider 未声明能力时，runtime capability 和 callback API 必须拒绝（§4）
3. ✅ fixture 可被 adapter contract tests 复用（§6 fixture schema + `tests/fixtures/external_contracts/wms/default`）
