---
title: WMS operation 稳定身份与合同边界
status: accepted
date: 2026-07-21
supersedes: null
---

# ADR：WMS operation 稳定身份与合同边界

## 背景

当前北向调用同时使用 `Port.method` 字符串、`effect_contracts` 字典、工作线专用 capability identity 和
Provider profile 中的字符串 allowlist。相同业务 operation 因调用位置不同而拥有多个名字，生成索引、测试、
文档和删除门禁无法围绕同一身份闭合。系统尚未发布，因此不保留兼容入口或旧数据迁移路径。

## 决策

operation identity 是跨 Plugin、Runtime、System Capability、catalog、Provider compatibility、evidence 与运维材料的
稳定身份。identity 采用 `<domain>.<bounded-operation>@<contract-version>`；它表达领域语义，不携带 endpoint、HTTP method、
credential、工作线、环境或 Provider 名称。handler、Port 方法与 Provider endpoint 均可演进，但不得借此创建第二个 operation identity。

本次清点锁定四个真实消费者；没有真实消费者就不预建空壳：

| Operation identity | 模式 | 现存遗留身份 | 后续合同所有者 | 删除门禁 |
| --- | --- | --- | --- | --- |
| `wms.inventory.query_inventory@v1` | QUERY | `wms.rough_sorter_inventory_admission@v1` 与 `WmsInventoryQueryPort.query_inventory` | inventory query typed contract | T7 通用查询与纯 policy 接线后删除专用 capability |
| `wms.inventory.confirm_inbound@v1` | EFFECT | `WmsInventoryTransactionPort.confirm_inbound` | inventory transaction typed contract | T9 typed EFFECT 与 reconciliation 闭合后删除字符串路径 |
| `wms.fulfillment.notify_pkg_binding@v1` | EFFECT | `WmsFulfillmentPort.notify_pkg_binding` | fulfillment typed contract | T10 callback reducer 闭合后删除字符串路径 |
| `wms.fulfillment.full_box_exchange@v1` | EFFECT | `WmsFulfillmentPort.full_box_exchange` | fulfillment typed contract | T12 独立迁移任务闭合后删除字符串路径 |

## 边界

### typed contract

每个 operation 只有一套严格的领域 request/result，由 `src/app/wms_integration/ports/` 拥有。System Capability 直接引用它，
不复制 schema；material-flow 只保留纯 policy 或 typed request builder；Plugin 不持有 Provider DTO。T2 才创建这些合同，
本 ADR 不创建空目录、模型、handler 或 dispatch 空壳。

### catalog 与生成物

author-time 单一真源由 typed operation contract、System Capability Definition 与 Provider profile 声明组成。确定性生成器据此
派生 capability catalog、Provider compatibility report、digest 与删除门禁输入。生成索引不是第二真源，禁止手工维护 identity，
也不新增 YAML/JSON 运行时 DSL。

### Provider 资料

Provider profile 只声明 provider/version/environment 身份以及对 operation identity 的兼容资料；endpoint、预算、retry 与出站认证
属于相应 operation contract/binding，credential 仅保存版本化 reference。Provider DTO 和 ACL 映射不进入 operation identity，
fixture 与 required cases 只属于测试/构建期 manifest。

### 删除门禁

`docs/architecture/northbound-wms-operation-inventory.csv` 是 T1 的可执行清单。每个条目只有在目标 typed 路径生效、对应测试完成
`KEEP/REWRITE/DELETE` 处置、旧 identity 与字符串字段扫描为零、生成物零差异、指标和文档完成后才可关闭。T12 必须按清单中
真实 operation 展开任务；不得以首批三个 operation 为范围豁免，也不得在门禁外延期清理。

## 零兼容约束

- 不保留兼容 alias、旧 schema、双写、双读、永久 shadow 路由或多版本 dispatcher。
- 不为旧测试保留生产路径；有业务价值的场景改写到新合同，无价值的专用实现测试直接删除。
- 不迁移开发或测试旧数据；目标 schema 变化时允许清理重建。
- 不扩张 `SystemCapabilityDefinition`，不修改 `WmsTypedPortService`，不在 T1 提前实现 T2 及以后功能。

## 结果

后续实现可用同一 operation identity 连接合同、catalog、Provider compatibility、evidence、指标、文档和删除门禁；代价是现有
字符串调用会发生一次破坏性迁移。该代价符合未发布系统的单一目标合同原则，也避免长期维护兼容分支。
