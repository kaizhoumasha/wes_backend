# WES 稳定性与中断恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让工程师从持久化事实快速定位中断，安全继续原执行，并能验证重启与发布后的恢复结果。

**Architecture:** 基础层保留 Transport、DeviceCommand、WmsConfirmation、InboundEvidence 的独立生命周期、幂等身份与现有扫描器。诊断页面读取这些基础事实；具体工作线决定业务是否继续。部署、备份与恢复演练由运维入口拥有，不建立通用恢复引擎或第二套事实账本。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy/SQLModel、PostgreSQL、Redis、Celery、Vue 3、TypeScript、Docker Compose、Jenkins。

**Spec:** 本文“需求与边界”；`docs/architecture/SRS.md`；`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`；用户 2026-09-09 稳定性复盘及本轮红线。

## Global Constraints

- 基础能力与业务能力严格区分；基础能力可独立部署、运行、测试，业务能力依赖基础能力。独立不等于拆成微服务，也不等于删除通用数据库结构。
- 未发布系统直接替换目标合同；不增加 v2、别名、shim、双路径或旧数据转换。现有记录恢复与旧版本兼容是两回事。
- 遵守 DRY、KISS、SOLID、YAGNI；复用已有 Service、Repository、HTTP、claim、审计、SSE、配置与发布入口。
- API → Service → Repository → Database；核心与 SDK 不导入具体插件，不根据 KT16、510056、扫码设备数量判断基础能力。
- 结果未知的物理动作保留原身份、证据、位置不确定性和资源围栏。超时、重启、页面按钮、诊断日志、健康检查不能证明物理完成。
- 开发/测试数据可清理，不要求兼容迁移；真实联调中未闭合的物理动作不能靠清库恢复。本轮不清理任何运行数据。
- 纯文档不编写测试代码；后续运行时改动按风险执行 TDD。Commit、Push、PR、Merge、Deploy 分别授权。
- 初级开发人员应能按单一入口理解每个领域；禁止动态恢复 registry、通用命令总线、全局常量中心、全链路新事件表。
- `docs/hardware/` 保留。失去当前职责的过程文档移到 `../archive_docs/wes_backend/`，不留副本或转发页。

## 1. 冻结基线与证据等级

本次只读代码核查基线：`develop@9e2104713ecd181e7eefa5bea133efcaf11a8a21`，开始时工作区干净。未重新连接现场、未运行生产测试、未复现真实搬运。用户已批准迁移到实施 worktree 并按切片执行；本计划状态为 **APPROVED — IMPLEMENTING**；不是实施完成或上线结论。

| 发现 | 当前证据 | 计划处置 |
| --- | --- | --- |
| 历史回库超时后迟到回调缺 `arrival_face`，被入口拒绝 | 9 月 8 日对话保存的现场收据与任务查询 | 保留完整终态合同；展示拒绝原因和待补事实，不放宽物理约束 |
| 历史 10/30 秒节拍引起空等 | 保存的数据库时间线；当前 `src/core/transaction_wakeup.py`、`src/celery_app/config.py` 已有主动唤醒与兜底 | 不重复实现；A4 验证消息丢失与进程退出 |
| 结果期限代码与合同不一致 | `src/app/transport/service.py:98` 为 20 分钟；`docs/contracts/transport-fulfillment-contract.md:596` 为 10 分钟 | A1 统一配置和合同；默认保留当前代码 20 分钟作为提案，不代表现场耗时已达标 |
| Transport 详情不暴露截止时间与发布版本 | `TransportTaskSnapshot`、`TransportTaskResponse` 只有结果与 latest_evidence | A2 扩展原详情，不另建任务模型 |
| WMS 近期诊断不是可靠恢复源 | `src/app/wms_diagnostics/repository.py` 为有保留期的 Redis Stream；Service 失败允许降级 | A3 直接查询 PostgreSQL 可靠对象；Redis 丢失仍可定位 |
| 已有恢复动作语义不同 | `requeue_reconciling` 保留身份；`retry_wms_action` 确认原 prepare 作废后生成新身份；设备 blocker/reprocess 已由无阻塞设计退役 | B2 仅保留事实诊断，不统一成“重试全部”或重新暴露人工续行 |
| 热补丁与运行进程基线曾不一致 | 近期发布对话；当前已有 hot-sync/probe、worker/Beat 探针 | C1 核验真实加载结果与制品一致性，不重新开发部署工具 |
| 备份已有独立计划，未证明异机恢复闭环 | 现存 `2026-08-18-wes-onsite-data-recovery.md` 与生命周期索引 | 保留唯一 owner，C3 衔接；不把历史计划当已部署能力 |

**根因假设：** 近期中断主要涉及协议接纳、正常长耗时与阈值、执行结果到下游消费、运行制品一致性四个边界；排障慢的已确认代码原因是可见信息分散、可靠状态暴露不足。当前扫描器存在，不据此断言其所有崩溃窗口都安全；A4 的切点测试用于确认或否定这一假设。

**调查结论的限制：** “已有方法”不代表所有业务入口已接入；“历史测试通过”不代表当前环境已验证。计划中的行为改变必须在实施快照重新建立对应证据。

## 2. 需求与边界

| 编号 | 用户能观察到的结果 | 唯一实施 owner |
| --- | --- | --- |
| R1 | 看到原任务、最后确认事实、实际 deadline、待处理证据、结果是否已发布 | A2、A3；B1 展示 |
| R2 | 区分证据重处理、原请求重试、作废后重新求值、等待外部事实 | B2 复用既有领域动作；已复现审计/并发缺口按 B2 接管流程由唯一领域 owner 修复，与 A4 去重 |
| R3 | 依赖恢复后，已落账未完成工作可由现有扫描继续；未知物理动作不重复发送 | A4 |
| R4 | 调整搬运等待窗口有唯一入口，已冻结 deadline 不被回调或配置刷新 | A1 实现；C1 实施 owner 验证进程配置与冻结期限，现场运行负责人另行确认路线耗时适配 |
| R5 | 发布目标、实际代码、进程加载版本、schema 和验证结果可核对 | C1、C2 |
| R6 | 服务器损坏后有异机恢复源及真实还原证据 | 既有备份计划，C3 只负责接入与验收 |
| R7 | 零已安装业务插件时基础查询/可靠处理正常；业务流程在插件包独立验收 | A4、B3 |
| R8 | 新人按一份运行手册定位和恢复，不执行临时 SQL 修状态 | B2、C3 的各自章节 |
| R9 | 无效旧路径与被取代过程文档退出；本轮不扩大到全仓清理 | 各任务最终残留扫描；C2 承接旧门禁验收 |

不纳入：新工作流引擎、自动物理补偿、WMS 状态查询新 operation、跨插件版本继续执行、集群/HA/Sentinel、提前建设 WAL/PITR、大型监控平台、全业务插件重写。需新增外部 operation 或改变物理退出事实时单独冻结合同。

## 3. 可独立验收的子计划

| 顺序 | 计划 | 独立交付结果 |
| --- | --- | --- |
| 1 | [A：基础可靠执行与事实查询](2026-09-09-stability-recovery-foundation.md) | 不安装业务插件也可验证的 deadline、可靠状态查询、崩溃恢复证据 |
| 2 | [B：诊断消费与领域恢复入口](2026-09-09-stability-recovery-consumers.md) | 现有页面准确呈现阻断事实，复用已有操作，业务验收与基础验收分开 |
| 并行交付单元 | [C：发布一致性与恢复演练](2026-09-09-stability-recovery-operations.md) | 发布可核验；普通 TEST FULL 验收及备份演练有明确 owner |

A1、A2、A3、A4 各自可评审；共享 Service、生成物、测试支撑只有一个写入 owner。B1 依赖 A2/A3 合同冻结；B2 可先核验现有操作；C 可独立于前端交付，但不得并行重建共享现场服务。本轮不启动子 Agent；执行方式由用户选择。

## 4. 恢复决策表

| 已确认状态 | 允许行为 | 禁止行为 |
| --- | --- | --- |
| 可靠义务已提交、消息未发出 | 现有扫描器领取原工作项 | 为漏唤醒另建任务身份 |
| 合法 Evidence 已保存、未应用 | 既有处理器重新处理同一 Evidence | 从诊断截断文本构造“成功证据” |
| 原结果已形成、尚未发布 | 原 publisher 按单调版本继续发布 | 新增另一个 outcome 或直接改业务步骤 |
| 消费者已处理该版本 | 幂等无操作 | 重复创建设备/搬运动作 |
| 已发送、对方可能接收、结果未知 | 冻结并等待匹配终态/合同允许的对账 | 超时自动重发、释放锁、清库 |
| 对方明确未接收且原合同允许重试 | 原领域动作、原身份、原正文、审计 | 将操作员勾选框当成 WMS 已完成事实 |
| WMS 明确作废原 prepare 且不再产出旧计划 | 当前 prepare 专属替换入口生成新身份，保留旧义务审计 | 用相同 operation_id 修改正文 |
| ECS 急停或原设备命令尚未闭合 | ECS 处理急停、复位与恢复；WES 保留原命令身份等待正常结果，且不阻断独立新命令 | 接收急停 Event、伪造 DEVICE_IDLE 或人工重处理原命令 |

表格用于评审和操作说明，不实现为共享状态机。每个领域保留其既有合同与判断。

## 5. 验收口径

- **正确性硬标准：** 原身份/摘要不漂移；已确认的物理动作不因恢复重复执行；证据不被覆盖；无权威终态不解除物理围栏。使用协议幂等允许的原请求重试次数与真实物理执行次数分别统计，不混为“HTTP 只发送一次”。
- **诊断目标：** 独立工程师面对 A4/B2 固定案例，使用页面与 runbook 在 2 分钟内指出阻断阶段、原身份和责任方；这是提案验收目标，不是现有实测成绩。
- **恢复目标：** 从依赖就绪、必要权威事实齐全、相应 lease 可重新领取开始计时；记录扫描周期、排队量、处理耗时及 p50/p95/max。首轮参考目标为单任务 60 秒内开始安全处理；不能通过缩短物理 deadline、lease 或屏蔽断言达标。
- **外部等待：** 设备运动、WMS 人工确认、缺少位置事实单独计时；不得记为 WES 已完成，也不掩盖其总停机影响。
- **持久性目标：** 已提交 PostgreSQL 事实在进程中断演练中不丢失；整机灾难恢复沿用既有计划 RPO ≤ 1 小时、RTO ≤ 2 小时，不能宣称整机零丢失。
- **基础与业务双验收：** 基础测试使用空插件部署和无业务规则的 fake；业务测试由具体插件拥有。没有已批准业务切片时明确记录未验收，不把自动联调当正式插件验收。

## 6. 执行前与完成门禁

以下已完成项仅覆盖本次后端 A1–A4 与 B2/B3 文档交付；后续切片继续按相同门禁执行。

- [x] 冻结实施 HEAD、dirty 文件指纹、精确目标符号、全部调用者、测试/fixture owner、HEAVY mapping、生成物与归档清单。
- [x] 首个生产补丁前执行 GitNexus upstream impact；工具不可用则记录错误并用精确调用点降级。HIGH/CRITICAL 范围一次说明并取得授权，不在同一清单重复暂停。
- [x] 每个运行时切片建立可观察行为 RED → 最小实现 → GREEN；已有能力仅新增故障验证时，不为凑 RED 破坏正确代码。
- [x] 先闭合直接/间接测试与映射，再做唯一主 Review、必要 QUALITY、selector HEAVY；同一有效 fingerprint 不重复完整门禁。
- [x] 对新增模块精确 mapping；测试支撑变更纳入所有直接消费者，不凭文件名猜 HEAVY。纯文档 mapping 为不适用。
- [ ] 生成物跟随后端 canonical，前端不手改 OpenAPI 元数据绕过门禁；仅前端交互执行浏览器 QA。
- [x] 删除本轮替换后无消费者的定义/导出/helper，不保留 renamed-old 文件或兼容 wrapper。
- [ ] 验证完成后再分别处理 Commit、Push、Merge、Deploy；本计划不自动授权这些动作。

## 7. 文档生命周期与本轮调查报告

`2026-08-26-release-operational-readiness.md` 的已实现过程由当前 `docs/devops/prod-release-deploy.md` 承接，剩余普通 TEST FULL 验收完整移交 C2；本轮将原文件完整移出项目并更新引用。其他备份、runtime-hardening、WMS 诊断计划仍有未闭合职责，本轮不凭日期删除，亦不复制其任务。

调查状态：**DONE_WITH_CONCERNS（只读规划调查）**。已核验符号、当前合同与相关历史；发现 deadline 文档漂移和诊断暴露缺口。修复、回归测试与现场复现本轮均未执行，分别由 A/B/C 验收。未持久化新的跨会话记忆。

计划自审：R1–R9 均有 owner；A3 的新类型/接口由 A3 定义；B 不构造恢复业务规则；C 不拥有备份实现；当前任务不编写 pytest、不创建 Commit。

## 实施进度与已确认合同修正

用户已批准本计划并要求迁入 worktree 按序实施。实施分支为 `codex/stability-recovery`，
基线 `9e2104713ecd181e7eefa5bea133efcaf11a8a21`。8 项计划及引用差异已迁入，主工作区其他变更保留。
没有 Commit、Push、Merge 或 Deploy。

| 切片 | 当前证据 | 未完成项 |
| --- | --- | --- |
| A1 | 唯一配置入口及冻结期限接线完成；包含在 468 项通过的聚焦回归中 | 长期进程配置与现场路线适配 |
| A2/A3 | 三项独立权限已确认并实现；持久查询、目录和真实新库 bootstrap 已通过；provider 已导出到 reports | 前端 canonical 冻结与交付到 develop |
| A4 | 4 项独占 PostgreSQL/Redis/真实 worker 中断场景通过；原扫描重新领取约 30 秒 | 其他领域不可据此宣称完整崩溃覆盖；现场待验收 |
| B1 | 已确认前端原冻结入口必须读取干净后端 develop | 后端交付到 develop 后再生成，禁止手改合同绕过 |
| B2 | 原后端准入/审计/身份约束复用；单一执行恢复手册已建立；原前端 prepare 文案已明示作废与新身份 | 前端交互及独立工程师手册演练 |
| B3 | 原 Transport 联调消费者 FAST 与 PostgreSQL 重启恢复通过 | 供应商与正式插件业务独立验收 |
| C1/C2 | 现有同步后受控重启、失败退出与发布门禁共 64 项聚焦测试通过；不新增部署机制 | 独立 Compose 加载实验、长期进程配置、普通 TEST FULL；DEPLOY NOT RUN |
| C3 | 已核验备份脚本与 runbook 尚未落地，继续由原备份计划独立拥有 | 异机输入、备份资产及真实 restore；不能宣称灾备可用 |

完整 QUALITY 已通过（默认 FAST 3577 passed / 5 skipped；skip 不是行为通过证据）。
另行执行的清理矩阵审计发现基线 CSV 仍引用旧 WorkLine 符号；本轮没有修改相关生产文件或生成器，
该项作为独立基线问题保留，不能用 QUALITY 通过替代它。新增生产文件 Ruff 与类型检查无错误。
恢复指南见 [execution-recovery](../../devops/execution-recovery.md)。验证快照及最新报告保存在本 worktree 的 `reports/`。

### 已闭合：三条查询使用独立只读权限

原 scanner 要求同一权限码的 method/path 唯一，按旧计划复用 read 权限会使新库 bootstrap 失败。
用户已确认并落实以下修正，未修改 scanner、角色策略或 schema：

| 接口 | 独立只读权限 |
| --- | --- |
| `/api/v1/transport/callback-receipts` | `ops:transport-callback-receipt:read` |
| `/api/v1/wms-diagnostics/confirmations` | `ops:wms-confirmation:read` |
| `/api/v1/wms-diagnostics/evidences` | `ops:wms-evidence:read` |

实际完整目录为 195 项，移除本轮三条 route 后为 189 项；旧 bootstrap 测试的 160 项已经过期。
已核对内置 GET 只读策略和精确权限集合，并通过真实数据库 bootstrap；自定义角色继续由既有管理入口授权。
历史 124 passed / 1 failed（权限冲突）和 138 passed / 2 failed（重复回调误期望 202）均已定位，
不能作为最终绿色证据。后者改为按当前合同分别断言 `202/RECEIVED` 与 `200/DUPLICATE`，4 项中断复测通过。

### 本轮收口证据

Claude 原 Reviewer 完成旧意见闭环与 fresh Review，结论为代码/测试无剩余阻断项。
评审后完整 QUALITY 通过；最终 selector 的 33 个文件由主集 140 项和共享 harness 补充集 26 项完整覆盖，
合计 **166 passed / 0 failed / 0 skipped**，两个独占 Compose 项目均已清理。
补充映射没有改变生产或测试行为；追加 selector 合同 213 项通过，未重复已有效的主集。
当前 source/test/config 指纹、环境指纹、manifest 和具体报告路径统一在 `reports/stability-recovery-progress.json`。

状态为后端实现、评审及上述验证完成，已获 ship 提交、推送和 PR 合入 develop 授权；交付动作以 Git/PR 记录为准。
本次不代表全计划、部署、现场或业务验收完成。
B1 下一步必须先按项目交付规则使后端候选进入干净 develop，然后用原冻结/生成命令继续前端。
