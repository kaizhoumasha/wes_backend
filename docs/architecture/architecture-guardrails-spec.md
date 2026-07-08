---
status: stable architecture guardrails
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
related: docs/architecture/legacy-cleanup-matrix.md
note: |
  将当前架构不变量映射到脚本、测试和 review checklist。
  规则 ID 使用稳定架构名称；历史编号只保留在归档材料中。
---

# Architecture Guardrails

> 脚本：`scripts/architecture-guardrails.sh`  
> allowlist：`scripts/architecture-guardrails.allowlist`  
> 测试：`tests/architecture/`

## 1. 编写目的

本文记录当前仍由自动化守住的架构边界，使实现者无需回读顶层设计即可执行架构约束。

## 2. Stable Guardrail Map

| ID | 不变量 | 自动化入口 | 脚本规则 / 测试 |
| --- | --- | --- | --- |
| `WMS_INTEGRATION_BOUNDARY` | 内部域不得 import WMS DTO/client/provider | import scan | `rule_wms_integration_boundary` |
| `EXECUTION_CORRELATION_BOUNDARY` | 跨域 session FK 收敛为 `ExecutionCorrelation` | FK 引用扫描 | `rule_execution_correlation_boundary` |
| `AUTHORITY_METADATA_BOUNDARY` | 查询响应强制 `scope/authority/source/evidence_at` | Pydantic schema test | `rule_authority_metadata_boundary` + `tests/architecture/test_authority_metadata_boundary_guardrail.py` |
| `DEVICE_COMMAND_BOUNDARY` | DeviceCommand 不含 PLC/坐标/关节/安全回路字段 | 字段声明扫描 | `rule_device_command_boundary` |
| `RUNTIME_INBOX_STATE_MACHINE` | RuntimeInbox 支持接收、处理、失败、死信与重放状态契约 | 状态机测试 | `tests/architecture/test_runtime_inbox_state_machine_guardrail.py` |
| `CAPABILITY_FORBIDDEN_DEPENDENCY` | capability 注入不得持有 HTTP client、service locator 或 provider exception | keyword scan | `rule_capability_forbidden_dependency` |
| `CAPABILITY_IMPLEMENTATION_IMPORT` | capability 不得 import wms_integration/device services/models 实现 | import scan | `rule_capability_implementation_import` |
| `INBOUND_NORMALIZER_OWNERSHIP` | inbound normalizer 只能由合法 runtime 入口持有 | AST scan | `rule_inbound_normalizer_ownership` |
| `LEGACY_RUNTIME_IMPORT` | production code 不得 import 已删除的 `src.workline_runtime` | import scan | `rule_legacy_runtime_import` |

## 3. Capability Boundary

Capability 只能暴露 port contract，不得直接持有 provider implementation、HTTP client、service locator、provider exception、DTO、inbound normalizer 或 runtime inbox consumer。

`CAPABILITY_FORBIDDEN_DEPENDENCY`、`CAPABILITY_IMPLEMENTATION_IMPORT` 和 `INBOUND_NORMALIZER_OWNERSHIP` 必须同时通过，才算 capability 边界合规。

## 4. Script Modes

```bash
bash scripts/architecture-guardrails.sh --mode warn
bash scripts/architecture-guardrails.sh --mode enforced
bash scripts/architecture-guardrails.sh --mode expiry-check
```

| Mode | 行为 | 退出码 |
| --- | --- | ---: |
| `warn` | 打印违规但不阻塞 | 0 |
| `enforced` | allowlist 之外违规失败 | 1 |
| `expiry-check` | `enforced` + 过期 allowlist 失败 | 1 |

## 5. Allowlist Contract

```text
rule_id|path|reason|expires_at|legacy_entry_id|drop_phase
```

| 字段 | 要求 |
| --- | --- |
| `rule_id` | 必须使用 stable guardrail ID |
| `path` | 违规文件路径；`CAPABILITY_IMPLEMENTATION_IMPORT` / `INBOUND_NORMALIZER_OWNERSHIP` 必须逐文件枚举 |
| `reason` | 豁免原因 |
| `expires_at` | 过期日期 `YYYY-MM-DD` |
| `legacy_entry_id` | 精确关联 `legacy-cleanup-matrix.csv` 第一列 entry_id |
| `drop_phase` | 必须与 matrix 对应 entry 的审计字段一致 |

脚本内置校验：

- `legacy_entry_id` 必须精确匹配 matrix。
- `drop_phase` 必须与 matrix 对应 entry 一致。
- `expires_at` 必须存在且可解析。
- capability implementation import 与 inbound normalizer ownership 不允许目录前缀 allowlist。

## 6. Quality Gate

`scripts/git-quality-gate.sh --profile quality` 会运行：

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
bash scripts/architecture-guardrails.sh --mode enforced
```

## 7. 验收

- `bash scripts/architecture-guardrails.sh --mode enforced` 退出码 0。
- 删除任意仍被实际违规命中的 seed allowlist 行后，`--mode enforced` 对应失败。
- `tests/architecture/` 覆盖 stable guardrail ID、allowlist contract、legacy matrix reverse reference 和 RuntimeInbox state machine。
