---
status: Approved
created_at: 2026-06-25
updated_at: 2026-08-03
spec: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
authority: docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
scope: WES 核心设备命令基础能力边界
---

# DeviceCommand 核心边界合同

## 1. 文档定位

本文只定义 WES 核心共享的设备命令可靠性边界，不定义任何厂商命令集、具体工作线动作或业务 Payload。

能力所有权严格分为三层：

| 层级 | 所有内容 | 不得包含 |
| --- | --- | --- |
| WES 核心 | 命令持久化、幂等身份、目标设备、deadline、ACK/CALLBACK 证据、通用状态与诊断 | 厂商 `task_type`、厂商字段、具体工作线规则 |
| 厂商 Adapter | 厂商 HTTP DTO、认证、命令名、Payload、ACK/Result 映射与厂商合同测试 | 工作线业务判定、数据库写入、通用流程引擎 |
| WorkLine 插件 | 何时创建命令、当前业务对象、逻辑目标和下一步 Decision | HTTP、Repository、重试、Outbox、设备安全互锁 |

厂商 Adapter 和 WorkLine 插件属于二次开发交付。核心不得要求供应商适配 WES 自有指令集，也不得把具体业务
命令反向固化为核心枚举。

## 2. 核心命令闭环

核心只保证以下不变量：

1. 下发前确认目标设备当前可接纳命令。
2. 在任何外部调用前持久化 `DeviceCommand` 及其幂等、关联和 deadline 事实。
3. ECS 同步 ACK 只表示接纳，不表示物理动作完成。
4. 只有匹配当前命令的最终 CALLBACK 才能推进物理位置和具体执行对象。
5. 重复 CALLBACK 必须复用首次结果；同一幂等身份、不同 Payload 必须拒绝并保留冲突证据。
6. 未知、乱序或无法关联的结果只保存 evidence 和 diagnostic，不推进当前对象。

WES 不拆解厂商长命令，不解释 ECS 内部步骤，也不实现设备间安全互锁。

## 3. 内部模型边界

最终 `DeviceCommand` 只保存执行可靠性所需的内部事实：

- 稳定命令身份、目标设备和当前具体执行对象关联；
- 厂商 Adapter 已验证的命令 Payload 或其不可变快照；
- payload digest、deadline、下发尝试和最终结果证据；
- `PENDING / DISPATCHED / ACKNOWLEDGED / SUCCEEDED / FAILED / TIMED_OUT` 等通用生命周期；
- correlation、trace 和 diagnostic 信息。

具体字段名以最终模型为准；不得为当前旧模型保留别名、转换层或兼容字段。

## 4. 厂商合同边界

每个厂商 Adapter 根据实际接口文档独立提供：

- 命令请求、ACK、CALLBACK DTO；
- 厂商命令类型和 Payload 校验；
- 认证、Endpoint、timeout 与基础错误映射；
- 命令请求到厂商 wire payload 的显式映射；
- 厂商合同测试和样例 fixture。

`docs/hardware/` 原样保留厂商提供的协议与联调资料。版本差异由 Adapter 合同、映射和测试显式处理，不通过
改写厂商原文消除，也不能把厂商资料提升为 WES 核心架构真源。

核心只依赖 Adapter 暴露的窄端口，不读取厂商 DTO，也不验证具体命令值。供应商接口变化只能修改对应 Adapter
及其测试，不能扩张核心 `DeviceCommand`。

## 5. WorkLine 业务边界

具体工作线插件决定：

- 哪个业务事件允许创建命令；
- 命令关联哪个 `MaterialExecution`、`BinExecution` 或其他具体对象；
- 厂商命令需要哪些逻辑业务参数；
- CALLBACK 后返回下一条命令、结束、NG 或对象级暂停中的哪一个封闭 Decision。

粗分机、自动分拣、人工分拣、满箱交换等流程不得写入核心合同或核心测试。

## 6. 禁止能力

WES 核心、Adapter 和插件都不得建立以下软件控制字段或抽象：

- PLC 点位、物理坐标、关节角度、速度曲线；
- 安全回路、急停复位或运动控制；
- WES 自有的通用厂商命令枚举；
- 运行时工作流 DSL、动态插件发现或通用命令解释器；
- 为旧 `task_type`、旧 Payload 或旧回调字段保留的兼容入口。

这些物理控制和安全事实由 ECS/现场安全系统拥有，WES 只消费其状态、ACK、CALLBACK 和事件证据。

## 7. 测试所有权

| 测试范围 | 唯一所有者 |
| --- | --- |
| `DeviceCommand` 通用生命周期、幂等、关联、deadline、证据和禁止硬件控制字段 | 核心 `tests/` |
| HTTP/认证/大小限制等共享传输不变量 | 核心 `tests/` |
| 具体厂商命令名、DTO、Payload、错误码和样例 | 对应厂商 Adapter 包 |
| 具体工作线何时创建命令及 CALLBACK 后业务推进 | 对应 WorkLine 插件包 |

核心测试不得使用具体厂商或工作线场景证明基础能力；Adapter/插件测试也不得替代核心可靠性测试。
