# 代码质量渐进优化设计

## 目标

在不改变 API、持久化结构和核心业务结果的前提下，渐进降低复杂度与类型债务，
消除本次变更范围内的重复实现，并收紧缺失路由与 QUERY 方法两个 fail-closed 边界。

## 现状与决策

- Ruff、Bandit、仓库 `quality` profile 在初始优化后均通过。
- 补充启用 Ruff `C901` 后发现 87 个复杂度超限符号，不适合一次性全面重构。
- `MaterialLocationConsistencyService.diagnose` 复杂度为 12，职责明确，并有资源域契约测试覆盖。
- basedpyright 初始报告 67 个 error；类型收窄后为 0 error。全仓 warning 从 65 降至 45，
  本次触达文件为 0 warning，未扩张到无关模块进行机械清扫。
- canonical payload 在一次投递中被恢复并计算 SHA-256 两次；修复后由请求工厂执行唯一校验。
- 当前唯一 QUERY operation 使用 GET；删除未被需求和测试支撑的 POST transport 分支。

## 设计边界

- 保持 `diagnose` 的公开签名和返回顺序不变。
- 保持五种稳定原因码及其字段内容不变。
- 将索引构建、挂载数量判断和单挂载投影判断拆成私有、单一职责逻辑。
- 持久化原值只在请求工厂恢复一次；payload hash、签名和发送继续使用同一冻结 bytes。
- Provider composition 使用 `WmsOperationContract` / `WmsProviderOperationBinding`，不使用无约束 `Any`。
- QUERY transport 仅接受当前 GET 合同；未来如确需 POST，必须先新增明确 payload 合同和测试。
- plugin logical route 缺失或为空时统一 fail closed。
- 不调整数据库访问、事务、Service/Repository 分层或跨域依赖。
- 不启用全仓 `C901` 门禁；总量降至 86，其余作为后续渐进治理候选。

## 验收标准

- 目标文件通过 Ruff `C901` 检查。
- 资源投影契约测试通过。
- basedpyright 全仓 0 error，本次触达文件 0 warning。
- canonical dispatch 每次投递只执行一次 payload 完整性计算。
- QUERY transport 拒绝推测性的 POST 合同，缺失 plugin logical route 被拒绝。
- Ruff format、Ruff lint、Bandit 和仓库完整 `quality` profile 通过。
- GitNexus 变更检测显示影响范围与 Runtime、Sys、WMS 和 migration 的实际修改相符。

## 风险与回滚

- 主要风险是拆分逻辑时改变问题优先级、Outbox 投递完整性或 WMS 状态事务顺序。
- 通过 RED/GREEN 回归、领域契约测试、完整 pytest 和质量门禁控制风险。
- 本次不涉及迁移、外部响应结构或数据库 schema；可按独立优化项回滚。
