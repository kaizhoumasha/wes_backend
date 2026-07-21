---
title: 北向公共能力双层提炼设计
status: 已确认，待计划
created_at: 2026-07-21
parent: docs/architecture/target-state-contract.md
---

# 北向公共能力双层提炼设计

## 1. 目标

将 WorkLine Plugin 与 WMS 的交互收敛为两层可复用能力：

1. `wms.*` 只承载北向协议调用、证据、超时、幂等与标准错误映射。
2. `material_flow.*` 承载可跨工作线复用的库存准入、库存事务规则和履约需求构造。

WorkLine Plugin 只保留本线配置、路由、状态机和编排决策；不得依赖 WMS client、DTO、Provider 实现或直接组织北向协议调用。

首个迁移对象为粗分机现有的 `wms.rough_sorter_inventory_admission@v1`。交付范围覆盖查询、库存事务和履约三个能力族，但按阶段交付。

## 2. 目标边界

```text
workline_plugins/<line>
  本线 typed config、拓扑、状态机、路由、动作选择
          ↓
runtime/capabilities/material_flow
  跨工作线业务规则与领域请求构造（无 I/O）
          ↓
runtime/system_capabilities/wms
  通用 WMS QUERY/EFFECT、evidence、幂等与失败映射
          ↓
wms_integration/ports
  ACL、Provider 和实际 WMS 适配
```

目录职责：

| 目录 | 责任 | 禁止事项 |
| --- | --- | --- |
| `src/app/runtime/workline_plugins/<line>/` | 工作线专属配置、事件路由、状态机和业务编排 | import WMS 实现、HTTP client 或 DTO；保存可复用库存规则 |
| `src/app/runtime/capabilities/material_flow/` | 库存准入、库存事务规则、履约需求构造等纯业务能力 | 调用 WMS Port、写 RuntimeIntentLog 或管理外部重试 |
| `src/app/runtime/system_capabilities/wms/` | WMS 协议能力和标准运行时语义 | 携带 `rough_sorter` 等工作线专属命名、设备角色或 Plugin binding 字段 |
| `src/app/wms_integration/ports/` | 稳定领域级 Port 合同 | 向 runtime 暴露 Provider DTO 或 client 实现 |

`WorkLine.config` 继续保存每条工作线的实例值；其 schema、设备角色、拓扑和 capability allowlist 仍由对应 Plugin Definition 作者态声明。激活后的配置、Provider profile、Port 要求和设备身份继续冻结在 immutable binding。

## 3. 能力与合同

### 3.1 通用北向协议能力

| 能力 | 类型 | 责任 |
| --- | --- | --- |
| `wms.inventory_query@v1` | QUERY | 接收通用查询条件，返回权威库存快照与 evidence；只读、可短 TTL 缓存。 |
| `wms.inventory_transaction@v1` | EFFECT | 接收通用库存事务请求，固定幂等键、回执、超时与错误分类。 |
| `wms.fulfillment@v1` | EFFECT | 提交/查询外部履约，统一异步回调、evidence、超时和 reconciliation 入口。 |

这三项只依赖对应的 `Wms*Port`。运行态 metadata（binding、Provider profile、调用者、幂等键）由 Runtime capability envelope 和 evidence 保存，不进入业务请求 schema。

### 3.2 material-flow 业务能力

`material_flow.*` 的输入只包含领域对象和通用 WMS 输出，输出为：

- `ADMIT` / `REJECT` / `HOLD` 等业务判定；或
- 标准化库存事务请求、履约需求。

这些能力必须是无 I/O 的纯计算。技术失败不得被重写为业务拒绝：

| 外部结果 | 通用能力输出 | 业务层行为 |
| --- | --- | --- |
| 超时、不可用 | `RetryableFailure` | 保留重试/等待语义，不产生库存拒绝 |
| 契约错误 | `ContractViolation` | fail closed，进入 Hold/诊断 |
| WMS 明确拒绝 | `BusinessReject` | material-flow 映射为业务 `REJECT` |
| 成功快照/回执 | `Success` | material-flow 进行可复用业务判定 |

Plugin 根据业务结果选择本线动作；例如粗分机可将 `REJECT` 映射为转 NG，将技术失败映射为 Hold。

## 4. 分期迁移

### 阶段一：库存查询与准入

- 建立 `wms.inventory_query@v1` 的通用合同、Port 适配和 evidence。
- 提取 `material_flow.inventory_admission` 纯规则。
- 粗分机改为“通用查询 → 纯准入判定 → 本线动作”。
- 删除 `wms.rough_sorter_inventory_admission@v1`，不保留兼容别名。

### 阶段二：库存事务

- 收敛通用库存事务请求、幂等键、外部回执、超时与恢复语义。
- material-flow 只生成事务业务请求；Plugin 只声明何时提出该请求。
- 每个新增事务能力必须有实际工作线或 material-flow 消费者，不建立空壳预留能力。

### 阶段三：履约

- 收敛履约需求、提交/查询、异步回调、超时、evidence 与 reconciliation。
- 保持 WMS 为履约及库存的外部权威；WES 不直连 RCS/AGV/CTU。
- 每个新增履约能力必须由实际工作线流程驱动并具有回调恢复合同。

## 5. 验收与测试

每个阶段都必须通过：

1. WMS capability 的 Port 契约测试：成功、明确拒绝、超时、不可用、无效响应。
2. material-flow 的表驱动纯规则测试：同一输入得到稳定判定，不依赖数据库或 Provider。
3. Plugin → RuntimeIntent 集成测试：Plugin 仅调用声明的 capability，且不 import WMS 实现/DTO。
4. QUERY evidence 或 EFFECT `RuntimeIntentLog` 的审计、重放和幂等测试。
5. `architecture-guardrails.sh`、生成索引一致性和对应运行态回归测试。

事务和履约阶段额外覆盖重复提交、重复回调、超时、外部拒绝、失败恢复和 reconciliation 入口。

## 6. 非目标

- 不把 WMS 主数据、库存、单据复制为 WES 权威数据。
- 不新增通用 DSL 或规则引擎。
- 不以兼容旧粗分机 capability 为目标；切换完成后删除旧专用 capability。
- 不在没有真实消费者时预建事务或履约能力。

## 7. 风险与控制

| 风险 | 控制 |
| --- | --- |
| 通用请求重新混入工作线字段 | schema 不得含 Plugin key、设备角色或 binding；架构守卫阻断。 |
| 业务规则回流 WMS capability | material-flow 纯规则测试和目录职责审查。 |
| 技术失败被误判为业务拒绝 | 固定 outcome 映射与回归测试。 |
| 切换改变粗分机业务结果 | 先以现有 characterization/contract 用例做等价验证，再删除旧能力。 |
| 泛化过度 | 每阶段要求真实消费者；无消费者则停在设计，不创建空壳。 |
