# 手工拣料可选转运货架实施计划

## 已批准行为与边界

首批 `plan_delta` 省略 `target_rack` 的 MANUAL 任务，其转运货架呼入、旋转和移出完全由人工在 WMS 操作；WES 不创建相关动作，不以该架绑定、到位、离场限制来源、料箱和任务完成。指定 `target_rack` 的任务保持现有流程。AUTO 任务仍要求目标架。显式 null 仍拒绝，后续 revision 不允许追加或修改目标架。

保留 `TRANSFER_RACK` 位置声明，但使其绑定可选；指定架的任务仍须具备有效绑定。只在现有 SDK 位置声明增加 `required`（默认 true），用途是区分可选位置，其他插件行为不变。复用 nullable 目标架字段，不增加任务模式、接口或业务表。

## 风险、变更面与所有者

分类：LARGE/HIGH-RISK。主 Agent 独占实施与验证；后续授权包含 Commit、Push、PR 和集成环境部署，尚未授权 Merge。

- wire/OpenAPI：首批可省略目标架；后续 revision 和 null 约束不变。
- 宿主计划接收/激活：按 task_type 校验，冻结无目标架选择，避免构造空货架 Fact，可靠接收、摘要、重放及原请求身份不变。
- 模型/迁移：调整 picking_task_plan_initial_consistent，仅 MANUAL 允许已应用计划且目标架两字段均为空；保留 Evidence 要求和字段成对约束。
- SDK/工作线：WorkLinePositionSlot.required、配置解析、插件声明；绑定存在时仍完整校验类型和位置，不为可选位置跳过验证。
- 手工插件：来源推进解除目标架前置条件；完成与后续离场扫描跳过未管理的目标架。计划 handler 已能处理 target_rack=None，保留已指定架行为。
- 消费者：插件能力 API 序列化、工作线平面/启停、前端生成合同和配置提示按实际影响同步；不扩展为新交互。
- 测试：wire/service/activation、SDK、工作线配置/启动、插件来源/完成/计划 handler；真实 PostgreSQL 验证持久化和约束。
- HEAVY：复用现有精确映射，增加新迁移映射，插件测试由插件单独运行。
- 文档：SRS、WMS 通用出库和人工出库合同；含未指定与已指定请求例、ACK 和业务边界。

## 执行与验收

1. 固定基线、调用点与影响分析；无关 dirty 文件的 SHA-256 已记录在 `/tmp/wes-optional-target-baseline.json`，不得修改。
2. 增加现有测试中的回归场景并确认 RED：无架首批接收/落库、来源推进、任务完成、可选位置启动、AUTO/半空目标拒绝。
3. 最小实现；同步合同、OpenAPI 和必要消费者。无架时不调用任何转运架查询或创建动作；有架行为不变。
4. 聚焦 GREEN：受影响域和插件 FAST、lint、类型/生成合同检查；闭合调用点与残留扫描。
5. 真实临时 PostgreSQL 运行迁移与持久化验证；selector 选中 HEAVY 按快照一次运行，插件有状态测试单独执行。禁止共享开发/服务器数据库。
6. 最终 full Review：两条流程、任务身份/重放、AUTO 边界、约束、测试所有权、生成物、无关 dirty 指纹与 diff 检查；修复发现并刷新失效证据。

## 交付限制

约束迁移必须在生产代码激活前应用；部署后需重建访问该关系的长期进程。本次已应用迁移并重建进程，再通过源码挂载部署，不能只做普通源码热同步。

## 实施结果

已完成实施、审阅、提交与 Push，并部署到集成环境，迁移 head 为 `7a0d19c4e632`。两条任务路径、AUTO 必填边界、null/后续 revision 限制、无目标架来源推进和完成、指定架准入校验均有覆盖。指定架缺少有效 `TRANSFER_RACK` 绑定时返回 `REFERENCE_CONFLICT`；已应用 Evidence 重放不重新解释准入。

- 补充准入修复后的宿主/工作线/SDK 聚焦回归：832 passed。
- 插件 FAST：365 passed。
- QUALITY 完整通过；核心 FAST 为 4082 passed、5 skipped，跳过项不作为集成证据。
- selector 选择 23 个核心 HEAVY 文件：117 passed、0 skipped。
- 插件 PostgreSQL/真实 worker：首次 25 passed、1 failed；失败为既有 SCAN4 夹具遗漏事件 timestamp。补齐合同数据后同一场景定向复测 1 passed，未修改生产逻辑或放宽断言。
- 干净临时 PostgreSQL 完成空库到 head、回退至 `bdf2d676d0a8`、再升级；两个专属验证环境均已清理。
- 新迁移已同步 HEAVY mapping 与固定迁移清单；SRS、通用/人工出库合同、插件 README 和 OpenAPI 已同步。所有原有 dirty 文件的 SHA-256 保持不变。

前端现有绑定编辑支持省略位置，未引入依赖新 required 字段的前端逻辑，因此本次前端源码无需变更。后端 provider 已导出并验证新字段与 schema head；用户同意将前端 canonical 契约冻结延后至后端 PR 合入干净 `develop` 后执行，后续事项已记录于 `TODOS.md`。

集成环境五个长期进程 healthy，640 个源码文件与当前部署快照的 hash 一致，三种准入探针及 `/health`、`/ready` 均通过。补充修复通过源码挂载生效，镜像本身尚未包含该修复。最终只读 Review 通过；外部 Claude Review 因 `403` 未完成，不计为通过证据。

验证日志、JUnit、manifest 与文件指纹位于 `reports/optional-target-verification/`；provider 产物位于 `reports/optional-target-provider/`。
