---
status: Phase 0 架构护栏
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md
related: docs/architecture/legacy-cleanup-matrix.md
note: |
  将主计划 §7.5 17 条不变量映射到脚本/测试/review checklist。
  Phase 0 建 phase-aware 机制 + seed allowlist + 失败样例;
  Phase 1 起按 §7.5 "核心 5 条违反立即阻塞 PR" 语义执行 enforced。
---

# Architecture Guardrails（P0-007）

> 父设计：主计划 §7.5 关键不变量（17 条 / 三级分类）
> 脚本：`scripts/architecture-guardrails.sh`
> allowlist：`scripts/architecture-guardrails.allowlist`
> 测试：`tests/architecture/`

## 1. 编写目的

将主计划 §7.5 的 17 条不变量映射为可执行扫描、测试和 review checklist，使实现者无需回读顶层设计即可执行架构约束。

## 2. §7.5 不变量映射

### 2.1 核心 5 条（强制自动检查，违反阻塞 PR）

| ID | 不变量 | 自动化入口 | 脚本规则 |
| --- | --- | --- | --- |
| C1 | 内部域不得 import WMS DTO/client/provider | `architecture-guardrails.sh` import scan | `rule_c1` |
| C2 | 跨域 session FK 收敛为 `ExecutionCorrelation` | schema lint + FK 引用扫描 | `rule_c2` |
| C3 | 查询响应强制 `scope/authority/source/evidence_at` | Pydantic schema test | `rule_c3` + `tests/architecture/test_c3_*` |
| C4 | DeviceCommand 不含 PLC/坐标/关节/安全回路字段 | 字段白名单扫描 | `rule_c4` |
| C5 | RuntimeInbox 状态机契约 | 状态机测试 | `tests/architecture/test_c5_*` |

### 2.2 重要 8 条（Phase 门禁检查）

| ID | 检查目标 | Phase 0 落点 |
| --- | --- | --- |
| I1 | callback HMAC + nonce TTL + path canonical | Phase 3 SPEC 占位; 门禁脚本预留 |
| I2 | idempotency 复合键 + hash 冲突 409 | Phase 1 schema 落地后接入 |
| I3 | capability 注入只能暴露 port contract | R-I3a + R-I3b（见 §3） |
| I4 | Event_Push 只能 ACK | BC-10 contract test + `rule_c3` response guard |
| I5 | manifest version pin | Phase 1 CEO-011 落地后接入 |
| I6 | DeviceCommand dispatch 前 ECS IDLE | Phase 1 CEO-010 + device-command-contract.md |
| I7 | 位置投影只能来自 evidence/RuntimeLocationEvent | Phase 1 schema 落地后接入 |
| I8 | Event_Push 响应拦截 command-like 字段 | BC-10 contract test |

### 2.3 设计 4 条（review 检查）

| ID | 检查目标 | review checklist |
| --- | --- | --- |
| D1 | 目标态契约优先，旧 API/旧表/旧插件不得反向约束 | PR review |
| D2 | B 方案以目标态边界 + 行为契约测试 + 破坏性清理清单为前置 | Phase 2 go/no-go 评审 |
| D3 | plane 接口不允许全员可读全量运营数据 | target-state-contract.md §5 |
| D4 | 当前阶段 RCS/AGV/CTU 调度只能经 WMS 履约 port | target-state-contract.md §6 |

## 3. I3 capability 注入/import 边界（拆为 R-I3a + R-I3b）

主计划 §7.5 I3 禁用对象较多，拆为两条规则共同覆盖：

| 规则 | 扫描 | 失败条件 |
| --- | --- | --- |
| R-I3a | `http_client\|service_locator\|WmsClientException\|DeviceClientException` 在 `src/app/runtime src/app/workline` | capability 持有 HTTP client/service locator/provider exception |
| R-I3b | `from src.app.(wms_integration\|device).(services\|models)..* import` 在 `src/app/runtime src/app/workline` | capability import wms_integration/device 实现 |

R-I3a + R-I3b 共同覆盖 §7.5 I3 全部禁用对象（HTTP client、DTO、provider exception、service locator、`WmsEventPort`、`DeviceEventPort`、`RuntimeInbox` consumer）。两条规则必须同时通过才算 I3 合规。

## 4. 脚本最小扫描规则

| Rule | 命令形态 | 失败条件 |
| --- | --- | --- |
| C1 | `grep -rnE 'from src\.app\.wms_integration\.(services\|models\|clients\|providers).* import' src/app` (排除 wms_integration 自身) | 内部域 import WMS implementation/DTO/client/provider |
| C2 | `grep -rnE 'workline_session_id\|material_session_id' src/app` (排除 runtime/orchestration) | 跨域表直接 FK 到 execution session |
| C3 | Pydantic schema test (`validate_authority_metadata`) | query response 缺 scope/authority/source/evidence_at |
| C4 | `grep -rniE 'plc\|coordinate\|joint_angle\|x_coord\|y_coord\|safety_loop' src/app/device src/app/workline src/app/runtime` | DeviceCommand/manifest/runtime 出现禁止字段 |
| C5 | `tests/architecture/test_c5_*` 状态机测试 | RuntimeInbox 无法覆盖 retry/dead-letter/replay |
| R-I3a | `grep -rnE 'http_client\|service_locator\|WmsClientException\|DeviceClientException' src/app/runtime src/app/workline` | capability 注入禁用关键词 |
| R-I3b | `grep -rnE 'from src\.app\.(wms_integration\|device)\.(services\|models)\..* import' src/app/runtime src/app/workline` | capability import wms_integration/device 实现 |

## 5. Phase-aware Enforcement Mode

`scripts/architecture-guardrails.sh --phase phase0|phase1|phase2`

| Phase | 模式 | 违规处理 | 退出码 | CI 阻塞 |
| --- | --- | --- | ---: | --- |
| `phase0` | warn-only | 打印所有违规但不退出非零 | 0 | 否 |
| `phase1` | enforced | allowlist 之外的 C1/C2/C3/C4/C5/R-I3a/R-I3b 违规即失败 | 1 | 是（PR 不可合） |
| `phase2`+ | enforced + 缩减 allowlist | 同 phase1，并要求每个 PR 至少消除一条 expired allowlist 项 | 1 | 是 |

Phase 0 建立 seed allowlist 覆盖已知 legacy 违规，确保 Phase 1 切 enforced 时不因历史包袱直接 fail。

## 6. allowlist 规范

### 6.1 格式

```
rule_id|path|reason|expires_at|legacy_entry_id|drop_phase
```

| 字段 | 要求 |
| --- | --- |
| `rule_id` | C1/C2/C3/C4/R-I3a/R-I3b |
| `path` | 违规文件路径；R-I3b 必须逐文件枚举，禁止目录前缀 |
| `reason` | 豁免原因 |
| `expires_at` | 过期日期 YYYY-MM-DD（**无过期视为失败**） |
| `legacy_entry_id` | 精确关联 `legacy-cleanup-matrix.csv` 第一列 entry_id（便于随 Phase 5 清理自动过期） |
| `drop_phase` | 必须与 `legacy-cleanup-matrix.csv` 对应 entry 的 `drop_phase` 一致 |

### 6.2 校验规则（脚本内置，非人工核对）

1. `legacy_entry_id` 必须精确匹配 `legacy-cleanup-matrix.csv` 第一列
2. `drop_phase` 必须与 matrix 对应 entry 一致
3. `expires_at` 必须存在且可解析；过期行在 phase1 先 warning，phase2+ 失败
4. R-I3b allowlist 不允许 `src/app/workline/services/`、`src/app/workline/repositories/` 等目录前缀，避免未来违规被历史 seed 覆盖
5. 删除任意 seed allowlist 行后，`--phase phase1` 必须对对应历史违规返回非零（证明 enforcement 不是空跑）

## 7. CI/Jenkins 接入

`scripts/git-quality-gate.sh --profile quality` 与 `--check architecture` 均会调用：

```bash
architecture-guardrails.sh --phase ${ARCHITECTURE_PHASE:-phase0}
```

- Phase 0 默认 phase0（warn-only）
- Phase 1 起设 `ARCHITECTURE_PHASE=phase1` 切 enforced

Jenkinsfile `Quality Checks` stage 新增 `Architecture Guardrails` 并行步骤。

## 8. 验收（SPEC P0-007）

1. ✅ `scripts/architecture-guardrails.sh --phase phase0` 可本地运行且退出码 0（warn-only）
2. ✅ `--phase phase1` 在 seed allowlist 覆盖下退出码 0；删任意 seed 行必失败
3. ✅ 脚本输出包含检查项 ID、失败文件、失败原因、修复提示
4. ✅ 后续 Phase 门禁能以 `--phase` 切换模式
5. ✅ `scripts/git-quality-gate.sh --check architecture` 已接入
6. ✅ CI/Jenkins 已接入 architecture guardrails 步骤
7. ✅ `tests/architecture/` 覆盖 C1-C5 + R-I3a/R-I3b
8. ✅ C5 使用 `tests/support/runtime_inbox_contract.py` 目标态模型，不 import legacy `WorklineInbox`

## 9. 后续 Phase 落地

| Phase | 任务 | 本基线锁定项 |
| --- | --- | --- |
| Phase 1 | `ARCHITECTURE_PHASE=phase1` 切 enforced | C1-C5 + R-I3a/b enforced |
| Phase 2 | 每 PR 消除一条 expired allowlist | allowlist 缩减至 0 |
| Phase 3 | I1/I2/I5/I7 接入（HMAC/idempotency/manifest pin/位置投影） | 完整 17 不变量自动检查 |
