# 自动拣料插件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans` 按切片执行，使用复选框记录进度。推荐主 Agent 顺序实施；本计划不授权自动派生 Subagent、Commit、Push、Merge 或 Deploy。

**Goal:** 先完整对齐已确认的 WMS DTO、SDK、typed converter、OpenAPI 与测试，再交付独立 `automatic-picking` 插件的正常业务闭环及其持久恢复。

**Architecture:** 宿主提供 typed operation、可靠接收、执行、派发和重放，自动插件拥有当前业务授权、工作轮次、Cell 循环、目标准备及完成判断。复用既有插件端口和扫描入口，不抽取 `bin-line-common`，不导入人工插件业务实现。

**Tech Stack:** Python 3.13、现有 Pydantic/SQLModel/SQLAlchemy、PostgreSQL、Celery、pytest、`uv`；不增加依赖来替代既有基础能力。

**Spec:** [修订后的自动拣料插件设计](../specs/2026-09-22-automatic-picking-plugin-system-design.md)，SHA-256：`00fefedde6ed530dc2435a99b36845704df98c081c7a40506b9f3232b5777189`。

**状态与基线:** 2026-10-17，原基线 `develop / d38cced4201e0a09e46ceff5fe7b9f84dce20f13`。任务 1–3 已提交为 `e558fe0b`；任务 4、任务 5 已确认路径及五项 Review 修订提交为 `2da3990c`；任务 5A 验收及计划更新已在 `codex/automatic-picking-workflow` 提交为 `26deca33`。任务 6A 已提交为 `97c99c9`，6B 持久化与离线恢复验收已提交为 `197c568d`。本轮实施 6C：精确关联已给定前提下的单盘绑定、typed Fact/Handler、首次 decide 与 ACCEPT 离线接线；不提交、不部署、不推进真实取盘。确定 WAIT 的持久到期续问留给 6D，G2/G3/G5 的生产门槛及 G6 首批期限保持未决。
进入实施前核对设计指纹；授权设计修订后同步更新计划对应切片，不能在执行中自行解释合同。

## 全局约束

- 所有实现切片均按 `LARGE/HIGH-RISK` 管理：RED → 最小实现 → GREEN；本次写计划不创建代码或 pytest。
- `API → Service → Repository → Database`；纯 Decision 只依赖 SDK、无 I/O；插件应用层经基础 Service 创建可靠对象。
- 公共能力各实现一次；operation 不重建 HTTP、inbox/outbox、领取、重试或队列。宿主及 SDK 不导入具体插件。
- 技术重试保留原身份、时间戳与冻结正文；确定 WAIT 才按业务决定创建新身份。ACK、ACCEPTED 和超时不替代物理终态。
- 只根据当前有效 Requirement 与权威 Fact 创建动作；原成员取消、父任务完成、已闭合历史和新轮次分别处理。
- 两臂命令可独立下发，实际交接与互锁由 ECS/PLC 裁决；不加平台释放事件、扫码台锁、设备占槽或全线 MaterialExecution FIFO。
- 不抽取共享人工业务包，不复制人工状态机，不建兼容别名、旧字段双路径、动态 registry 或成功占位消费者。
- 新表及迁移随真实业务切片增加，复用 MaterialExecution、Command、Transport、Evidence、Confirmation；不预建所有模型。
- 所有命令使用 `rtk`，项目命令使用 `uv run`；测试所有权遵守 [tests/README.md](../../../tests/README.md)。

## Review Focus

1. 回架候选查询为空但原请求未知，不能触发离场；独立架不受统一阻断。测试 owner：任务 8。
2. M1 报告先确认、M2 尚未创建时重启，当前授权仍由 Work 恢复且至多消费一次。测试 owner：任务 6、7。
3. PREPARING/revision 0 必须可达首批超期确认；计划先到或旧响应迟到不得错误完成任务。测试 owner：任务 5。
4. 父任务完成、没有新设备事件时，drain WAIT 到期仍能以新身份重问；新候选不加入旧 READY reservation。测试 owner：任务 9。
5. 共享类型传播不能遗漏：旧出库字段退出，新 revision 不被旧结果关闭；入库独立合同不得被误改。测试 owner：任务 1、2、10。
6. 尚无 MaterialExecution 时，SCAN2 的 work_plan 可经固定 typed 端口创建、读取和恢复；不得提前虚构单盘身份。基础接线 owner：任务 3A；业务 owner：任务 5。
7. 新建 drain 或 WAIT 到期重问前，同一 WorkLine 事务优先准备下一任务；原未决请求及 READY 不被覆盖。测试 owner：任务 9。

## 前置事实与未决项

协议真源为 [SRS](../../architecture/SRS.md)、[出库主合同](../../contracts/wms-outbound-picking-task-integration-requirements.md)、
[WMS 联合确认](../../integration/wms-joint-confirmation-automatic-picking.md)和[机械臂附录](../../integration/device-annex-automatic-picking-arms.md)。
以下限制只作用于相应字段或执行分支；不能用 Mock、放宽断言或临时字段绕过，也不能据此阻塞无关已确认部分。

| 编号 | 已明确 / 尚未明确 | 影响与解除证据 |
| --- | --- | --- |
| G1 字段结构与边界 | 2026-10-16 已确认 int cell_index=1–7、int bin_type=2/3/6、int reel_layer/目标层列、float 厚度/直径、barcode str ≤1000；编码对应和设备物理边界仍待设备映射 | 任务 2 按已确认类型/枚举/长度完成严格 DTO 与正反例；编码对应和设备值域只阻塞使用它们的设备路径 |
| G2 扫码关联 | O1/O2 的事件设备及来源 Command 精确关联、O6 business_key 必要性未定 | 限制任务 6 的生产扫码/命令接线；须有获批附录和可匹配的结果、事件样例，禁止用最近命令或平台容量推断 |
| G3 Bin 与目标设备映射 | O9/O10/O11/O15/O16 涉及时限、层列/面、cell_index、必填位置字段及设备值域 | 限制任务 6、7 对应命令生成；须冻结映射、成功后置条件和合法边界。不能用编码 cell_id 充当 bin_cell_location |
| G4 DirectPick 映射 | O12/O13/O17 的退料架位置类型、slot 到设备字段及测量参数来源未定 | 限制任务 10 的直接取料设备路径；若需扩展 plan_delta，先批准具体 wire，再同步全部消费者，不反推 bin_* 参数 |
| G5 PkgID | decide 只传原始 barcode，已确认目标响应未提供 PkgID，而 movement_report 仍必填 | 任务 7 的正常生产闭环须先确认已有权威来源或修订所属合同；不拆条码、不伪造 ID、不私加响应字段，也不先做不可逆取盘再补猜报告身份 |
| G6 首批期限 | 业务超期规则已确认，插件配置入口、双方期限值及合法范围待落定 | 任务 5 建立插件唯一配置入口并冻结每次等待；不复用 30 秒技术派发窗口，未明确值不猜默认值 |
| G7 异常分支 | REJECT、空取和 NG 协议保留，设备失败/空取语义及恢复动作仍有未决 | 正常切片只可靠留证并保留依赖；这些分支另做实施切片，不能因此删 DTO 或宣布整线业务验收 |

任务 1 可先完整推进。任务 2 按 2026-10-16 确认表开展 RED 与受控替换；G1 剩余设备映射不阻塞 DTO 与共享接线；
任务 3 及后续可先做不依赖未决值的 typed/纯业务工作，依赖这些值的生产接线须等待对应证据。
正常取盘生产闭环要求 G1/G2/G3/G5 的对应项闭合，首批超期路径另受 G6 约束；DirectPick 设备路径另须 G4。

### 实施顺序、代码依赖与生产门槛

编号是默认推进顺序，不把整项任务验收当成所有后续工作的硬依赖。先对齐相应路径已确认的合同，再接其业务；
允许独立声明、已确定接口及纯业务测试先完成，不能用旧 DTO、临时类型或成功占位绕过未决字段，不能把部分完成记成整片 GREEN。

| 可独立推进的部分 | 实际代码依赖 | 未决项只限制什么 |
| --- | --- | --- |
| 任务 4 包声明、静态装配 | 任务 0、现有插件 SPI；具体端口随所属业务片注入 | 不等待 G1–G6；未接业务时不允许宣称插件已可执行 |
| 任务 5 准备/投料、首批等待、Work | 分别依赖任务 4/既有投料端口、确认进度、任务 3A | G6 只限制首批期限配置与超期路径；Work DTO 已确认；G1 剩余项只限制对应设备映射，不依赖 G2/G3/G5 |
| 任务 8 的 NO_WORK 流出及回程 | 任务 5 的 Work/点2交接、既有方向命令与回架端口 | 生产调用须有完整获批 work_plan decoder 和对应方向合同；不依赖任务 6–7 或 G2/G3/G5，离线回程测试可先做 |
| 任务 8 的逐盘结束后流出 | 上一行，加任务 6–7 的明确无后继及本轮逐盘收口 | 只有这一入口继承取盘/报告的对应未决项，不扩大到回程公共推进 |
| 任务 9 drain 准入及完成判断 | 任务 8 的当前 FIFO/回程、任务 5 的 prepare 协调与确认进度 | 不等待全量取盘设备验收；实际关闭含物料任务仍须其必报事实已确认 |
| 任务 10 的取消/范围完成与直接取料 | 计划事实部分依赖任务 4–5 及既有接收；设备部分复用任务 6–7 | G4 只限制 DirectPick 设备接线，G5 只限制需逐盘报告的正常闭环，不要求先完成无关 drain |

### 当前任务 5–6 剩余项与继续条件

以下按已提交基线及后续 5A/6A/6B/6C 的明确验收范围核对，不把基础端口已有、单项测试通过或本计划的复选框当成整项业务已完成。

| 路径 | 当前事实与归属 | 继续条件 |
| --- | --- | --- |
| 任务 5 普通 prepare、投料、SCAN2/Work | 已实现并提交，Work 的 READY/WAIT/NO_WORK、结果应用/发布及持久恢复已有验收 | 不受 G2/G3/G5/G6 阻塞；后续只补本业务缺口，不重复实现共享可靠机制 |
| 任务 5 跨 revision 来源接续 | 切片 5A 的 6 项 PostgreSQL 验收已通过：当前进场复用原 Transport，各 revision 成员独立申请，重启/并发扫描防重、事务回滚、取消及闭合历史清理均已覆盖；未改生产代码 | 本项已闭合，不依赖首批期限或机械臂参数；首批确认和任务 6 按各自条件继续 |
| 任务 5 首批等待与 revision 0 确认 | 宿主在查找 EXECUTING 前已有 batch 钩子；自动插件目前只恢复 Feed/Work，尚无任务确认进度、配置入口或 completion driver | G6 需给出期限值、单位、合法范围及具体配置落点；不得借 30 秒技术派发期限、重启时间或 WMS 重试间隔代替 |
| 任务 5 正常完成确认的共用协调器 | completion_confirm 基础合同/调度可用，自动插件的业务发起与重问进度尚未接入 | 首批分支受 G6 限制；普通完成分支不因 G6 一概停工，但必须具备可证明的当前本地完成谓词。涉及逐盘报告、Work 放行及回程的完整验收随任务 7–9 闭合，不把空查询当成完成 |
| 任务 6 已确认纯业务规则与持久适配 | 6A 的来源规则/协调器由 6B 补入 Pickup、Work 消费/当前取出事实引用及 Repository；batch/Work owner 可显式注入来源协调器，生产组合根尚未启用 | 6A 离线证据与 6B PostgreSQL 证据分别记录；6C 补齐明确关联输入后的单盘应用；真实事实 reader、设备映射与生产扫码汇合仍未启用，不能以持久适配或离线接线代表生产取盘闭环 |
| 任务 6 Pickup/来源 Command 与扫码关联 | 设计要求同事务保存授权消费、Pickup、来源 Command；真实扫码必须精确关联 | 生产构造/真实扫码接线须闭合 G2 的事件设备、来源 Command 引用/business_key 规则及 G3 对应来源映射；离线测试不受这些值阻塞，但不先建无 Command 的 Pickup 或仅先扣减授权 |
| 任务 6–7 不可逆取盘及正常逐盘闭环 | 需要可靠取出、关联扫码、decide、目标动作和 movement_report 都有确定合同 | 除来源映射/关联外，还须闭合 G3 的目标映射及 G5 权威 PkgID 来源；不能先取盘再补猜报告身份 |

G1 的 WMS 字段类型和已确认枚举不重新列为阻塞：`cell_index=1..7`、`bin_type=2/3/6`、层列/层数整型、厚度/直径浮点及原始 barcode 长度已接通；这些不等于设备字段映射已经验收。G4 只限制任务 10 的直接取料设备路径；G7 只限制对应异常分支，不扩大成投料、Work 或纯规则测试的停工条件。

**任务 5 首批分支的唯一 owner 与出口：**G6 闭合后，由自动插件的同一个 completion driver 接入现有 batch 前置钩子，扫描 PREPARING/revision 0。首次匹配 PREPARE_ACCEPTED 的持久接收时间加冻结期限得到到期时间，重复扫描/重启不重置；计划先到停止新建 revision 0 请求，已有义务仍沿原身份交付。确认创建和进度同事务；未决结果由基础 Confirmation 恢复，明确 BUSINESS_IN_PROGRESS 的新请求由插件按到期重新求值，PLAN_REVISION_STALE 等补发增量，旧响应不能关闭新 revision。普通 EXECUTING 分支使用同一协调器，不另建 AUTO worker/timer 或第二个完成 owner。

**任务 6 的所有权边界：**初始及后继待消费授权归当前 Work；消费授权、创建/复用 Pickup 和原来源 Command、登记消费关联必须同事务。Command 技术恢复归设备基础服务；取出结果与扫码汇合归插件 driver；关联成功后才创建/复用 MaterialExecution。最终 ACCEPT 的本盘目标与后继授权/明确无后继交接同事务，Work 可在 M1 闭合后独立恢复 M2，不查询可清理的 M1 历史。只有当前栈顶可靠取出且有 WMS 后继授权才可开始下一来源；无需等待 M1 PUT/report，全线/平台不加软件锁，互锁归 ECS/PLC。

#### 实施切片 5A：跨 revision 来源接续验收（已完成）

**交付：**闭合任务 5 中不依赖 G6 的剩余来源身份路径；先验证现有能力，只在发现实际缺口时作必要修正。不新增模型、状态、配置、operation 或生产入口，不顺带做任务 6–9。

**文件与接口：**新增插件 owner `T/integration/test_source_revision_postgresql.py`；复用宿主 `PickingTaskPlanActivationService.activate_batch()`、插件 `AutomaticPickingBatchDriver.advance_in_session(db, line, task)`、`FeedRepository.source_ready(db, line, member)` 及既有 Transport/Confirmation。若回归证明插件有缺口，仅修改对应 `P/application/{batch_driver,feed_repository}.py` 或计划 handler；若是共享能力缺口，单独说明真实影响，不在插件复制基础机制。既有新旧批次扫码用例仍保留其主要测试所有权。

- [x] 先建立同任务、同 rack/face、两个 revision 的真实成员及当前进场事实，验证复用当前物理生命周期、原 Transport 身份不变；每个新有效成员恰好创建一次带本成员 revision 的 inbound_batch。
- [x] 验证重复扫描/重启/丢通知不重复申请；创建 Feed 与原 Confirmation 的事务中断全部回滚。旧成员取消只停其未提交尾部，不能取消新成员或让旧结果满足新成员。
- [x] 验证位置未知、未发布或实际面向不匹配时不得提前投料；新成员需要另一面时只验证等待其实际到位，不提前实现旋转/离场。清理不再承接当前事实的已闭合历史后，新成员仍按当前有效意图和事实运行。
- [x] 测试准备数据及批处理计数期望对齐现有端口后，6 项业务验收均通过，未发现生产实现缺口，保留现有生产代码。未为得到 RED 破坏实现。
- [x] 在隔离 PostgreSQL/显式插件环境运行新 owner 与插件受影响回归；如触及宿主入口、队列或模型再选择对应真实 worker、HEAVY/迁移验证。插件测试不进入核心默认套件；按最终差异做一次闭环 Review。

**5A 验证与 Review：**新增 [来源接续 PostgreSQL owner](../../../workline_plugins/automatic-picking/tests/integration/test_source_revision_postgresql.py) 共 6 项；最终显式插件套件 `uv run pytest -c workline_plugins/automatic-picking/pytest.ini workline_plugins/automatic-picking/tests -o addopts= -q --tb=short` 为 61 passed（42 FAST、19 PostgreSQL，含既有真实 Confirmation worker）。测试拓扑/插件所有权检查 14 passed，Ruff 检查、格式及 diff 检查通过，核心 HEAVY selector 为 NONE。每项 PostgreSQL 场景使用迁移至 head 的独占临时库；未改模型、迁移或宿主入口。
闭环 Review 核对了当前窗口/原 Transport、成员 revision、并发恢复、事务回滚及取消边界，无未关闭问题。历史清理用例先经真实 FactProcessor 发布旧结果，再删除已闭合的旧 Confirmation，并验证 revision 3 继续申请；当前成员、Feed 完成标记、进场绑定和权威位置依据继续保留，未把当前事实误作可删除历史。任务 5 其余未完成项和任务 6 保持原状态；未提交、未部署。

**验收：**当前生命周期复用和成员业务身份独立同时成立；每个未闭合 Feed 的 owner 仍是既有 batch driver，Confirmation/Transport 各沿原身份恢复；不读取已闭合历史决定新业务，无新机械臂 Command。验收后仅勾选任务 5 的来源跨 revision 项，首批超期与任务确认项、任务 6 仍不得整体勾选。

G6 获批后再做任务 5 的首批确认小片；任务 6 的纯规则及来源事务协调器已由下述 6A 完成离线验收，fixture 中设备参数仅为测试输入/非合同，不生成生产默认值、fallback 或成功占位消费者；模型仅随能够独立验证的生产原子业务片落地。生产 Pickup/Command 与扫码接线待对应合同闭合后整体接通。若转做任务 8 的明确 NO_WORK 出口，可按其原有独立依赖另开切片，不以任务 6/G5 为阻塞，也不混入本次 6A。

#### 实施切片 6A：来源规则与离线事务验收

以下记录 6A 结束时的范围；后续持久适配以 6B 的记录为准。

**范围：**插件新增 `P/handlers/source_authorization.py` 和 `P/application/source_flow.py`；分别由 `T/test_source_authorization.py`、`T/test_pickup_flow.py` 拥有验收。`WorkSourceFact` 只是当前 Work、直接 Requirement 和当前物理事实的不可变输入快照，不是生产授权表、Cell 库存或新业务状态。

- [x] READY/后继授权归当前 Work；按 Work 和原授权 Evidence 消费。同 Cell 可连续取多盘，不以 `(work, cell)` 去重；跨 Cell 只跟随 WMS 后继，不按 Cell 编号排序。
- [x] 消费授权、创建 Command/Pickup、记录消费关联共用调用方事务；ACCEPT 目标与后继授权/明确无后继也共用事务。中断、并发扫描、重启和旧结果重放只恢复当前身份。
- [x] 当前栈顶未取得可靠取出事实时不越过；后继授权有效且栈顶可靠取出后，不等 M1 的目标准备、PUT 或报告。实际双臂交接许可仍归 ECS/PLC。
- [x] M1 已交接的当前事实保留在 Work 输入中，注入 M1 已闭合的前提并清理旧 Pickup/Command 测试历史后仍可创建 M2；旧 READY/ACCEPT 不覆盖新授权。新 Work 轮次独立消费，实际同箱 SCAN2 准入仍由任务 5 的既有 owner 验证。
- [ ] 新增生产持久模型/Repository、真实权威事实 reader、完整的单盘 MaterialExecution/fact_factory、扫码汇合及 runtime composition；这些仍属于任务 6 的后续切片，不由离线 fake 承接。

**端口与恢复边界：**协调器要求 `source_for_update` 在调用方事务内按 WorkLine→Work 顺序锁定，ACCEPT 再锁定当前 Pickup；不提交事务、不执行网络 I/O、不等待其他机械臂或设备结果。待消费授权与消费关联由当前 Work 承接；Command 可靠恢复复用基础服务。请求映射器为必填注入项，仅返回现有 `DeviceCommandRequest`，保留 WorkLine 与扫码前 Pickup execution_ref，不能追改为稍后识别的 MaterialExecution；没有生产默认映射或 fallback。当前没有生产持久适配和装配，因此运行时尚不能创建或恢复此片的 Pickup；未来接通时由已有 batch/Work owner 恢复，不能另建自动 worker。

**离线证据：**新增 36 项（纯规则 12、事务 24），插件 FAST 合计 78 passed；核心测试拓扑/插件所有权 14 passed。事务端口仅在测试内实现，使用本地 SQLite 真实回滚与并发写事务；其写事务串行化是测试 harness，不是生产平台锁，也不证明 PostgreSQL 行锁实现。双臂验收只覆盖下一来源不等待前盘目标阶段，真实 PUT/双臂派发与互锁属于任务 7 及设备合同。Command 参数、可靠取出事实、M1 闭合前提和 MaterialExecution 关联均为明确的测试输入；不解析设备回调、不构造扫码关联、没有 PkgID，也不派发设备命令。19 项原有 PostgreSQL/worker 测试本轮未执行，生产入口未变，不以旧结果替代本片证据。

**后续准入：**G2/G3/G5 继续只约束对应的生产取盘、设备/扫码关联和逐盘闭环；G6/G4/G7 不扩大阻塞本片。任务 6 的完整验收项继续保持未勾选，不将 6A 当作可部署的取盘闭环。

**6A 闭环 Review：**已核对规则输入、Work 授权消费/交接、Command 原身份、测试所有权及当前运行时调用面，无未关闭问题。明确 WorkLine→Work→Pickup 锁顺序，补强连续取盘使用不同 MaterialExecution 的测试身份，保留真实 PostgreSQL 适配的验收门槛；纯规则仅导入 SDK，安装包可导入新增模块，核心 HEAVY selector 为 NONE。未新增生产持久模型、业务状态、配置或基础层业务逻辑，未接 composition，未提交、未部署。

#### 实施切片 6B：PostgreSQL 来源授权持久化与离线恢复

**范围：**复用 6A 的 `AutomaticPickingSourceFlow`，增加插件私有 Pickup 与 Repository。Work 只补 `consumed_pickup_id` 和 `top_removed_evidence_id`：前者用于恢复同一消费结果，后者将当前可靠取出事实保留给后继，不依赖已闭合前盘。来源 locator、本盘目标、ROTATE/REPLACE 及后继/明确无后继内容均引用原 Evidence，不复制响应、不新增授权表或业务状态。

**恢复 owner：**已有 batch/Work driver 显式注入来源协调器后扫描当前 Work；未消费授权归 Work，已创建 Command 的技术恢复归设备基础服务。取出尚未知时前盘仍是当前未闭合依赖；匹配事实转入 Work 后不查询其可清理 Pickup/Command。锁顺序 WorkLine→Work→Pickup，短事务内不执行外部 I/O。没有新 worker、平台锁、全线 FIFO 或跨臂等待。

**验收范围：**真实 PostgreSQL 及真实 `DeviceCommandService.create_command_in_session`；覆盖各写入阶段回滚、行锁等待、并发消费/ACCEPT 重放、同 Cell 和跨 Cell 后继、明确无后继、未知栈顶补证、成员取消、旧 READY/ACCEPT 重放、清理旧 Pickup/Command 后恢复、存量 Work 升级与有数据降级拒绝。模型/迁移差异、核心测试所有权及 selector 单独验证，插件事务测试唯一 owner 为 `T/integration/test_pickup_identity_postgresql.py`，不进入核心 HEAVY。

**未启用边界：**设备事实 reader 和请求映射器均为必填注入项，没有生产默认实现；测试明确提供设备参数、扫码/MaterialExecution 关联与可靠取出语义。生产组合根不注入来源流程，设备命令不派发；6B 结束时尚无单盘业务应用/FactProcessor handler，后续范围见 6C；生产扫码汇合及任务 7 仍未实现。G2/G3/G5 继续仅限制对应生产路径，G4/G6/G7 不扩大阻塞本片。未提交、未部署、不推进真实取盘。

**验证与 Review：**插件合计 128 passed（78 FAST、31 项本片 PostgreSQL、19 项既有 PostgreSQL/真实 worker），无跳过；核心安装/拓扑/selector 聚焦 252 passed。完整 QUALITY 通过（脚本 428 passed，默认 FAST 4280 passed/5 既有 skipped），核心 selector 仅选 2 文件，固定 PostgreSQL 17.10/TimescaleDB 2.27.1 上 HEAVY 18 passed。模型/迁移 diff 为零；全量核心 metadata 比对有既有 inbound_evidences/picking_tasks 环引用 SAWarning，不属于本片模型差异。

独立首轮 Review 发现 1 项：调用方预载取消前成员时，WorkLine 锁不会刷新 ORM identity map，可能让已取消 Requirement 创建 Command。真实 CancelService 交错回归 RED→GREEN，锁后显式刷新直接成员，未加状态或锁模型；最终受影响插件集合 109 passed。此前本片验收发现的“重复当前取出事实仍要求旧 Pickup 存在”也已由 RED→GREEN 修正。同一 Reviewer 在一轮中完成意见闭环及 fresh full Review，无未关闭 finding。后续修正只触及插件 Repository/测试，未失效的核心 QUALITY/HEAVY 与既有 worker 证据不重复执行。证据绑定当前可执行文件指纹及隔离环境，不代表生产来源流程启用或真实设备验收。

#### 实施切片 6C：精确关联输入后的单盘离线接线

**范围：**`P/application/material_flow.py` 实现必填 typed `MatchedMaterialScan` reader 边界、原扫描 Evidence 的单盘绑定和 Fact 增强；`P/handlers/material_flow.py` 只依赖 SDK，纯 handler 创建首次 `PickingMaterialIntent` 或确定 ACCEPT/WAIT 的 `Wait` Decision。扫描必须精确给出 WorkLine、Pickup、来源 Command、原始 barcode 和扫码时间；测试专用报文不是设备合同，不推断最近命令、设备位置或 PkgID。

- [x] 原扫码 Evidence、Pickup→MaterialExecution 关联同事务保存；重复、并发及重启沿原 admission Evidence 复用一个单盘。Pickup UUID 仅用作 WES 本地 trace/执行/首次 decide identity，不能作为 WMS PkgID；原 prescan Command 的 execution_ref 和空 material_execution_id 不改绑。
- [x] FactFactory 核对原 Work/成员/授权来源、当前单盘与扫码、typed 原请求、结果 operation/response Evidence 及摘要；纯 handler 保留 barcode 原文和 scanned_at，不在基础层解释 Cell、目标或后继业务。
- [x] 宿主只增加可选 `PluginDecisionApplication`：精确 frozen plugin binding 解析后，在 Decision 应用与 Evidence 发布之间调用，复用 FactProcessor 原事务、claim、失败退避与持久扫描；宿主既有 `material_decide_typed` 补持久请求到 SDK intent 的解码，Repository 校验持久摘要/请求身份，业务应用不读取 wire 信封；插件回调复用 6B `handoff_in_session`，目标结果引用与 Work 后继/明确无后继和 Evidence 发布同事务。未安装回调的插件行为不变。
- [x] 明确 WAIT 是已确定结果，原 Confirmation 完成、MaterialExecution HOLD、原授权仍已消费，不创建后继或新请求；交付未知尚无业务结果，基础 Confirmation 以 PENDING/retry_eligible/next_attempt_at 保留原 identity/body 技术重试。技术派发窗口不解释为业务重问期限。
- [x] 离线验证 ACCEPT 不等于可靠取出；得到匹配的栈顶取出 Evidence 后，M1 仍 HOLD 时 M2 可独立绑定/decide，不增加全线 FIFO、平台占槽或另一臂的报告依赖。
- [x] 6D：保存确定 WAIT 的到期进度并由已有插件 owner 到期创建唯一新 operation_id；实现与独立验收见下节，不将 WAIT 原请求重发当作业务重问。

**恢复 owner：**来源授权继续归当前 Work/batch；设备 Command 技术恢复仍归基础服务；精确扫码关联成功后的未发布 MaterialExecution Evidence 唯一归 FactProcessor 与冻结插件。ACCEPT 事务中断会回滚 Decision、Pickup 目标引用、Work 授权与发布标记，持久 claim/退避恢复原 Evidence。绑定及 FactProcessor 均按 Evidence→WorkLine→Work→Pickup 排序；来源 batch/Work 继续使用 6B 的 WorkLine→Work→Pickup，不反向锁扫码 Evidence。扫码与来源 Command 的生产汇合仍须 G2；没有新 worker、授权模型或业务状态。

**装配边界：**`build_plugin` 只有显式注入 material flow 时安装两个 handler/factory/事务回调，离线测试使用真实 FactProcessor、DecisionApplier、Confirmation/Command Service；生产 deployment 仍不注入来源或 material flow。G2 精确扫码生产合同、G3 设备映射/可靠事实读口、G5 权威 PkgID 不猜，任务 7 动作/报告尚未启动；G4/G6/G7 只限制各自分支。

**验证：**最终插件 FAST 84 passed；修正后的受影响 PostgreSQL 53 passed（6C 22 项、6B 31 项），无跳过。首轮完整 PostgreSQL/真实 worker 71 passed；其中未受修正影响的原 19 项 Feed/Work/revision/worker 证据复用，不将其描述为修正后重新全量执行。核心域合同与执行聚焦 949 passed；最终 QUALITY 通过（脚本 428 passed、默认 FAST 4286 passed/5 既有 skipped）。现行 selector 因触及 `outbound_picking/**` 扩为 19 文件，固定 PostgreSQL 17.10/TimescaleDB 2.27.1 与独占 Redis 上 113 passed、无跳过。插件测试不进入核心 selector。已有 metadata 环引用及 plan_delta 测试数据的 Pydantic 序列化 warning 不属于本片新增模型/合同变化。Ruff、格式、计划引用和 diff 检查通过；无 schema 或迁移变化。

**闭环 Review：**首轮发现 2 项 P2：绑定重放与 FactProcessor 的 Evidence↔WorkLine 反序死锁；插件应用层解释持久 wire 信封。先以真实 PostgreSQL 受控交错复现死锁，再统一锁序；宿主 converter 补严格字段类型的 `decode_request`，Repository 回传 typed intent，应用层退出信封解读。同一独立 reviewer 在一轮内完成旧意见闭环及当前完整 14 文件的 fresh full Review，逐文件指纹一致，无未关闭 finding。后续仅补本文证据，不失效当前可执行快照。未暂存、未提交、未部署，生产入口仍未启用，不把离线成功视为真实取盘或双臂物理验收。

#### 实施切片 6D：确定 WAIT 的持久到期续问

**最小持久进度：**Pickup 只增加 `material_confirmation_id`（当前可靠请求）和 `material_retry_at`（当前 WAIT 的 `Confirmation.completed_at + retry_after_ms`）。不复制 WMS 响应或来源授权，不新增模型、业务状态、定时 Evidence、worker 或配置。首次 decide 与游标登记、WAIT 到期与结果发布、新 Confirmation 与游标切换、ACCEPT 与 Work 授权交接分别复用原事务。纯 handler 仍只输出 typed Decision。

**恢复 owner：**已有关联且未 ACCEPT/未 CLOSED 的单盘由插件 material flow 扫描；已有 batch 的 `advance_completed_in_session` 在检查新任务状态和当前 Work 列表前委托，不依赖一次性通知。只有确定结果已由 FactProcessor 发布、到期且原直接成员未取消、原 Work 仍持有本盘消费时，才创建新 operation_id。未发布结果继续归 FactProcessor；当前请求交付未知或待对账继续归原 Confirmation，超时不转为新业务请求。已持久义务不因父任务状态变化丢弃；直接成员取消只停止新业务续问，不虚构持有物料的退出事实。

**身份边界：**续问沿同一 Pickup/MaterialExecution、扫码 Evidence、来源 Command、task/source locator、barcode 原文及 scanned_at，仅更换 operation_id 与新请求信封 timestamp。宿主同域 `MaterialDecideScheduler/ResultReader` 只复用基础可靠创建并严格读取 typed 原请求/结果，核对 owner、operation、Evidence、摘要及封闭响应；插件核对当前游标与原业务事实。旧结果不能回退游标或覆盖 ACCEPT；后续恢复不寻找“最近请求”，不依赖完整历史请求链。当前 MaterialExecution 仍引用的 HOLD 事实属于当前依据，不能当作可清理历史删除。

**存量与迁移：**迁移 `548db61057e6` 仅添加上述两个可空字段及当前请求外键。空游标统一按首次 operation_id=Pickup UUID 初始化，承接首次结果前及存量 6C WAIT；已发布 WAIT 从原 completed_at 恢复到期时间，不重投原 Evidence、不从重启时间重新计时。未发布结果仍留给 FactProcessor，丢失或漂移的精确请求 fail closed。有游标/到期数据时拒绝有损降级，不猜测转换或删除运行进度。

**测试所有权：**`T/integration/test_material_wait_postgresql.py` 是 WAIT 续问业务的唯一主 owner，覆盖并发、各事务崩溃恢复、WAIT→WAIT→ACCEPT、技术未知、旧结果重放、身份漂移、直接成员取消/父任务完成、可清理历史删除及存量 6C 升级；6C helper 补齐真实 Confirmation 完成时间。插件 FAST 只验证已有 batch 委托；核心 FAST 仅验证固定 typed 端口差异，核心 HEAVY 沿域映射及新增迁移映射运行，不接收插件业务测试。

**启用与未决范围：**生产组合根仍不注入来源或 material flow，无生产扫码关联、设备取盘/PUT 或 movement report。G2/G3/G5 分别约束设备扫码关联、映射与可靠事实、权威 PkgID；G6 首批期限不阻塞本片，使用已确认 retry_after_ms；G4/G7 仅约束各自分支。任务 6 完整闭环和任务 7 仍未完成，不做 bin-line-common 抽取。

**验证：**6D 新增 PostgreSQL 22 passed，既有插件事务/真实 worker 回归 72 passed，插件 FAST 85 passed，合计 179 passed、无跳过；含空库升降级、存量 6C WAIT 升级恢复、有进度拒绝降级及模型/迁移零差异。核心 typed 端口/selector/拓扑聚焦 254 passed；完整 QUALITY 通过（脚本 429 passed，默认 FAST 4292 passed/5 既有 skipped）。现行 selector 为 21 文件，固定 PostgreSQL 17.10/TimescaleDB 2.27.1 与独占 Redis 上最终 HEAVY 108 passed、无跳过。首轮 HEAVY 的 49 项 setup error 来自旧 integration fixture 拒绝 postgres 管理库命名；同实例预迁移 test_auto6d 后完整重跑同一 manifest 通过，没有为环境错误修改生产代码或放宽护栏。现有 metadata 环引用、Redis close 弃用及 plan_delta fixture 序列化 warning 保留，不扩面修正。

**闭环 Review：**独立 reviewer 核对固定 14 文件及 SHA-256、合同、事务/游标/恢复 owner、取消锁序、历史边界、测试/helper 与 HEAVY 所有权，无可证实 finding。所有可执行文件保持评审指纹；后续仅补本文验证记录，Ruff/格式及 diff 检查通过。Git HEAD、index 与共享 config 未漂移，无暂存；未提交、未部署，不推进真实取盘。离线成功不代表 G2/G3/G5 或任务 7 已具备生产准入。

#### 实施切片 7A：普通目标已就绪后的 PUT 创建离线事务

**范围：**仅 BIN_CELL 来源、最终 ACCEPT 的普通 RACK_SLOT 目标，且明确的 typed reader 已确认当前盘目标架/面/工作位及直接准备依赖就绪。`P/application/target_preparation.py` 通过现有 `DeviceCommandService` 创建 PUT；reader 和请求映射器均必填，没有生产默认实现。测试的设备、时限和报文只为离线输入，不构成 G2/G3 合同；不应用 PUT 成功为位置，不创建 movement_report、不关闭单盘，ROTATE/REPLACE 留给后续切片。

**最小持久关联：**Pickup 仅新增可空唯一 `put_command_code` 外键，命令正文和技术生命周期只保存在原 DeviceCommand。新命令与引用在 WorkLine→Work→Pickup 锁事务中同时提交/回滚；内部 `WORKLINE_BUSINESS / put:<pickup UUID>` 表示本盘 PUT 步骤，关联已存在的 MaterialExecution，不改变扫码前来源 Command。该内部身份不定义设备附录 O6 的 business_key，也不是 PkgID。迁移 `4e30101440d2` 增列、唯一约束及外键，有 PUT 引用时拒绝有损降级；无授权模型或新业务状态。

**业务与恢复 owner：**已有 batch 的 `advance_completed_in_session` 可选注入 target flow，在当前 Work/任务激活过滤前扫描当前未 CLOSED 的 ACCEPT Pickup；已发布 ACCEPT 是当前待创建目标动作的依据，不重投该 Evidence、不新问 material.decide。创建前核对当前请求、ACCEPT Evidence、WorkLine/单盘/来源及直接成员取消事实；复用现有 physical reader 按本盘冻结来源与原来源 Command 核对可靠取出 Evidence，未知则等待，不借 Work 当前栈顶解释旧盘；父任务终态不代替直接取消。已建 PUT 只按精确引用恢复，不重算目标、映射或 deadline，不因取消、超时或未知状态换身份；技术履约归原 Command 基础服务。本片不消费成功结果，也不借命令状态宣布物理完成或单盘收口。

**并发边界与所有权：**M1 PUT/报告不作为 M2 来源创建的统一门槛；M2 仍由当前 Work 授权及可靠栈顶取出事实准入，未知栈顶不得越过。`T/integration/test_target_put_postgresql.py` 唯一拥有 PUT 创建业务的 PostgreSQL 验收，复用 6B/6C 输入并实际使用 batch 入口、typed material reader 和 DeviceCommandService；不复制基础派发/ACK/技术重试矩阵。核心仅承接迁移/selector/拓扑测试，插件业务测试不进入核心 HEAVY。

**启用边界：**production composition 仍不注入来源、material 或 target flow；G2/G3/G5 继续约束对应生产路径，不猜设备映射、扫码关联或 PkgID。G4/G6/G7 不扩大为本片阻塞；后续成功 PUT 与位置/报告义务的原子联动必须单独完整实施，不以 fixture 将单盘直接标为 CLOSED。

**验证：**最终 PUT PostgreSQL 36 passed，插件既有 PostgreSQL/真实 worker 回归 94 passed，插件 FAST 86 passed，共 216 项通过且无 skipped；核心聚焦 248 passed。最终 QUALITY 通过（核心 FAST 4292 passed / 5 项既有 skipped，不将 skipped 作为通过证据）；selector 仅选中核心工作线配置 PostgreSQL 文件，8 passed。已覆盖命令/引用双侧故障回滚、并发重复创建、重启沿用原引用、旧 ACCEPT 重放、取消与身份漂移、未知取出等待、外来/未应用取出事实拒绝、M2 接续后仍按 M1 精确来源创建、空库/存量迁移、metadata 无差异及有损降级保护。

**闭环 Review：**独立 reviewer 首轮发现创建 PUT 缺少本盘可靠取出事实门禁；先以回归复现后修正，再由同一 reviewer 完成旧意见闭环与当前 10 文件完整复审，无新增 finding。跨工作线测试夹具另修为真实存在的 WorkLine，定向核对确认断言语义及 owner 不变，最终 36 项与 QUALITY 已重新通过。准确 RED 为 4 项真实门禁失败和 1 项无效 FK 夹具失败，后者不计业务失败证据。最终仅补本文验证记录，可执行文件保持评审指纹；未暂存、未提交、未部署，不推进真实取盘。任务 7 整体及 G2/G3/G5 生产准入仍未完成。

#### 实施切片 7B：确定 PUT 位置与报告义务的离线原子应用

**范围：**只接 7A 的 BIN_CELL→普通 RACK_SLOT 原 PUT Command。`P/application/placement_flow.py` 的必填读口显式给出匹配成功事实、实际放置时间和本盘权威 PkgID；没有生产读口，不从 barcode/trace 推导，不定义设备结果报文。插件纯 typed Fact/Handler 通过现有 facade 返回 movement_report intent，宿主原 DecisionApplier 创建 Confirmation；不改 SDK/wire/基础流程或生产 composition，不关闭单盘、不启用真实派发。

**最小持久关联：**Pickup 只增加成对的 `put_result_evidence_id` 与 `movement_confirmation_id`（迁移 `55adfc1a25a7`）。前者指向保存确定放置内容的原成功 Evidence，后者指向冻结完整报告的 Confirmation；不另存目标/PkgID/请求正文，不新增位置账本、授权模型或业务状态。配对约束防止半份关联；已有 7A Pickup 保持空引用，存量按当前 PUT 事实接续。存在已确认引用时拒绝有损降级。

**身份与原子性：**先核对原来源/单盘绑定、PUT 的 WorkLine/MaterialExecution/步骤身份、Command 的精确 result Evidence、SUCCEEDED 与匹配成功读口，再核对当前已发布 ACCEPT 的来源与精确目标。ACK、超时或单独状态值都不能生成位置报告。报告只带合同规定的 RACK_SLOT 架/面/槽位，层列仍属目标设备映射。报告 intent、当前位置/报告引用、Evidence Decision 摘要和发布在 FactProcessor 的同一应用事务中提交/回滚；重放沿相同 operation_id、Evidence 和正文，不查询最近历史。

**恢复 owner：**7A batch 继续发现/关联 PUT；成功结果 Evidence 由 FactProcessor 的既有领取、租约、延期与重领扫描恢复，冻结插件的唯一应用路径负责业务解释。已建报告归 Confirmation 原身份履约，不因父任务终态或直接成员取消丢弃已发生物理事实。未知结果/权威 PkgID 未给定时返回 DeferExecution，原 Evidence 未发布、无新报告；本片收到报告结果也明确延期给后续关闭切片，不加 no-op 成功消费者。Work 的下一来源授权仍独立，不等待 M1 报告。

**验收与所有权：**`T/test_placement_report.py` 拥有纯 SDK 决定，`T/integration/test_placement_report_postgresql.py` 拥有成功 PUT→位置/报告原子应用的 PostgreSQL 验收，复用 6B/6C/7A 输入和真实 FactProcessor/DecisionApplier/Confirmation。覆盖回调先于 ACK、并发、双侧故障回滚、租约到期、通知丢失、重放/身份漂移、缺成功/PkgID、M2 并行、存量迁移及报告结果不提前关闭。核心只承接精确迁移 mapping/selector、schema 与插件所有权；不复制基础派发完整矩阵。

**验证：**以已提交 7A `aac76175` 为基线，新增放置/报告 PostgreSQL 36 passed，既有插件 PostgreSQL/真实 worker 回归 130 passed，插件 FAST 88 passed，共 254 项通过且无 skipped；核心聚焦 257 passed，selector 仅选中工作线配置 PostgreSQL 文件，8 passed。最终 QUALITY 通过（核心 FAST 4292 passed、5 项既有 skipped，不计作通过证据）。metadata 无迁移差异，空/存量升级、成对引用和有损降级保护均已覆盖。首轮 35 passed/1 失败来自测试误把“处理并识别摘要冲突”的计数当成失败计数；依据现有内核行为修正计数，并补强原报告不变与 Evidence/MaterialExecution 进入既有 RECONCILING 的断言，最终 36 项已完整重跑通过。

**闭环 Review：**独立 reviewer 核对固定 13 文件及指纹、合同、精确身份、原子事务、恢复 owner、取消后事实、并发和测试/HEAVY 所有权，完整 Review 无可确认 finding。后续仅补本文验证记录，可执行文件保持评审指纹。HEAD、index 与共享 Git config 未漂移，无暂存；G2/G3/G5 仍只约束对应生产路径，fixture 的成功事实和权威 PkgID 不构成合同确认。未提交、未部署，不推进真实取盘，任务 7 整体和单盘关闭仍未完成。

#### 实施切片 7C：报告确认后的单盘关闭与 Work 当前事实续接

**范围：**只消费 7B 已冻结的 BIN_CELL→普通 RACK_SLOT 报告。宿主固定 `MaterialMovementReportResultReader` 按当前 Confirmation ID 校验 owner、operation/operation_id、请求摘要、响应 Evidence 的身份/摘要及 `COMPLETED + RECORDED/DUPLICATE`，返回纯 typed intent/outcome；不读取插件业务表、不决定关闭资格。插件 `MaterialReportedFact` / handler 使用现有 CompleteExecution，不改 wire/SDK，不新增模型、业务状态或迁移。

**关闭与原子性：**插件核对本盘当前报告、已发布 ACCEPT、已发布的精确成功 PUT 位置引用，以及报告冻结的原来源/普通目标。报告结果 Fact 的 operation_id、Evidence、typed outcome 必须对应同一当前义务。ACCEPT 已在 6B/6C 原事务交接下一来源或明确无下一来源，关闭时不再次交接；M2 无需已存在。若 Work 仍持有本盘 ACCEPT 且尚未消费，插件从既有可靠取出读口核对原来源 Command 的取出 Evidence，与 CLOSED、报告结果摘要和发布同一 FactProcessor 应用事务保存到 Work.top_removed_evidence_id；读口在准备阶段只读，不能提前提交该事实。缺可靠取出则由原 Evidence 延期恢复。若授权已消费或已推进，保留当前 Work 的游标与取出事实，不要求 M2 的取出/目标就绪，不回写前盘事实。直接成员取消或下一来源暂不可用不丢弃本盘已经确定的位置和报告事实；新来源仍遵守原准入。

**恢复 owner 与历史边界：**未完成报告义务归原 Confirmation；已保存未发布结果归原 FactProcessor 领取/租约/延期扫描。关闭前尚未移交的取出依赖归仍未闭合的 M1，关闭提交后归当前 Work；现有 batch 的 Work 扫描负责发现下一来源，不增加 worker/队列或关闭 Work。清理已闭合 M1 的 Pickup、原来源/PUT Command、已履约 Confirmation 及无当前引用的 PUT 历史后，Work 依靠仍被引用的当前 ACCEPT 和取出 Evidence 独立续接；这些当前事实及必要 owner 不是可清理历史。无下一来源由当前 ACCEPT 的省略字段明确表达，不以空查询推定完成。关闭 M1 不等同于 Work、Bin 或 PickingTask 完成。

**验收与所有权：**`T/integration/test_material_close_postgresql.py` 是报告确认→单盘关闭、Work 原子保存和跨历史恢复的主要 PostgreSQL owner，承接 7B 原延期关闭的两项测试；7B 文件继续拥有 PUT→报告创建。纯决定复用 `T/test_placement_report.py`，组合测试核对静态 handler。核心 FAST 拥有 typed decoder / reader 严格合同，核心 movement_report production wiring 验证无插件时 MaterialExecution owner 的可靠履约与 typed 读取；不复制插件业务矩阵。新读口由现有出库域 HEAVY mapping 覆盖，selector 合同新增该路径断言。

**启用与未决范围：**生产组合根仍不注入 material/来源/目标流程，不启用真实派发、不推进真实取盘。7C 读取已冻结的 7B 报告，不重新索取设备映射、扫码关联或 PkgID；G2/G3/G5 仍分别限制对应生产入口/事实读口和新报告身份的权威来源。普通目标以外的准备、RACK_SLOT/NG 分支及任务 7 其余路径仍未完成，未决值不猜，不做 bin-line-common 抽取。

**验证：**基线为已提交 7B `c1394d0a`。新增关闭 PostgreSQL 33 passed，既有插件 PostgreSQL/真实 worker 164 passed，插件 FAST 90 passed，合计 287 项通过、零 skipped；核心聚焦 305 passed。最终 selected HEAVY 为现有 mapping 选中的 20 个宿主文件，102 passed、零 skipped；最终 QUALITY 通过（核心 FAST 4314 passed、5 项既有 skipped，不计作通过证据）。7C 无新迁移；插件既有 schema/迁移回归和独占临时库的现有 head 升级均通过。

**失败闭环与 Review：**首轮宿主 HEAVY 因手动运行遗漏隔离 base 测试库迁移而有 49 项失败/初始化错误，迁移后定向 49 passed；新增 MaterialExecution owner 夹具另补必填设备标识及局部外键清理，完整业务断言保持不变，定向真实 worker 4 passed 后重跑完整 20 文件集合通过。独立 reviewer 核对固定 18 文件、typed 身份、原子关闭/发布、恢复 owner、历史清理、授权推进和测试/HEAVY 所有权，完整 Review 无可证实 finding；其后只变更核心夹具文件，同一 reviewer 定向核对确认能精确还原首轮指纹、无断言弱化或 owner 变化。生产可执行文件保持评审指纹，最终仅补本文证据。HEAD 不变、暂存区为空，保留工作区改动；本轮不提交、不部署、不启用真实派发或真实取盘，任务 7 整体仍未完成。

#### 实施切片 8A：NO_WORK 点2放行与首次 SCAN3 持久交接

**范围：**仅接当前 Work 的确定 NO_WORK，不接逐盘正常结束。`return_flow.py` 通过必填的离线命令 mapper 和精确 SCAN3 reader 使用现有 DeviceCommandService/FactProcessor；生产组合根未注入该 flow，不猜点2设备参数、期限、SCAN3 编码或关联规则，不启用真实派发。

**持久身份与原子性：**Work 仅增加唯一 `exit_command_code`，已有 EXIT_PENDING 保持唯一前置恢复依据。新 NO_WORK 的应用、点2 Command 创建/关联和 Evidence 发布同事务；存量已发布 EXIT_PENDING 由 batch 前置扫描补建，沿 Work/NO_WORK 冻结命令身份。已有命令恢复不重新计算正文或 deadline；明确 FAILED 转既有 RECONCILING，未知/超时沿原 Command 由基础机制履约，不创建替代动作。命令 ACK/成功不代替 SCAN3 到位，精确到位也不伪造命令终态。

**首次交接：**按当前 Work、原点2 Command、工作线、料箱、扫码设备、事件时间及当前 Evidence 核对显式关联。缺精确关联由原 Evidence 延期；错误身份进入既有对账。首次有效 SCAN3 在同一 FactProcessor 应用事务创建 `AutomaticPickingReturn`、关闭 Work 并发布该事实；重复扫码保留首次 Evidence。回程根仅保存工作线、料箱、原 Work 的不可变因果标识、原 Command 和首次 SCAN3 Evidence，不新增授权模型或状态，不引用已闭合 Work 外键。迁移 `bb07c44fb54f` 保留存量 EXIT_PENDING，拒绝丢失已建立的命令/回程身份的降级。

**恢复 owner 与截点：**SCAN3 前当前 Work/EXIT_PENDING 归既有 batch 前置扫描，且不要求父任务仍 EXECUTING；已建命令的派发/结果归基础 Command，未发布扫码事实归 FactProcessor 的领取、租约与延期恢复。交接后 Return 是后续回程的当前持久根，计入原 business blocker/archiver；重启及旧扫码重放不再读取已闭合 Work、WorkPlan Confirmation 或旧 SCAN2/NO_WORK 历史。下一片由既有 batch 接续该根；本片停在等待后续物理事实的离线交接点，不增加空成功消费者或假回架动作。

**测试所有权：**`T/test_bin_release.py` 拥有精确关联输入边界，`T/integration/test_returns_postgresql.py` 拥有 NO_WORK→点2命令→首次 SCAN3 原子交接、存量恢复、并发、双侧崩溃/发布、租约、重放、无 EXECUTING、回调早于 ACK、清理已闭合历史及新轮次隔离。核心只承接显式模型注册/过滤、迁移 selector 和既有 schema/工作线配置 HEAVY，不导入插件业务测试。SCAN4 FIFO、return_batch、drain 与逐盘正常结束入口留在后续切片，G2/G3/G5 继续仅约束各自生产路径。

**验证：**以已提交 7C `54b7ba6e` 为基线，8A PostgreSQL 33 passed、既有插件 PostgreSQL/真实本地 worker 回归 197 passed、插件 FAST 98 passed，合计 328 项通过且零 skipped。核心聚焦 255 passed，selector 仅选中 schema baseline 与工作线配置两个文件，HEAVY 18 passed、零 skipped；最终 QUALITY 通过（核心 FAST 4315 passed、5 项既有 skipped，不计作通过证据）。独占临时 PostgreSQL 完成干净迁移链、存量 EXIT_PENDING 升级、空引用降级/再升级及已建立命令/回程时的有损降级保护；Work/Return metadata 对比无差异。初次失败属于夹具终态/投料前提、漏载 metadata 及 FactProcessor 处理计数误用，修正后保留完整身份、状态、发布与回滚断言，最终 33 项统一重跑通过。

**闭环 Review：**独立 reviewer 核对固定 15 文件及 SHA256，完整检查精确身份、事务/锁序、原命令 owner、无 EXECUTING 的 batch 前置恢复、闭合历史清理、新轮次隔离、安装/迁移及测试所有权，无可确认 P1/P2 finding。后续仅补本文验证记录，可执行快照未变化。HEAD、暂存区、Git 配置与 worktree 判定未漂移，未提交、未部署、不启用真实派发或真实取盘；任务 8 整体及后续回程入口仍未完成。

#### 实施切片 8B：首次 SCAN4 事实、FIFO 投影与当前 Return 发现

**范围：**仅在精确关联显式给定的离线前提下接续 8A。SCAN4 reader 返回既有 Return 的本地身份、工作线、箱码、事件设备与时间；不新增设备 wire 字段，不猜扫码格式或沿用人工线/SCAN1–2 后缀。生产组合根未注入该 reader。业务仍在独立插件，不改 SDK、WMS DTO、基础 FactProcessor/Command 机制或抽取 bin-line-common。

**最小持久化与原子性：**现有 Return 只增加成对的 `scan4_evidence_id` 和 `scan4_event_time`，以唯一 Evidence 引用、完整配对约束及当前工作线/事件时间索引承接首次事实和 FIFO 运行投影；迁移 `d028d7c65eb7` 保留存量 8A 的空引用，不从 SCAN3、接收时间或历史命令回填。首次关联、时间投影与 Evidence 发布共用 FactProcessor 应用事务；并发只冻结一次，重扫及旧结果重放保留首次值。同一 Evidence 已归属另一 Return 时明确对账，不靠数据库唯一冲突重试。已有 SCAN4 事实时拒绝有损降级。

**FIFO 与资格分离：**使用获批的 SCAN4 同源事件时间，按当前已绑定的实际回程通道跨任务读取；不假定 SCAN3 与 SCAN4 设备时钟可比较，不使用 received_at、Return ID、条码或 source_event_id 作为物理排序键。仅已有 SCAN4 的当前 Return 进入观察队列；重扫不重排。不同 Return 的相同事件时间保留双方首次事实并沿既有 Evidence 对账，不能编造平局顺序。FIFO 查询对顺序冲突、首次事实未发布或身份/摘要/时间投影不一致返回不可判定；空列表仅表示无已观察入队记录，不表示物理 EMPTY 或 NO_BATCH。

**恢复 owner 与截点：**未发布 SCAN4 Evidence 归原 FactProcessor 领取、租约与延期扫描；缺精确读口延期保留原 Evidence，确定身份错误按既有对账边界留证。既有 batch 前置钩子直接发现当前 Return，独立于 Feed/未闭合 Work 和 EXECUTING 任务；发现是只读查询，不计作业务推进。当前根、FIFO 事实和原命令均保留给下一片，既有 business blocker/archiver 继续有效；清理可清理的前段 Work、Feed、已履约请求及旧 Evidence 后仍可发现、入队和重放。当前引用的必要事实及未闭合 Command 不是可清理历史。回程后继归 batch，既有命令仍归基础 owner；本片不创建方向命令、不赋予回架 READY、不消费 FIFO、不接 return_batch 或 drain，不增加队列表、业务状态、定时器或成功占位消费者。

**验收与所有权：**插件 `T/test_return_fifo.py` 拥有精确输入类型边界；`T/integration/test_return_fifo_postgresql.py` 拥有首次事实/投影/发布、并发、重放、前置 batch 接线、无 EXECUTING、存量升级与历史清理的 PostgreSQL 验收。8A 的原 `test_returns_postgresql.py` 继续拥有前段交接；核心只承接精确迁移 selector、模型/schema 和测试所有权边界。G2/G3/G5 继续限制对应逐盘生产路径，点3/4命令及生产扫码关联另按其实际合同闭合，不扩大阻塞本片离线事实与排序。

**验证与闭环 Review：**本片实现与验证完成。新增 PostgreSQL 验收 22 passed、8A 回归 33 passed、插件 FAST 107 passed；核心 selector/测试所有权 249 passed、选中宿主 HEAVY 8 passed。QUALITY 通过（4315 passed、5 个既有 skipped，跳过项不计通过）。迁移验证覆盖存量 8A 空配对升级、空事实降级后重升、已有 SCAN4 事实时拒绝有损降级，以及模型/数据库一致性；以上证据绑定本轮最终可执行快照。

独立只读 full Review 已核对 11 文件冻结清单、直接/间接测试所有权和恢复调用链，未发现需要修复的 P1/P2 问题。当前 Return 发现已接入无 EXECUTING 任务也可到达的 batch 前置入口，但查询结果尚不用于后继业务推进；生产精确关联 reader 与组合根装配、方向命令、READY、FIFO 消费、`return_batch` 和 drain 仍留待后续切片。本轮不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成。


#### 实施切片 8C：方向命令、成功结果与回架候选资格

**范围与前提：**在 8B 当前 Return 上，精确关联、完整方向请求和权威后置条件由离线 reader/mapper 显式给定；不定义 SCAN3/SCAN4 生产条码/设备参数规则。正常方向采用既有 `MOVE_FORWARD` Command，源位置、参数和期限均由给定映射完整提供，并由现有 DeviceCommandService 校验冻结 binding。生产组合根仍不注入回程流程，不接 `return_batch`、FIFO 消费或 drain。

**最小持久化与原子性：**当前 Return 仅增加 SCAN3/SCAN4 Command 引用和 `ready_evidence_id`，不新增队列表或业务状态；迁移 `71246b611dbf` 保留存量 8B 的事实与空方向引用，已有方向义务或资格事实时拒绝有损降级。batch 同锁创建必要方向命令并关联 Return；SCAN4 首次事实已给定时不补发已失去必要性的 SCAN3 动作，已有前段命令仍沿原身份履约。设备失败或未知不自动创建新命令身份。

**资格与顺序：**首次 SCAN4 仍只冻结物理入队顺序。精确匹配本 Return、本点原 Command 的权威 `SUCCESS`、宿主已应用终态和显式 OUTLET 后置条件后，资格依据与 Evidence 发布由 FactProcessor 同事务落地。SCAN3 成功不授予回架资格，ACK 不替代成功；重扫、重放不改首次时间。此处候选资格不等于 WMS `return_batch READY` 的精确分配。原始结果摘要复用宿主 `UNIFORM_WIRE` 规范化，不复制可靠接收/派发机制。

**恢复 owner：**现有 batch 前置钩子独立发现当前 Return 并创建缺失的必要方向动作，不依赖 EXECUTING、已闭合 Work 或一次性通知；停用线保留当前根，不新建方向动作。已建 Command 的交付、技术重试、回调接收与终态归设备基础服务。未发布结果唯一归 FactProcessor 的持久 claim/租约/延期扫描；缺 reader 延期，确定错配/失败留证对账，不授予资格。当前 Return、必要首次事实与原 Command 引用保留；可清理前段历史不参与恢复。

**测试所有权与截点：**插件 `T/test_return_directions.py` 拥有 typed 关联输入边界，`T/integration/test_return_directions_postgresql.py` 拥有方向创建/关联与资格/发布原子性、并发、崩溃、重放、回调早于 ACK、无 EXECUTING、存量 8B 及历史清理后的恢复；8A/8B 的测试继续拥有其原行为。核心仅增加精确迁移 selector fixture/mapping 并验收 schema。生产精确关联、方向映射和成功后置条件仍须其实际合同闭合；G2/G3/G5 的取盘/报告未决项不扩大阻塞正常 NO_WORK 离线回程。

**验证与闭环 Review：**本片实现与验证完成。新增方向 PostgreSQL 32 passed、8A/8B 回归 55 passed、插件 FAST 115 passed；核心 selector/测试所有权 249 passed、选中宿主 HEAVY 8 passed。QUALITY 通过（4315 passed、5 个既有 skipped，跳过项不计通过）。迁移验收覆盖存量 8B 事实保持、空引用降级后重升、已有方向义务时拒绝有损降级及模型/schema 一致性；插件与选中 HEAVY 无跳过项。独立只读 full Review 核对 11 文件冻结 SHA、实际恢复调用链和测试所有权，未发现确认的 P1/P2/P3 问题。最终可执行快照保持评审指纹，后续仅补本文验证记录。

下一片再接 FIFO 合格连续前缀、冻结 `return_batch` 请求/决定和 BIN_MOVE 交接，不能把本片候选资格当作 WMS 精确目标分配；同架面下一轮分配及运动前门禁仍按主合同另行验收。任务 8 整体仍未完成；生产 reader/mapper 未启用，本轮不提交、不部署、不启用真实派发或真实取盘。


#### 实施切片 8D：冻结回架前缀、分配结果与 BIN_MOVE 交接

**范围：**在 8C 已形成候选资格的前提下，精确 HANDOFF_POSITION、当前实际 rack/face 与到位 Evidence、权威运输结果均由离线 typed reader 显式给定。只接正常 FIFO 前缀、`return_batch` 与原 BIN_MOVE 的可靠业务关联，不启用生产 reader/mapper、实际派发或取盘，不接 drain 或完整换面/离场入口。

**最小当前进度：**增加插件私有 `AutomaticPickingReturnAllocation`，仅保存当前到位/架面、原 operation、请求时有序 Return execution ID、结果引用/选中数量、原 Transport 及交接/收口引用。完整冻结请求和响应仍只归 Confirmation/Evidence，动作正文归 Transport。Return 只增加当前 allocation 引用；ACCEPTED 交接同事务清除该引用并关闭 Return，allocation 保留标量成员身份与原 Transport，不以已关闭 Return 的 FK 或旧 Work/Feed 恢复后段。迁移 `a448148f9636` 保留存量 8C 的空分配引用，有分配进度时拒绝有损降级；部署只增加模型注册，不装配生产业务流程。

**FIFO 与原子性：**按首次 SCAN4 时间从当前待派发物理队首读取，只取资格已应用发布、精确位置已知的连续前缀，最多 4 箱；未知/未就绪项截断，不先过滤 READY 再越过它。已由原请求领取的成员不重复进入待派发范围，不能据此宣称物理 EMPTY。当前进度、成员领取与原 Confirmation 创建同事务；请求内顺序号从 1 连续冻结，重提不换身份或正文，新成员不追加到旧请求。结果由既有 typed reader 按原义务校验，READY/NO_BATCH 的分配关联、未选成员释放及发布同事务；NO_BATCH 结束本次到位决定，不按其 retry_after_ms 在同一次物理到位轮询。

同一次实际到位可在前一 READY 批次满足终态门禁后继续处理 FIFO 尾部，新请求使用新 operation；不能把 NO_BATCH 的到位结束限制扩大到 READY。每轮仅以 operation 唯一，在既有 WorkLine 锁下串行检查当前进度；下一轮请求、成员领取及已交接成功旧进度的归档同事务，不依赖已归档的分配历史。机会式/运动前服务阶段的结束条件仍由后续阶段入口负责，本片不增加阶段状态或入口。

**BIN_MOVE 与门禁：**READY 已应用发布、原实际架面仍匹配且原源位置确认后，复用 ReliableBinTransportCreator 创建和关联单次 BIN_MOVE；冻结目标与其他活跃 Bin 搬运的冲突检查使用既有目标事务锁身份，不建立储位主账或自行改址。Return 按原 Transport 的明确 ACCEPTED（或已证实的成功终态）交接，不伪造物理终位。同一 WorkLine/rack/face 下一轮请求仍等原 BIN_MOVE 的权威终态在宿主及插件发布、最终位置已知且成功终位匹配冻结目标；明确未接纳的 REJECTED 只解除该轮分配等待，不伪造 Return 交接或业务重试。未知/失败恢复保留原身份和直接依赖，未确认的异常业务决定不猜。独立已知架面可继续，另一个架面事实未发布不构成全线围栏。

**唯一恢复 owner：**当前分配和必要后继归原 batch 前置入口，无 EXECUTING、无新事件仍扫描；原 WMS 请求的收发与技术恢复归 Confirmation；WMS/Transport 结果的业务应用及发布归 FactProcessor；原 BIN_MOVE 履约、物理结果及位置投影归 Transport。自动插件结果发布器只新增固定回程分支并校验原 WorkLine binding，不把跨任务回程挂回某个已结束的 PickingTask。已闭合 Return 清理后，allocation 仍独立承接同面等待。已交接成功的旧进度可随同到位下一轮请求原子归档，或在已收口且权威读口确认到位轮次已替换/结束后退出扫描；NO_BATCH 当前到位结束标记只走后一出口。未知读口不退役该标记，未发布的独立架面只限制其本身。

**所有权与后续：**插件 `T/test_return_allocation.py` 验证输入边界，`T/integration/test_return_allocation_postgresql.py` 拥有前缀、冻结/应用/搬运/交接原子性、并发、重放、同面门禁与历史清理验收。8A–8C 测试继续承接已有行为，核心仅验收部署模型隔离、schema、selector/测试拓扑。G2/G3/G5 仍只限制对应取盘、扫码和报告生产路径；当前回程的生产关联、位置及架面读口另外闭合，fixture 不成为映射合同。后续补运动前无候选/原义务分支及完整换面离场、正常逐盘结束入口，再进入任务 9 drain。

**验证与闭环 Review：**本片实现及验收完成。最终新增 PostgreSQL 35 passed、8A–8C 回归 87 passed、插件 FAST 126 passed；核心聚焦 256 passed、选中宿主 HEAVY 18 passed。QUALITY 通过（4316 passed、5 个既有 skipped，跳过项不计通过）。迁移验收覆盖存量 8C 保持、空进度降级后重升、有分配进度时拒绝有损降级，以及模型/schema 一致性；插件与选中 HEAVY 无跳过项。独立只读 full Review 发现同到位 READY 尾部被错误永久阻断的 P2，新增两个 PostgreSQL 反例先失败后通过；删除过宽到位唯一约束，在同一 WorkLine 锁及事务下创建下一轮并归档已交接成功旧进度。原 Reviewer 合并完成旧意见闭环与当前 fresh full Review，计划退役出口文字也同步闭合，无剩余确认的 P1/P2/P3 问题。受影响完整验收及门禁已刷新，证据绑定最终可执行快照；后续仅补本文验证记录。本轮不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成。

#### 实施切片 8E：运动前求值的持久阶段与门禁

**范围：**复用 8D 的精确位置/实际架面读口、typed 结果应用与原 BIN_MOVE 恢复。运动前业务依据由离线 `MatchedReturnMotion` 显式给定，复用 `MatchedReturnFace`；不从扫码、任务状态或候选空查询推断运动意图，不创建旋转/离场 Transport，不接 drain、逐盘结束或生产 reader/mapper。

**最小当前进度：**插件私有 `AutomaticPickingReturnMotion` 仅保存 WorkLine、本次实际架面到位 Evidence、业务依据 Evidence、架/面、原 allocation execution 标量 ID、求值结束及归档时间；唯一到位/架面阶段只限制运动前服务，不给 8D 的所有 READY 加回到位唯一约束。无新授权模型、业务状态枚举或 timer。待处理阶段的原分配是当前依赖；阶段结束后仅凭本阶段事实抑制重开，不读取已关闭 allocation、Return、Work、Feed 或 Confirmation。迁移 `b1692d740313` 基于 `a448148f9636`，有阶段/出口事实时拒绝有损降级。

**先恢复后判断：**batch 原入口先推进 8D 可靠义务，再发现/创建当前运动前阶段。原请求未知、结果未应用/未发布、READY 未创建 BIN_MOVE、仅 ACCEPTED 或终位未知均保留等待；本架其他当前直接回架义务也须闭合。既有 NO_BATCH 必须已应用发布才可消费，不能跳过其他直接义务。READY 沿原 Transport 权威终态及已知成功目标收口；明确未接纳的 REJECTED 在结果发布后仅结束该等待，不关闭 Return、不伪造位置或新增业务重试。

**一次求值：**未产生本阶段请求时才在原义务检查之后读取当前 FIFO。顺序/资格/位置未知与真正无当前可派发候选分别返回；后者只结束求值，不声明物理 EMPTY。新请求、成员领取、Confirmation 与阶段关联同事务。阶段自己的 READY 所选成员及直接义务闭合即结束，未选尾部或后来候选不延长本阶段；此前独立批次完成后仍可进入尚未求值的运动前阶段。无运动前结束标记的 8D 正常路径保留同到位合法下一批能力。未知架面、空列表或其他独立架的事实不退役结束标记，确认同架新的权威到位后旧标记才退出当前扫描。

**恢复与测试所有权：**运动前阶段归同一个 batch 前置入口，缺新事件、缺 EXECUTING 或重启后未再次提供运动输入也能按当前记录恢复；结果仍归 FactProcessor，原请求/动作仍归 Confirmation/Transport。Work blocker/archiver 纳入未结束阶段；部署只注册模型，不启用生产业务。`T/test_return_motion.py` 拥有 typed 输入边界，`T/integration/test_return_before_motion_postgresql.py` 拥有阶段、原义务优先、并发/崩溃/重放及清理恢复；8D 与 8A–8C 继续承接原行为，核心只证明模型部署、schema 与 selector/测试拓扑。G2/G3/G5 及其他未决项仍只限制各自生产分支。

**验证与闭环 Review：**本片实现与离线验收完成。新增 PostgreSQL 26 passed、8D 回归 35 passed、8A–8C 回归 87 passed、插件 FAST 133 passed；核心聚焦 257 passed、选中宿主 HEAVY 18 passed。QUALITY 通过（4317 passed、5 个既有 skipped，跳过项不计通过）；中途终端脚本用例遇到一次系统 setpgid 权限瞬时错误，单项复查及随后完整 QUALITY 均通过，未修改该脚本或断言。迁移覆盖存量 8D 保持、空阶段降级后重升、有阶段事实时拒绝有损降级及模型/schema 一致性。独立只读 full Review 发现缺失本架事实也会退役结束标记的 P2，新增空列表/仅其他架两个 PostgreSQL 反例先失败，最小修复为同架新到位才退役；原 Reviewer 合并完成旧意见闭环与当前 fresh full Review，无剩余确认的 P1/P2/P3。格式化后的退役定向回归 3 passed；插件与选中 HEAVY 无跳过项。最终可执行快照与评审指纹一致，后续仅补本文验证记录。不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成，实际旋转/离场、原进场轮次的运动绑定及机会式入口留在后续切片。

#### 实施切片 8F：原进场轮次与来源结束绑定

**范围：**仅在显式给定原进场 client identity、来源 Feed 和服务结束 Evidence 的离线前提下，把 8E 阶段绑定到来源成员及原进场轮次。不创建 CTU02/CTU03，不接 drain、机会式入口或生产 reader，不新增运动授权模型或业务状态。

**绑定与身份：**现有阶段补原进场 client identity、原轮次起点/因果 token、原 Feed 标量和确定服务结束 Evidence。原 RACK 进场执行、工作位、架/面及成员必须匹配；最终空清单沿原 inbound_batch 结果，非空清单只接受末段已发布、明确成功且到达绑定 HANDOFF_POSITION 的原结果。跨 revision 保留成员独立，旧结束事实不能满足新 Feed；原 Feed 冻结请求不得早于本轮已精确关联的权威到位依据；动作创建时间不足以替代到位，已知失败且位置明确的原动作也不覆盖后续成功换面的当前事实。当前到位本身为原进场、原窗口尚未释放，或换面 binding 的直接来源精确指向已绑定该原轮次的阶段服务结束事实，才具备正向轮次依据；不能用中间历史缺失推断。已冻结阶段重放不得换绑。存量 8E 行不猜轮次，在显式精确输入下同事务补绑定；尚未明确结束的未绑定当前记录继续列入未闭合统计。

**范围与接管：**同轮换面不重置起点，窗口释放不等于新轮次。先恢复所有原可靠义务，再按本轮范围检查本架各面的直接回架与实际 RACK/BIN 动作；更早轮次不成为新轮次围栏。同架在途、未知、未发布及其它工作线已提交未 ACK 动作不能跳过；计划、reservation 和不再承接当前事实的已闭合历史不构成接管。采用现有对象 authority 锁与 WorkLine 事务边界。明确本线同架新到位后，尚未创建请求的旧阶段可退役；跨工作线接管则必须由更新因果的当前 Projection、精确成功且已发布的 Transport 与 Evidence 共同证明，已绑定旧阶段关闭并保留结束标记，未绑定存量阶段关闭后归档。旧架面重放及清理归档行后均先核对当前到位，不能重建旧请求；已有请求仍先由原 owner 收口。

**恢复与清理：**仍归现有 batch 前置入口，FactProcessor/Confirmation/Transport 所有权不变。已绑定阶段以当前锚点与确定事实恢复，不重新读取已清理 Feed、Confirmation 或原进场历史；当前到位及服务结束事实仍是本阶段当前依赖。8E 出口只结束求值，不产生运动许可，后续真正创建动作时仍需同事务重新校验当前事实及接管。

**测试与迁移：**8F PostgreSQL 拥有原轮次/服务结束绑定、并发/崩溃/重放、窗口释放及清理恢复；8E 保留原行为断言，只将夹具补为真实原进场和 Feed 事实；8D/8A–8C 承接既有行为。迁移 `20d588a3a234` 基于 `b1692d740313`，保留未绑定存量行，有绑定事实时拒绝有损降级。G2/G3/G5/G6 等未决项不由夹具补造。

**验证与闭环 Review：**本片离线实现与验收完成。最终 PostgreSQL 63 passed（8F 37、8E 26），8D 35、8A–8C 87 及插件 FAST 150 保持通过；新增迁移的存量保留、全空/全有约束、降级重升及有绑定时拒绝有损降级均已覆盖。QUALITY 通过（4317 passed、5 个既有 skipped，跳过项不计通过），选中宿主 HEAVY 8 passed，格式和差异检查通过。独立 Review 确认的三个 P2（同面旧轮次围栏、完整旧 Feed/end 误绑定、跨线接管旧阶段无出口）均先补失败反例后最小修复；另覆盖 root 早于旧请求、清理中间进场历史、原进场已知失败后成功换面以及存量阶段归档清理后旧输入重放。原 Reviewer 合并完成闭环与当前 fresh full Review，无剩余确认的 P1/P2/P3。最终可执行快照匹配评审指纹，后续仅补本文验证记录；主工作区 61 个既有改动、HEAD、暂存区及 Git 配置未漂移。不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成，CTU02/CTU03、机会式入口、生产 reader 与 drain 留待后续明确切片，G2/G3/G5/G6 未决项保留。

#### 实施切片 8G：普通来源架 CTU02 与下一面交接

**范围：**在精确到位、下一服务成员/架面、完整 `TransportRackPosition` 和权威结果显式给定的离线前提下，复用 8F 原轮次与 8E 已结束阶段，为普通来源架创建并关联唯一 CTU02。部署组合不安装生产 reader，不启用真实派发；不接 CTU03、drain 或机会式入口，不新增授权模型、业务表或业务状态，不抽取 bin-line-common。

**创建与结果：**调用方沿既有 WorkLine authority 事务，重验本架当前到位、原来源服务结束、本轮直接回架/BIN/RACK 义务及下一有效成员，沿原阶段唯一创建 Transport 并关联；窗口释放不重置轮次，未 ACK、未知或未发布义务不允许换身份。复用 ReliableRackTransportCreator 的 CTU02 与完整冻结正文；插件静态发布步骤接入现有 typed TransportOutcome、FactProcessor，不重建 HTTP/回调/outbox。回调早于 ACK 仍先保存原结果，再由 batch 核对匹配成功、当前权威位置与目标面，不能将 ACCEPTED 或数据库状态视为换面完成。

**持久交接：**现有阶段增加原旋转 Task 标量及结果 Evidence；下一面 Feed 同事务保存原进场 identity、起点/token、精确本次到位与成员独立 inbound_batch 请求，再归档上一阶段。下一次 8F 绑定从当前 Feed 接管，允许清理已闭合阶段、原 Feed/Confirmation/原进场 Transport，不回读已闭合历史。通用 batch 在原运动交接前不能旁路建 Feed，合法同面后续 revision 从当前 Feed/阶段承接同轮锚点。同架更新因果且明确成功的权威到位可终止旧阶段；计划、reservation 和未知结果不能构成接管。

**恢复 owner：**创建、原动作恢复和下一面事务交接归同一 batch 前置扫描，位于 EXECUTING 准入前；没有新输入或没有 EXECUTING 也继续接收原结果。物理原结果归 Transport，插件应用/发布归 FactProcessor，冻结请求归 Confirmation，下一面 Feed 归既有 batch。未知、未发布或不匹配结果保留原动作和当前阶段，停止受影响交接。旧结果重放不重开已交接阶段。

**测试与迁移：**插件 `test_source_rack_motion.py` 拥有显式 typed 输入；`integration/test_source_rack_motion_postgresql.py` 拥有唯一创建、原子关联、真实本地结果发布、并发/崩溃/重放、回调早于 ACK、跨 revision、窗口释放、同架接管、历史清理与下一面恢复。8F/8E、8D 及 8A–8C 继续证明各自原行为；核心仅证明模型部署/schema、selector 和测试所有权。迁移 `d061a18adea9` 基于 `20d588a3a234`，存量新增列保持空，有运动/交接锚点时拒绝有损降级。G2/G3/G5/G6、设备映射、扫码关联与 PkgID 未决项不由夹具补造，仍只阻塞各自真实分支。

**验证与闭环 Review：**本片离线实现与验收完成。最终 PostgreSQL 100 passed（8G 37、8F 37、8E 26），8D 35、8A–8C 87、直接受影响的 Feed/来源 revision 16 及插件 FAST 158 保持通过；核心 selector/测试拓扑 249、选中宿主 HEAVY 8 passed，最终 QUALITY 4317 passed、5 个既有 skipped（跳过项不计通过），格式与差异检查通过。迁移覆盖存量 8F 保留、完整锚点约束、降级重升、有运动时拒绝有损降级及模型/schema 一致性。真实本地发布路径发现 8F 额外要求 typed outcome 不含的 `kind`，最小修正为校验原 Transport 类型，保留原执行、摘要、成员及位置校验；另补通用 batch 交接前旁路门禁及当前轮次承接。最终专用 PostgreSQL 环境不配置 Redis；扩展执行的 3 个既有真实 worker 用例因缺 `INTEGRATION_REDIS_URL` 未完成，未改实现或断言、未计通过，直接影响集合独立重跑 16 passed、3 deselected；本片没有 worker/队列 wiring 变更。独立只读 full Review 核对 16 文件固定 SHA，无确认的 P1/P2/P3；对象锁疑点沿 `current_source → current_arrival` 复核后撤回，无须重复加锁或扩展模型。最终可执行快照与评审指纹一致，后续仅补本段验证记录。主工作区 61 个既有改动、HEAD、暂存区、依赖与 Git 配置保持不变；不提交、不部署、不启用真实派发或真实取盘。任务 8 整体仍未完成，CTU03、机会式入口、生产 reader 与 drain 留待后续切片，G2/G3/G5/G6 未决项继续保留。

#### 实施切片 8H：普通来源架 CTU03 的 READY 消费与离场交接

**范围：**仅在精确到位、原进场轮次、当前来源服务结束依据和已发布的原 departure READY 显式给定时，创建并接续普通来源架 CTU03。来源/drain 的 departure wire 是 WorkLine-owned、`task_id=null`；本片只实现普通来源架，不创建首次 departure 请求、不续问 WAIT 或执行拒绝后的业务重决策，不接 drain、生产 reader、真实派发或取盘，不新增授权模型、业务状态或 bin-line-common。

**身份与原子性：**沿既有阶段冻结原 departure operation、Transport 标量和结果 Evidence。原请求/响应须精确匹配 WorkLine、rack、当前物理 location/face、原 READY Evidence 和冻结目的地；按 `departure:<operation_id>` 创建静态 CTU03，创建、阶段关联及原窗口 departure 关联同事务提交。窗口已释放不重开，也不重置原轮次。当前阶段服务结束事实与原 READY 均不得早于其因果依据；本轮实际承接的未结束 Feed 仍阻止离场；当前阶段保留已应用发布的来源结束事实，不回读可清理 Feed。单纯计划成员不构成物理接管，不按缺失 Feed 否定结束事实；实际 RACK/BIN 在途/未知/未发布或接管仍重新核对。

**交接与恢复：**来源父任务终态不证明物理离场，最后换面阶段保留当前轮次给离场 owner；已交给下一面 Feed 的旧阶段仍按 8G 归档。离场冻结后通用 batch 不再为同架新成员发起投料。batch 前置扫描在 EXECUTING 查找之前恢复原阶段；Transport 及现有窗口服务拥有 ACK/物理结果/ACCEPTED 释放，FactProcessor 拥有插件结果应用/发布。接纳仅释放本地窗口，不关闭原物理义务、不推断 EMPTY 或 reservation 释放；精确已发布终态结果闭合原义务。未知和失败保留原执行，明确更新因果的权威同架接管只结束旧义务，不重开旧服务或重发物理动作。已归档结果重放不回查可清理 READY Confirmation。

**历史与测试所有权：**创建后恢复只依赖当前阶段、原离场 READY、原执行及其直接结果，不读已闭合 Feed/原进场历史。插件 `test_source_rack_departure.py` 拥有 typed 输入，`integration/test_source_rack_departure_postgresql.py` 拥有创建/关联/窗口交接/结果、并发/崩溃/重放、回调早于 ACK、跨 revision、同架接管、无 EXECUTING 与清理恢复。8G 仅修订父任务结束时的 owner 交接期望，其他业务断言保留；8F/8E 和直接 Feed 消费者回归。迁移 `43401479b2cc` 基于 `d061a18adea9`，只扩展既有阶段，保留存量空值并拒绝丢弃已冻结离场引用；核心仅承接模型/schema、精确 HEAVY mapping 和测试拓扑。

**后续边界：**首次 departure 决定、确定 WAIT 持久到期续问、明确 REJECTED 后的新决定，以及已接纳 FAILED 且仍在工作位而缺少权威面向的恢复留给后续切片；不将未知当失败或以计划面向补造。G2/G3/G5/G6 等未决值只限制对应生产分支，任务 8 整体及任务 9 drain 均未完成。

**验证与闭环 Review：**本片离线实现及验收完成。最终 8H PostgreSQL 44 passed（含迁移链、存量保留、模型/schema 一致性及有损降级拒绝），插件 FAST 167 passed；8E–8F 回归 63 passed、8G 回归 37 passed、直接 Feed 消费者离线回归 16 passed，未运行依赖真实 worker 的另外 3 项。核心聚焦 249 passed、精确 selector 选中宿主 HEAVY 8 passed，QUALITY 通过（4317 passed、5 个既有 skipped，跳过项不计通过）。schema 比对保留既有 Evidence/PickingTask 外键环警告，无新增迁移差异。独立只读 Review 发现创建前回读已闭合 Feed 历史、全取消来源失去离场出口两项 P2；三个反例先 RED，再按原 READY 与当前服务结束事实、实际本轮 Feed 因果范围修正，新增五类轮次/取消回归。原 Reviewer 合并完成旧意见闭环和最新完整 diff 的 fresh full Review，无剩余确认问题。验证绑定当前可执行快照，后续仅补本文记录；HEAD、暂存区、依赖文件及主工作区原有改动指纹保持不变。本轮不提交、不部署、不启用真实派发或真实取盘，任务 8 整体仍未完成。


#### 实施切片 8I：普通来源架首次 departure 决定与 WAIT 持久续问

**范围：**延续 8H 的显式精确到位、原进场轮次及全部当前来源服务结束依据；插件私有 matched 输入无既有 operation 时发起首次决定，有原 READY 时仍走 8H。生产服务结束候选 reader 不在本片装配，不以缺失 Feed、父任务完成或无 EXECUTING 任务推定服务结束。先收口当前阶段和直接物理义务，再创建 WorkLine-owned、`task_id=null` 的 typed departure 请求。未决设备映射、扫码关联、PkgID 和首批期限不设默认值；不接 drain、REJECTED 后业务重决策或已接纳 FAILED 面向恢复，不启用真实派发/取盘。

**最小持久进度：**复用既有阶段的 departure operation 游标，仅增加 `departure_retry_at`；无新授权模型、业务状态枚举或 timer。首次请求/原游标、到期新请求/游标替换分别同事务提交；迁移 `de97e0860fd5` 基于 `43401479b2cc`，允许 operation 先于 Transport 存在，仍拒绝孤立 deadline/result/Transport。存量 8H 引用不改写；请求期拒绝有损降级。请求未决、WAIT 与 READY 尚未创建 CTU03 均进入现有 batch 前置扫描，不要求再提供候选、设备事件或 EXECUTING 任务。

**结果与原子性：**基础 Confirmation 可靠保存响应后，固定插件 WMS consumer 经 FactProcessor 对精确当前 operation/原 Evidence/WorkLine/rack 做 typed 校验；WAIT Evidence 接收时间加 `retry_after_ms` 与结果发布同事务保存，重启/重放不重置。新增宿主 `RackDepartureResultReader.for_evidence` 仅读取原待应用响应，不拥有业务时机、时间或状态；原已发布 reader 仍禁止未发布响应驱动物理动作。WAIT 到期由 batch 在原 WorkLine/对象 authority 锁下重新核对当前游标、已发布原 WAIT 和当前事实，使用新 operation 与当时权威位置/面向创建请求；未知、RECONCILING、未发布或身份漂移不成为 WAIT，不换身份。正常未发布响应经精确 typed 校验后仅等待当前项，独立 Feed/回程等阶段仍可提交；身份和摘要冲突不被正常等待吞掉。READY 沿 8H 创建/关联唯一 CTU03；原结果重放不推进新游标，旧已闭合 WAIT/Feed/来源 Confirmation 可清理，不依赖历史最近响应。

**恢复与边界：**批处理仍先推进原回架/运动义务，再恢复 departure；请求期阶段即冻结来源服务，换面/通用退役/新 Feed 不再抢占同一阶段。计划或未来 revision 本身不延长停靠；当前本架在途/未知/未发布动作仍阻止新决定与运动，独立架不受统一阻断。已确认更新因果的实际接管结束旧本地阶段，保留原 Confirmation/Transport/Evidence 的可靠交付和物理结果，不恢复旧服务。窗口释放不重置原轮次。FactProcessor 只拥有当前响应应用/发布；业务到期新请求唯一归 batch；发送、原身份重提及物理接纳/结果沿现有 owner，不新增队列或 worker。

**测试所有权：**插件 typed 输入由原 FAST 文件承接；`integration/test_source_rack_departure_decision_postgresql.py` 拥有首次请求、当前游标、WAIT、FactProcessor 真实事务交接和应用/发布崩溃，以及并发/重放/跨 revision/轮次/接管/窗口释放/历史清理。8H 原孤立引用断言按本片请求期合同改为拒绝孤立 Transport，其他物理语义保留。宿主 FAST 和原生 PostgreSQL 仅验证 typed reader 的原身份与应用/发布读取边界；精确 HEAVY mapping 保留同文件 Scheduler 的真实 worker 派发验收，并增加 reader 原生 PostgreSQL owner；其他 20 个服务的原 HEAVY 集合保持不变。插件测试不进入核心 QUALITY/HEAVY。

**验证与闭环 Review：**本片离线实现及验收完成。最终 8I PostgreSQL 31 passed、8H 回归 44 passed、插件 FAST 168 passed；8E–8G 回归 100 passed、直接 Feed 消费者 16 passed，另外 3 个 Feed worker 用例未纳入本轮验收。核心聚焦 1176 passed；精确 selector 选中宿主 HEAVY 13 passed，包含 Scheduler 真实本地 worker 3 项、typed reader 原生 PostgreSQL 2 项和工作线配置/迁移 8 项。最终 QUALITY 4319 passed、5 个既有 skipped（跳过项不计通过），Ruff、格式与差异检查通过。迁移验证包含存量 8H 保留、模型/schema 一致性、完整约束和请求期有损降级拒绝；保留既有 Evidence/PickingTask 外键环警告。独立 Review 首轮发现正常未发布响应中断独立业务扫描、同文件 Scheduler HEAVY owner 遗漏两项 P2；分别以真实独立 Feed 和 selector 回归先 RED 后修正，再由同一 Reviewer 合并完成旧意见闭环与最新 21 文件 fresh full Review，无剩余确认问题。可执行文件保持评审指纹，后续仅补本段验证记录；HEAD、暂存区、Git 配置、依赖及主工作区 62 个既有改动指纹保持不变。测试仅使用独占临时 PostgreSQL/Redis 和本地 Mock WMS，不构成生产联调或物理验收；未暂存、未提交、未部署，不启用真实派发或真实取盘。生产候选 reader、drain 及 G2/G3/G5/G6 未决边界保留，任务 8 整体仍未完成。

#### 实施切片 8J：普通来源架真实候选发现与持久交接

**范围：**新增插件私有 `source_rack_candidates.py`，为现有当前架面、运动前来源绑定及首次 departure 三个读口提供真实 Repository 输入。当前面必须来自配置 FIVE_RACK 的精确 RACK 成员、原 Transport 成功且结果已发布、当前位置与因果匹配；识别普通 CTU01 与既有自动 CTU02 到位，不以最新历史响应、计划面向或空查询猜测。来源结束分别取精确 RACK_FACE_DONE，或非空投料最后分段的已发布权威交接；交接前当前面已接收且未取消的来源成员不能缺少完成依据。

**交接与恢复：**复用 8E 的运动前求值、8F 的原进场轮次/Feed/服务结束绑定及 8I 的 operation 游标。既有 batch 在 WorkLine 锁事务中发现、验证并保存阶段，满足门禁后同事务创建首次 departure；创建后由原阶段恢复，不依赖候选再次出现。当前阶段已经绑定时不因新 revision 重绑；冻结阶段之后的新成员不延长原停靠。既有合法未服务下一面不成为 departure 候选；CTU02 生产候选/参数 reader 不在本片新增。最后换面成功但没有下一 Feed 时，仍可用原阶段与真实当前面交给 8I，不能要求虚构 Feed。

**历史边界：**已有 Feed 与运动阶段分别增加 `completed_source_member_ids`，只承接同一任务/来源架已完成的不可变成员事实，不表示下一来源授权、当前面或物理位置。阶段绑定时同事务承接源 Feed 已继承的事实与仍存在的当前完成标记，CTU02 的下一面 Feed 再同事务继承；下一阶段沿该事实继续承接。A→B 交接后可清理 A Feed、Confirmation、旧阶段及原进场历史，B 阶段承接后也不再读取 B Feed/原 Confirmation；首次 departure 只核对当前阶段已承接的完成成员和当前合法未完成成员。缺失不是已完成；已完成成员不因新物理轮次重开，旧物理到位也不能证明本轮到位。迁移 `6680af0fe85b` 基于 `de97e0860fd5`，仅从仍存在的当前标记回填，不补造已丢失事实；活跃已绑定阶段的源 Feed 已清理，或活跃已交接 Feed 的前序同架已接收成员缺少当前标记、无法区分未服务和已清理时明确拒绝升级；标记完整的旧多面链正常回填。降级会丢失已交接且不再能从当前标记重建的完成依据时拒绝执行。仍被当前阶段直接引用的服务结束/到位 Evidence 不是可清理历史。

**所有权与未启用边界：**阶段与 departure 的业务恢复归现有 batch；结果应用/发布归 FactProcessor，原身份交付归 Confirmation，Command/Transport 沿现有基础恢复。不新增授权模型、业务状态、worker、timer 或业务共享层；生产组合根本轮不注入这些 flow，离线组合使用真实读口证明接续。扫码关联、方向/取盘/PUT 参数、PkgID、首批期限不猜；不接 departure 异常、drain 或真实派发。

**测试所有权与验收：**插件 `integration/test_source_rack_candidates_postgresql.py` 拥有真实发现、原子交接、并发、崩溃、跨 revision、窗口释放、同架接管、旧轮次及清理恢复，复用原 8F/8I owner 回归。插件业务测试不进入核心 QUALITY/HEAVY；新增迁移精确映射到宿主配置/迁移验收，核心承接 selector、拓扑和该 schema 路径。首轮独立 Review 发现多面历史依赖 P2，已用 A→B 清理反例先 RED 后修正，并补当前 B Feed 清理、有损降级拒绝及存量回填/schema 验收；闭环审查另发现普通 task 入口遗漏完成事实和存量源 Feed 清理后升级无出口两个 P2，已补真实 A1→B→A2 后 task advance 及旧库升级拒绝反例；共用准入同时覆盖 CTU01 等待阶段和 CTU02 换面后已继承完成事实；同一精确到位和 owner 优先读取已绑定 Motion 的完成事实，未绑定才回退 Feed，避免部分 Feed 清理后被旧列表遮蔽；新 revision 仍可接续；迁移覆盖 B 阶段创建前后，标记完整则回填、前序标记丢失则拒绝升级。最终验收：8J PostgreSQL 42 项、受影响投料/换面 PostgreSQL 47 项、插件 FAST 168 项、所选 HEAVY 8 项均通过；QUALITY 完整刷新通过（4319 passed、5 个既有 skipped），lint/format/diff 检查通过。3 项真实 worker 测试明确未运行，不计入通过。迁移覆盖干净库至 head、旧链回填/schema 对齐、缺失依据升级拒绝及有损降级保护。原 Reviewer 已合并完成旧意见闭环与当前完整审查，无剩余确认问题。保持未提交、未部署、未启用生产组合/真实派发；原工作区改动保持不动。

#### 实施切片 8K：普通来源架离场明确未接纳后的重新决定

**范围：**只接原 CTU03 Transport 明确 `REJECTED`、匹配结果已应用并发布、当前货架仍权威在原工作位且业务 owner/原轮次有效的离线路径。复用 Motion 当前 departure 引用及 8I/8H；不接已接纳后 `FAILED`、drain、生产装配或真实派发。

**事务与恢复：**batch 前置扫描从原 Motion 发现明确拒绝，核对原 Transport/READY/结果身份和摘要，再重验当前面向、来源服务结束及本架直接义务。新 departure 请求创建与 operation 游标替换、旧当前 Transport/result 引用退出在同一 WorkLine 锁事务提交；旧 Transport、请求正文及 Evidence 不改写。确定 WAIT 按原接收时间加 `retry_after_ms` 保存到期时间，未知沿原请求恢复；READY 用新决定创建唯一新 Transport。旧结果重放不覆盖新游标，也不复活已接管的旧轮次。没有候选、父任务完成或窗口释放不丢失原 owner。

**历史与边界：**源 Feed/原 Confirmation 已将必要事实交给 Motion 后可清理；当前服务结束和实际到位 Evidence 继续作为直接依据。活动进场窗口仍引用的拒绝 Transport 是当前依赖，保留至新 Transport 接管窗口引用后再清理；窗口已释放且新决定已接管时，旧离场记录可在新 READY 前清理。清理后新实例仍恢复当前请求和后续重新决定，不读历史最后响应。发送未知、仅 ACCEPTED、结果未发布、位置/面向未知和在途接管不产生替代动作；更新的权威实际接管按既有路径退役旧业务。未来 revision 本身不延长原停靠。

**所有权与验证：**业务重新决定继续归插件 batch/Motion，FactProcessor 负责原结果应用与发布，Confirmation/Transport 负责原身份可靠交付；不新增模型、业务状态、worker、timer、迁移或基础层业务规则。新增插件 PostgreSQL recovery 测试覆盖并发、事务崩溃、旧结果重放、跨 revision、窗口释放、同架/跨线接管、当前依赖及合法历史清理；复用 8H/8I/8J 回归。最终验证：新增 8K PostgreSQL 31 项，8H/8I/8J PostgreSQL 回归 117 项，共 148 项通过；插件 FAST 168 项、测试拓扑/所有权 14 项通过，Ruff、格式及 diff 检查通过，HEAVY selector 为 NONE。独立 Reviewer 完整闭环审查未确认 P1/P2/P3，无需代码修复。完整 QUALITY 由本轮提交 hook 执行；未决字段、设备/扫码映射、PkgID、首批期限保持原范围。本轮仅提交当前工作分支，不部署、不启用真实派发、不推进真实取盘。

#### 实施切片 9A：下一任务优先的 drain 决定与持久交接

**范围：**只实现 drain 首次准入、当前 operation/结果引用、确定 WAIT 到期续问和 READY 冻结交接；不创建 drain 货架 Transport，不接 reservation 执行或任务完成确认，不启用生产装配。当前插件未声明 RETURN_BUFFER 槽位，本片使用显式权威缓冲位置/容量及既有 MatchedReturnLocation 读口作为离线前提，不把 OUTLET 推断为退箱位，不生成生产默认值。

**准入与原子性：**现有 batch 前置入口在无 EXECUTING 任务时仍调用插件 drain owner。先按当前引用恢复未知请求、未发布结果或 READY，只有首次请求/确定 WAIT 到期才进入新决定准入；同 WorkLine 锁事务优先调用 prepare_next_in_session，claim、绑定及 prepare 义务原子提交。成功准备即交回既有 prepare 生命周期；只有明确 NO_ELIGIBLE_TASK 才继续核对本线当前 Work/Feed、未释放来源窗口及当前 FIFO。WORKLINE_NOT_READY 等原因不能当作无任务。FIFO 依既有首次 SCAN4 事实取序，已分配、未合格、位置未知的队头不可越过；权威缓冲位置/容量缺失不创建请求，数量超界明确拒绝，不截断冒充正确容量。

**当前进度与恢复：**新增插件 AutomaticPickingDrain 仅保存 WorkLine 当前 operation、响应 Evidence、WAIT 到期时间和冻结 Return ids；请求/完整响应及可靠交付仍只归 Confirmation/Evidence。新义务与当前引用创建/替换同事务，单线当前唯一索引为并发后盾；确定 WAIT 以原 Evidence 接收时间加 retry_after_ms 固定期限。未知沿原身份恢复，旧响应不能覆盖新游标；WAIT 到期无已确认需求或下一任务优先时退出当前等待，事实未知仍保留 owner。READY 保留原决定和原成员，新增候选不追加，独立任务可按既有 prepare 入口准备；本片不伪造 reservation 消费、物理完成或 EMPTY。

**恢复所有权与历史：**业务准入/WAIT/READY 继续归插件 batch，原结果应用与发布归 FactProcessor，技术交付归 Confirmation；未接 drain flow 时结果延期保留。新增当前进度计入业务停用/换绑 blocker，并接入现有显式归档。当前引用的请求、Evidence 和 READY 成员仍是直接依赖；旧 WAIT 引用退出、可清理前段 Work/Feed 删除后，新实例从当前进度恢复，不扫描最近历史响应或依赖已完成任务。没有新通用授权、timer、worker 或基础层业务规则，不导入人工 drain。

**测试所有权与验证：**插件 test_drain_flow 拥有输入与 batch 接线，integration/test_drain_progress_postgresql 拥有下一任务优先、当前引用、WAIT/READY、并发、崩溃、重放、无 EXECUTING、合法历史清理及本表迁移验收；插件 composition 测试保持静态 operation 集与默认未装配断言。核心只承接显式模型安装/过滤、拓扑、selector 及配置/schema HEAVY。最终验证：插件 PostgreSQL 43 项、受影响回归 41 项、插件 FAST 174 项、核心安装/拓扑/所有权 32 项、selector 合同 236 项及所选 HEAVY 18 项均通过；3 项既有真实 worker 用例明确未运行，不计通过。干净库降至父版本再升 head、schema/当前唯一索引及拒绝有损降级通过，Ruff/格式/diff 检查通过。首轮 Review 发现容量校验可被未知/未合格/已分配尾项提前返回绕过的 P2，已补三例真实 PostgreSQL 反例先 RED 后修正：每次确认成员加入前缀后立即校验原容量，不增加新规则；三例 GREEN、完整 43 项及 FAST 已刷新。同一 Reviewer 合并完成该意见闭环与当前完整审查，无剩余确认 P1/P2/P3。完整 QUALITY 由本轮提交 hook 执行。设备/扫码映射、PkgID、失败离场面向与首批期限仍按各自分支保留；本轮仅提交当前工作分支，不部署、不创建 drain 货架 Transport、不启用真实派发或真实取盘。

#### 实施切片 9B：drain CTU01 与到位持久交接

**范围：**在原 READY 已匹配、应用并发布，精确工作位、完整 CTU01 参数及权威结果显式给定的离线前提下，实现 reservation 每架唯一进场、精确绑定及首次到位交接。不接 return_batch、换面离场或 reservation 收口，不启用生产装配；不推断设备、扫码、RETURN_BUFFER 位置/容量或 PkgID。

**原子性与最小持久化：**新增 AutomaticPickingDrainRack 只关联 drain、rack、TransportDecisionBinding 和可空的到位 Evidence。每架执行与原 drain operation、READY、首个冻结面、目标工作位精确关联；Transport、窗口、Binding 与该执行引用同事务创建。复用 ReliableRackTransportCreator 和现有进场窗口，picking_task_id 为空。窗口 REUSED 不能代替本轮身份，旧同架窗口不被冒领；未知结果不创建替代动作。racks 是无序集合，一架缺参数或等待不阻塞其它可提交架，物理窗口仍由既有能力约束。

**到位与恢复 owner：**Transport 持续拥有原身份提交、技术重试及未知结果；插件静态 publisher 校验原 reservation 与动作，FactProcessor 校验结果身份/版本及完整冻结 outcome 并负责业务发布。batch 在没有 EXECUTING、通知丢失或重启时继续扫描原执行引用；先恢复原义务，再读取尚未创建架的显式参数。仅在匹配 SUCCEEDED、唯一成功 RACK 成员、精确目标/面和当前因果位置投影、宿主结果与业务 Evidence 均发布后，原子保存到位 Evidence。ACK/ACCEPTED 不代表到位；旧轮次/旧版本不能覆盖当前交接。单架到位可独立留给下一片，不等待整组到齐。

**历史与后续边界：**当前 drain READY/Confirmation、Binding、Transport 和到位 Evidence 仍是当前直接依赖，保留至后续 owner 接管；旧 WAIT 和已交接的前段 Work/Feed 可清理。恢复不查询最后历史响应、不创建虚构任务/Feed，不增加授权模型、业务状态、通用 timer 或基础层业务规则。下一片 9C 才将原冻结 Return ids 与实际到位架面接入普通 return_batch，9D 再处理冻结面顺序和离场；没有这些后续事实不关闭 drain 或释放 reservation。

**验证归属：**插件 integration/test_drain_inbound_postgresql.py 拥有唯一创建、原子回滚、并发、回调早于 ACK、结果重放、身份/内容漂移、多架独立推进、无 EXECUTING 与合法历史清理恢复；原 9A 和既有 Transport 分流路径做定向回归。核心只承接模型安装/过滤、测试拓扑/所有权、selector 与 schema/config HEAVY。新增迁移由 Alembic 生成 revision，支持干净库往返与拒绝丢失执行身份的降级；完整 QUALITY 留给后续明确授权提交的 hook。最终验证：9B PostgreSQL 33 项、9A 及旧 Transport 分流回归 49 项、插件 FAST 174 项、核心安装/拓扑/所有权/selector 269 项、所选 schema/config HEAVY 18 项通过；Ruff、格式和 diff 检查通过。迁移在隔离 PostgreSQL 的干净逻辑库执行，schema 比较无差异，拒绝丢失执行身份的降级；比较全局 metadata 时仍有既有循环外键 warning。本轮独立 Reviewer 核对固定 13 文件与日志并完成完整审查，未确认 P1/P2/P3，无需代码修正。未新增 worker、队列或 timer 接线，未运行真实 worker；不提交、不部署、不启用真实派发或真实取盘。

#### 实施切片 9C：原冻结成员回架与成功事实持久交接

**范围：**在精确到位、当前位置和权威结果显式给定的离线前提下，将原 READY 的冻结 Return ids 接入已有 return_batch、BIN_MOVE 和结果处理。不接换面、离场、补架或 drain 收口，不启用生产装配和真实派发；设备/扫码映射、RETURN_BUFFER 参数及 PkgID 仍按原分支保留。

**最小当前进度：**复用现有模型：Allocation 新增可空 drain_id，关联请求候选中原成员所属 drain；Drain 新增 returned_ids，仅保存已经精确匹配并发布的原成员回库成功事实。没有新通用模型、业务状态、授权或 timer，不复制 payload、储位或库存。普通架为原成员返料时同样关联原 owner，不把新候选扩充到旧 reservation。存量9B进度升级后成功事实为空，存量普通 Allocation 没有 drain 关联；不从旧历史猜测补齐。

**候选与事务：**9B当前进场、当前因果位置和已持久交接的到位身份共同决定 drain 架面；另一入口提供相同旧进场不能绕过重验，独立普通架面不被旧 reservation 当作资源围栏。drain 架只领取原范围内的合法 FIFO 连续前缀，未知、未合格、位置缺失、未交接领取及范围外队头不能被过滤跳过。原决定未知/未发布只保留其原成员直接依赖；确定 WAIT 不占有候选。请求创建、候选领取及 drain 关联同 WorkLine 事务提交；两入口共享原 Allocation/Confirmation/Transport，不建立第二套可靠机制。

**事实与恢复：**batch 先恢复原 Allocation，再发现新候选；FactProcessor 仍负责原请求/Transport结果应用与发布，Confirmation/Transport 沿冻结身份可靠恢复。BIN_MOVE 创建复用原 READY，ACCEPTED 只交接 Return，不计回库成功；settled、NO_BATCH、缺失或已归档 Return 也不是成功证明。位置 TARGET_PLACED 与原终态结果经宿主应用后，业务结果须匹配原请求、成员、目标、完整冻结 outcome 和发布版本；只把确定成功且位置明确的原成员写入 returned_ids，与本次推进同事务。部分成功先留存正事实，其余未知/失败仍保留原动作及依赖，不新建替代动作；当前领取尚未交接时不越过该队头。

**历史边界：**当前原请求、已分配成员和未闭合 Transport 仍保留。Return 接纳归档后可以删除其闭合源记录；Allocation 尚未完成时，其冻结 return_ids、请求和动作继续恢复成功结果。原成功事实交给 Drain 后，已整体闭合且没有其它当前引用的 Allocation/Transport/Confirmation 历史可清理，当前 drain 仍独立知道哪些原成员成功；不查询最后历史响应或凭没有记录推断完成。drain 本身不因本片成功而归档或释放 reservation。

**测试归属：**插件 integration/test_drain_return_postgresql.py 拥有原成员范围、两入口竞争、请求/动作/成功事实原子回滚、ACK/未知/未发布分类、部分成功、多架独立、同箱码新轮次、合法历史清理和本字段迁移验收。既有9B、回架分配及运动前门禁按直接调用回归；核心仅承接安装/拓扑/selector和所选配置/schema HEAVY。完整 QUALITY 留给后续获授权提交的 hook，本轮不提交、不部署、不启用真实派发或真实取盘。

**验证与闭环 Review：**9C PostgreSQL 31 项、9B 回归 33 项、其它聚焦回归 40 项、插件 FAST 174 项、核心安装/拓扑/selector 269 项、所选配置 HEAVY 8 项通过；40 项为定向选择，不代表相关领域全量验收。隔离 PostgreSQL 验证存量9B升级、干净库迁移往返、受影响表 schema 无差异及拒绝丢失当前归属/成功事实的降级；全局 metadata 比较仍有既有循环外键 warning。Ruff、格式和 diff 检查通过。独立 Reviewer 核对固定12文件及六组日志，完成完整审查，未确认 P1/P2/P3，无需修正；未改变已验证的可执行快照。恢复仍由原 batch/FactProcessor/Confirmation/Transport 承担，换面离场与 drain 收口留给后续切片，离线成功事实不代表现场业务已完成。

#### 实施切片 9D 首片：drain 冻结面序换面与到位交接

**范围：**在原 reservation、精确当前到位、完整冻结位置参数和权威结果显式给定的离线前提下，实现同架按 `rack_faces[]` 顺序的 CTU02 唯一创建、关联、结果消费及换面后向9C交接。不接 departure/CTU03、补架、异常自动重试或 drain 收口，不启用生产组合根、真实派发或取盘。

**最小持久接续：**复用 AutomaticPickingDrainRack 的 binding_id/arrival_evidence_id 保存本轮当前动作和已交接到位；首个 CTU01 的实际到位可交接给同 reservation 的当前 CTU02。新动作、当前 binding 替换和本面阶段出口同一 WorkLine 事务提交；新动作未确认成功发布前 arrival 引用为空。动作关联固定原 READY Evidence、drain operation、rack 和冻结面序号；位置沿本轮已经权威匹配的冻结工作位，不新增 WMS 字段或推断设备映射。没有新增表、字段、业务状态或迁移。

**门禁与恢复：**复用既有 AutomaticPickingReturnMotion 表达每面运动前求值，原 return_batch/分配及同架直接义务先恢复，未知、未发布或 READY 已分配未闭合均不按无候选处理。只在明确接入本片后，由 batch 在回架求值前准备 drain 面阶段，不伪造 Feed、来源时间或来源服务结束；本阶段的新请求继续由原回架 owner 原子关联。接入前的独立 READY 批次先恢复，不能直接代替本阶段最终求值；其闭合后仍按当前 FIFO 求值。普通9C同面分批能力保留。原义务闭合或确定无候选后冻结阶段，后到候选不重开。新面来自对应 CTU02 的权威成功、唯一 RACK 成员、当前因果位置/面及宿主和业务发布，不要求新投影继续匹配旧 CTU01；旧结果只留证，不覆盖当前动作。两入口仍通过原 Allocation/Confirmation 领取防重，独立架不等待其它架动作。当前架存在更新的未闭合动作或已被新因果事实接管时不创建本轮换面。

**唯一 owner 与历史：**插件 batch 的原 drain/inbound 入口先扫描当前动作并恢复已发布到位，再推进冻结面序。Transport 负责原动作提交和技术恢复，FactProcessor 负责原结果应用/发布；不增加 worker、timer 或通用授权机制。当前 READY 和当前动作/未闭合分配仍是直接依赖；成功交接后，未被其它当前对象引用的旧面阶段和已闭合分配可清理，恢复不查询最后历史响应。仍被宿主活动窗口引用的原对象继续保留。无 EXECUTING 或新通知也可恢复原义务；终面停在9C回架，不创建离场或归档 drain。

**测试所有权：**插件 integration/test_drain_rotation_postgresql.py 拥有冻结面序、创建/关联/到位事务、并发、重放、ACK时序、未知/发布门禁、两入口、多架、无EXECUTING和合法历史清理。9B/9C、回架分配/运动前阶段和普通来源运动/离场直接消费者定向回归；核心只验证安装和测试拓扑，HEAVY 按最终 selector 清单执行。完整 QUALITY 留后续获授权提交 hook；未决值继续按原分支保留。

**验证与闭环 Review：**本片28项与9C31项 PostgreSQL 最终联合验收59 passed；9B33项和普通回架/运动前及来源分流65项定向回归通过，合计157项不同 PostgreSQL 用例。末次修正只影响 drain 原范围外队头分支，按 Reviewer 核对的覆盖路径复用9B及普通域证据，并完整刷新本片/9C。插件 FAST174项、核心安装/拓扑33项通过，HEAVY selector 为 NONE（无 schema、基础层或核心测试资产变化）；Ruff/格式/diff检查通过。既有全局 metadata 循环外键 warning 保留，受影响 schema 比较无差异。

首轮两项P2及闭环追加一项P2均以真实事务反例先RED后GREEN：未知普通架面读口保留旧分配时不得用空来源时间；接入前独立READY不能替代本阶段最终求值；原成员已回库后，范围外新FIFO队头不得留住旧reservation架面。最终求值归原ReturnBatchFlow，明确范围外队头只结束本范围求值，不跳队领取或改写成功事实。原成员未知、未合格、未交接依旧等待。同一独立Reviewer合并完成完整审查、意见闭环及最终差异核对，无剩余确认P1/P2/P3。当前12文件可执行快照保持固定，无暂存/提交/部署；独占测试环境已清理。离场、drain收口及现场/真实worker验收不在本片完成结论内。

#### 实施切片 9D 后续片：终面求值与显式 departure READY 的 CTU03 交接

**范围：**原 drain READY、精确当前到位、完整参数及已发布 departure READY 显式给定的离线路径。终面同样先准备运动前阶段，再由原 ReturnBatchFlow 恢复原义务并完成一次求值；未接本片时维持9D首片的终面边界。不接首次 departure、WAIT续问、拒绝后重新决定、离场失败重试、补架或 drain 收口，不启用生产装配或真实派发。

**身份与事务：**显式输入只关联原 drain id、rack、当前到位 Evidence 和 departure operation；已有 Motion 的 departure operation/Transport/result 引用承接原执行。终面阶段已结束、本架所有原分配及直接义务已闭合、当前权威架面仍匹配且未被更新动作接管后，才消费精确 WorkLine-owned、task_id=null 的原 READY。READY 请求不得早于当前到位及本次阶段结束，目的地原样采用 WMS rack_destination，固定 CTU03；Transport、原 READY Binding、Motion 引用及原进场窗口 departure 关联同事务提交。不伪造来源 Feed、服务结束 Evidence 或来源轮次时间，不新增表、字段、业务状态、migration 或通用授权。

**交接与恢复 owner：**显式安装本片后，原 batch 前置入口先扫描持久 Motion 的原离场引用，再准备架面求值，最后按原 drain 推进新动作；恢复不需要新候选、EXECUTING任务或新通知，停用后仍可收口已建动作。Transport 保持原身份提交及技术恢复，FactProcessor 应用并发布匹配的原结果。ACCEPTED仅释放本地进场窗口，不代表物理成功、drain完成或WMS reservation释放；本架未闭合结果不统一阻塞其他架。只有原成功终态、唯一RACK成员、已知精确终位、宿主和业务发布均闭合后，才归档该面离场阶段；ZONE目标保留WMS原目标，实际成功终位仍须为明确RACK_POSITION。明确非成功或未知结果保留原阶段，不创建替代动作。

**所有权与历史：**普通来源 departure 扫描仅领取有来源绑定的Motion，drain结果使用独立静态step沿冻结Binding接入，不按task_id=null猜业务owner。原离场的READY、当前Motion/Binding/Transport及其结果仍为直接依赖；已交接且无当前引用的中间面阶段、分配和动作可清理，恢复不查询最后历史响应或已清理来源Feed。终面冻结后后到FIFO不重开阶段，不扩展原return_ids，原成员未全部回库也不冒充drain完成。

**测试所有权：**插件 integration/test_drain_departure_postgresql.py 拥有终面求值、原READY身份/发布门禁、创建和关联原子性、并发、崩溃、接纳与回调时序、多架、旧轮次/接管、无EXECUTING、停用、结果重放及合法历史清理。9B/9C/9D首片和普通来源departure/WAIT/明确未接纳路径做定向回归；核心只承接安装/拓扑，HEAVY执行最终selector清单。未决设备映射、扫码关联、PkgID和首批期限保持原阻塞范围；完整QUALITY留后续获授权提交hook。

**验证与闭环 Review：**本片29项、9D首片28项、9C31项及9B33项 PostgreSQL 联合验收121 passed；普通来源departure/WAIT/明确未接纳三域106 passed，合计227项不同用例。插件FAST174项、核心安装/拓扑33项通过；HEAVY selector为NONE，9个Python文件Ruff/格式及diff检查通过。既有全局metadata循环外键warning保留，未改schema。终面缺阶段与有READY未建CTU03均先取得真实RED再GREEN；开发回归中一项断言误将未业务发布视为完成、一项历史清理fixture缺一次持久扫描，分别按合同和既有owner补齐前置，未放宽生产门禁。独立Reviewer核对固定10文件及完整调用链、事务、对象锁、测试所有权和历史边界，未确认P1/P2/P3，无需代码修正；最终可执行快照保持不变。仅完成本片离线验收，不提交、不部署，不接首次departure、WAIT续问或drain收口。

#### 实施切片 9E：drain 首次 departure 与确定 WAIT 持久续问

**范围与身份：**沿用9D终面阶段、当前权威到位和原冻结drain reservation，所有直接回库义务闭合后才创建首次 typed departure 请求。Confirmation与Motion的当前 `departure_operation_id` 同事务落地；后续结果必须匹配原WorkLine、rack、当前operation、请求正文和响应Evidence。普通来源以来源绑定领取，drain以原READY关联领取，不能仅凭 `task_id=null` 选择owner。未扩展字段、模型、业务状态、通用timer、SDK或宿主业务逻辑。

**WAIT与READY：**确定WAIT的到期时间固定为原Evidence的 `received_at + retry_after_ms`，FactProcessor同事务应用业务结果和发布；已发布结果也可由持久扫描补记到期时间。到期后重新核验当前原架面、终面阶段及直接义务，以新operation和替换当前游标同事务续问。未知、对账或未发布结果保留原身份；重放旧结果不延长期限、不覆盖新游标。departure义务不受FIFO为空、无EXECUTING或下一任务准备结果取消。READY沿当前持久游标接回9D的CTU03创建、关联和结果恢复，不需要重提供显式READY输入。

**恢复owner与历史：**原batch入口先恢复operation-only和已建Transport，再推进新候选；Confirmation负责原身份交付，FactProcessor负责精确typed结果应用/发布，Transport继续负责原执行。当前drain根/READY、Motion、当前Confirmation及原动作仍是直接依赖；旧WAIT及已交接的闭合历史可清理。停用线遵循既有宿主领取规则，结果保留至恢复活动；不以停用制造新动作。没有通知、没有EXECUTING任务、双worker竞争或事务中断均不另建owner。

**测试所有权与边界：**插件 `integration/test_drain_departure_decision_postgresql.py` 承接首次请求、当前游标、WAIT期限、续问原子性、结果发布、READY接回、并发/崩溃/旧结果、来源owner隔离、多架独立、无EXECUTING及历史清理恢复。9C/9D和普通来源departure三域做定向回归，核心只验证安装/拓扑。不接drain收口或失败重决策，不启用生产装配或真实派发；设备映射、扫码关联、PkgID及首批期限等未决项保持原阻塞范围。

**验证与闭环 Review：**新增25项、9C/9D回归88项及普通来源departure三域106项 PostgreSQL 验收全部通过，合计219项不同用例；插件FAST174项、核心安装/拓扑33项通过。HEAVY selector为NONE，5个Python文件Ruff/格式及diff检查通过，无schema变化；既有metadata循环外键warning保留。首次请求和WAIT游标/发布先取得真实RED再GREEN。开发验证纠正离线时钟早于权威到位的fixture，并按SRS及宿主既有规则修正停用线测试前提，未放宽生产门禁。独立Reviewer核对固定6文件、事务/锁、恢复链、owner隔离、测试所有权与历史边界，未确认P1/P2/P3，无需代码修正；其后仅补齐回归结果和本文验收记录。完整QUALITY留后续获授权提交hook；本片不提交、不部署，不接drain收口或失败重决策。

## 变更面与文件归属

下表为计划内目标。路径缩写仅用于后续任务：`A=src/app/wms_adapter/outbound_picking/`，
`S=src/wes_plugin_sdk/src/wes_plugin_sdk/`，`P=workline_plugins/automatic-picking/src/automatic_picking/`，
`T=workline_plugins/automatic-picking/tests/`；任务 4–5 的文件已按下方进度创建，其余仍为计划路径。

| 区域 | 当前路径 / 计划新增路径 | 唯一职责 |
| --- | --- | --- |
| 出库 wire/转换/示例 | `A/{work_plan,material_decide,source_empty,movement_report}_{wire,typed}.py`、对应已有 adapter、`A/openapi.py` | 固定协议、类型转换及示例，四个 API 各在自己的所属能力内实现 |
| SDK | `S/wms_types.py`、`wms_operations.py`、`facts.py`、`decisions.py`、`__init__.py` | 不可变 intent/outcome、固定 facade 与封闭基础 Decision/Fact 合同 |
| 共享物料接线 | `src/app/execution/services/{fact_builder,decision_applier,fact_processor}.py`、`src/app/wms_adapter/confirmation_adapter.py` | 固定 typed 类型与已有可靠执行端口的接线，不拥有 AUTO 工作顺序 |
| Work 阶段 typed 接线 | 新建 `src/app/wms_integration/outbound_picking/services/bin_work_plan.py`、所属 `services/__init__.py`、现有 Confirmation/Task Repository 及 `deployment/plugin_composition.py` | 固定创建/读取端口，按原请求身份校验并返回 typed 值；不读取插件 Work 表、不判断业务重问 |
| 插件声明 | 包根 `pyproject.toml`、`P/{__init__,definition,prepare_policy}.py` | 独立安装包、静态声明及准备策略 |
| 插件应用协调 | `P/application/{plugin,flow,batch_driver,completion_driver}.py` | 既有插件端口实现、事务协调和当前业务扫描 |
| 插件业务与存储 | `P/handlers/`；`P/application/{work_model,work_repository,pickup_model,pickup_repository,task_confirmation_model,task_confirmation_repository}.py` | 当前轮次、扫码前关联、授权及任务确认进度；随任务 5–7 增加 |
| 插件回程与排空 | `P/application/{return_model,return_repository,return_flow,drain_model,drain_repository,drain_flow,drain_inbound,drain_inbound_repository,drain_return_repository}.py` | 本线回程 FIFO 和当前排空进度；随任务 8–9 增加 |
| 部署关联 | `deployment/{plugin_definitions,plugin_models,plugin_composition}.py`、根 `pyproject.toml`、`uv.lock` | 安装来源、静态组合、模型注册/过滤，不放工作流判断 |
| 迁移/验证归属 | Alembic 生成的 `migrations/versions/` 新文件、`migrations/env.py` 的既有接线、`docs/architecture/heavy-test-impact.toml` | 每个新增模型的持久化及精确核心 HEAVY 映射；插件测试不进入 selector |
| 生成合同 | `scripts/export_release_provider.py` 的现有导出入口及实际受影响消费方 | 生成当前 provider 快照，不手改生成物、不为本任务扩大前端交互范围 |

同一文件和执行路径由主 Agent 顺序维护。实施前补齐 SDK 导出、fixture、mock、QA 示例、直接/间接消费者 manifest，
机械传播一次列全；不以测试失败列表代替调用点枚举。公开 typed 变化触及的宿主、插件及工具消费者都须同批迁移。

## 实施切片

### 任务 0：固定执行范围和证据

**文件：**只读上述设计、合同、目标源文件、测试、HEAVY mapping 和 `AGENTS.md`；本任务不改代码。

- [x] 保存 HEAD、三份既有 dirty 文档及其他现场的路径/指纹，执行前核对设计 SHA；只对目标重叠变化读取 diff。
- [x] 为任务 1–3 冻结实际生产符号、调用点、摘要/规范化、fixture、测试 owner、生成物和 HEAVY 范围；后续插件切片在各自开始前补齐。
- [x] 对计划修改的生产函数/类先做 GitNexus upstream impact；缓存同一 base/意图的结果，HIGH/CRITICAL 先说明影响再在授权范围内继续。
- [x] 核对索引是否 stale；仅必要时分析一次并保护 Agent 入口。确认 FAST 收集和依赖，不为得到 skip 先跑名义集成测试。

**恢复 owner：**无新增持久对象。**验收：**变更 manifest、未决项与测试所有权可追溯；未改用户现场，不把计划记录当实施证据。

### 任务 1：对齐三条 RACK_SLOT 请求的 revision 身份

**依赖：**任务 0。**文件：**`A/{material_decide,source_empty,movement_report}_{wire,typed}.py`；`S/{wms_types,wms_operations,__init__}.py`；`A/openapi.py`。
**测试：**已有 `tests/contracts/wms_adapter/outbound_picking/{test_material_decide,test_source_empty,test_movement_report}.py`，以及 SDK 对应消费者测试。
**接口：**沿用 `outbound_material_decide`、`outbound_source_empty_decide`、`outbound_material_movement_report` 的具名方法及原返回 intent，增加条件 `plan_revision`。

- [x] RED：RACK_SLOT 缺少、null、bool、非正或超过合同整数范围的 revision 拒绝；不同 revision 正文不同，同身份内容漂移冲突。
- [x] 同批更新 wire、SDK、facade、converter、OpenAPI 请求示例和调用点；BIN_CELL 保持合同规定的请求形态，不额外发送 revision。
- [x] GREEN：确认从 facade 到冻结 JSON 的 revision 无丢失，物料决定、空取、上报均引用原成员版本；技术重试不换身份。
- [x] 运行三个目标测试文件及受影响 SDK 测试，检查目标 API 的旧调用残留；source.empty 仅对齐协议，不实现自动空取动作。

**恢复 owner：**沿用 WmsConfirmation，operation 身份及错误恢复不变。**验收：**三条接口同步、无只有 wire 有字段而 typed converter 丢失的情况。

### 任务 2：一次性替换单 Cell、扫码原文及目标/后继结构

**依赖：**任务 1；对应严格值域受 G1 约束。**文件：**`A/{work_plan,material_decide}_{wire,typed}.py`、对应 adapter、`A/openapi.py`；`S/{wms_types,wms_operations,__init__}.py`。
**测试：**已有 `test_work_plan.py`、`test_material_decide.py`、受影响 SDK/fixture，以及 `tests/api/test_qa_regression_transport_openapi.py`。
**接口：**保留 `BinWorkPlanReady`、`PickingMaterialIntent/Accept` 和固定 facade 名，按目标合同替换字段；`encode_request(intent, *, timestamp)`、`decode_outcome(payload)` 保持各 operation 独立归属。

- [x] RED：READY 单 Cell 的完整字段、原样 barcode、目标层列、后继对象/省略、DirectPick 无后继、ROTATE/REPLACE 以及 WAIT/REJECT 条件字段。
- [x] 替换 `cell_ids[]`、出库 `PickingSixInOne`、枚举型后继；目标层列使用出库目标 typed locator，不污染 plan_delta、来源 slot 或 movement_report 的独立 locator 合同。
- [x] 字段缺失/类型/禁止 null 按已确认表校验；未知长度、数值类型或映射不拿样例定型，不增 permissive parser、默认值或兼容分支。
- [x] 同批迁移 SDK 导出、四条转换链的所有受影响消费者、OpenAPI 业务响应示例与 QA fixture；原始字符串不裁剪、不拆条码，不解析 PkgID。
- [x] GREEN 后运行受影响出库合同领域、SDK 及 API OpenAPI QA；限定范围扫描旧字段/旧类型/旧后继枚举，确认旧路径和失去用途的 helper/import 退出。

**恢复 owner：**WMS 交付仍归 Confirmation，确定业务 WAIT 后的重问留给插件；Adapter 不加循环。
**验收：**DTO、SDK、converter、OpenAPI 和测试在同一最终快照一致；G1 未闭合的子项只报告未完成，不能宣布整个合同对齐。
入库自己的 `SixInOne` 及 REJECT.source_disposition 的 CONTINUE/CLOSE 保持独立语义，禁止无边界全仓替换。

### 任务 3：补齐 Work 与单盘的共享 typed 接线

**依赖：**任务 0 与任务 1–2 中本路径实际使用的合同；3A 不依赖 3B，接口轮廓可先落定，所用 DTO 未闭合时不宣称完整接线验收。
**文件：**3A 新建 `src/app/wms_integration/outbound_picking/services/bin_work_plan.py`，同步所属导出、Repository 必要方法及 deployment 注入；3B 修改 `S/{facts,decisions,wms_types}.py`、`src/app/execution/services/{fact_builder,decision_applier,fact_processor}.py`，实际需要时修改固定 confirmation adapter 接线。
**测试：**3A 新建 `tests/contracts/wms_adapter/outbound_picking/{test_work_plan_scheduler,test_work_plan_reader}.py`，扩展已有 `tests/integration/wms_adapter/outbound_picking/test_work_plan_production_wiring.py`；3B 新建 SDK `test_outbound_picking_facts.py`，复用核心 `tests/runtime/execution/{test_fact_builder,test_decision_applier,test_fact_processor,test_wms_confirmation_dispatch}.py`。

**3A：无 MaterialExecution 的 Work 请求与结果端口。**沿现有所属 operation service 模式新增固定 scheduler/reader，直接复用 Confirmation 生命周期；不把 work_plan 塞进单盘 Fact/Decision 路径。

- [x] RED：只有 PickingTask 和本次 SCAN2 上下文时，从 `BinWorkPlanIntent` 创建唯一义务，严格 converter 保留原请求；重复创建重放原身份，内容漂移冲突。
- [x] `BinWorkPlanScheduler.create_in_session(db, intent, *, picking_task_id, created_at)` 冻结原身份、timestamp、正文及 deadline；Confirmation 唯一 owner 为 PickingTask，Work 保存 operation 引用，不再同时设置 material_execution_id 或 workline_id owner。
- [x] `BinWorkPlanResultReader.by_operation_id(db, *, picking_task_id, operation_id)` 通过既有 Repository 按冻结身份取请求/响应，校验 operation、任务归属、摘要及 response Evidence 关联，返回不可变 typed intent、状态、Evidence 引用及 outcome；未决时 outcome 为空，不读“最近一条”。确定结果须匹配 COMPLETED Confirmation、原 response_evidence_id 及 APPLIED 的 WMS_RESULT；业务发布是后续应用结果，不能反过来成为首次读取条件。
- [x] reader 返回 typed intent 的 task/bin/scanned_at，供任务 5 插件核对当前 Work 并在可靠应用条件满足后推进；本任务不提前实现插件。reader 不读插件表，不解释 READY 后取盘、NO_WORK 放行或 WAIT 到期重问；无需新增通用 envelope 或另一套可靠表。
- [x] GREEN：FAST 用中性 fake 覆盖身份不匹配及原请求读取；扩展已有真实接线测试验证零插件可交付、无 MaterialExecution、三种结果可靠保存且 WAIT 不自动重问。任务 5 再证明结果应用和 Work 同事务关联；部署注入后才接业务。

**3B：已有 MaterialExecution 的物料 Fact/Decision。**扩展 `WmsResultReadyFact.outcome` 封闭集合及 Decision 接纳的正常出库 intent，复用所属 converter。

- [x] RED：从已保存的 material.decide/movement_report Evidence 构造 typed Fact，经静态 handler 与 DecisionApplier 创建唯一可靠义务；身份不匹配不得推进。
- [x] 让 FactBuilder 的固定出库 operation 分支调用所属 decoder，DecisionApplier 对已批准 intent 调用所属 encoder；私有静态选择不导出 generic call/裸 envelope 构造入口。
- [x] 同步封闭联合、构造校验、摘要 discriminator 及直接/间接测试；只补必需的类型接线，不增加 Cell、双臂、WAIT 重问或完成判断。
- [x] GREEN：核心使用中性 typed fake 验证事务、摘要、原身份重放及旧入库路径；插件业务事实增强仍由其 fact_factory/具名 reader 拥有。

**恢复 owner：**3A 的原请求由 Confirmation 派发/保存；Work 当前引用由任务 5 的插件 batch owner 周期发现，reader 只是读取端口，不是第二个恢复 owner。3B 的 Evidence 由 FactProcessor 领取/重放，Command/Transport 各自恢复。
**验收：**Work 和单盘两条链分别可达；创建 Work 请求不要求先有扫码或单盘，重启后可从当前 Work 的原 operation 引用继续；核心/SDK 不导入 automatic-picking，不依赖插件测试证明机制。

### 任务 4：独立插件包与静态装配

**依赖：**任务 0 与现有插件 SPI；包声明/静态装配不等待任务 1–3 全部验收，具体业务端口随其调用路径的合同及接线验收后注入。**新增：**插件包 `pyproject.toml`、`P/{__init__,definition}.py`、`P/application/plugin.py`、`T/test_definition.py`、`T/test_plugin_composition.py`。
**修改：**根 `pyproject.toml`、`uv.lock`、三个 `deployment/plugin_*.py` 文件；复用既有 installed-plugin/runtime-binding 合同。
**接口：**`DEFINITION` 的 `plugin_key="automatic-picking"`、`supported_line_types=("AUTO",)`；包版本在本切片冻结单一初始值，不随宿主运行动态选择。

- [x] RED：声明可独立导入，静态运行关联身份一致，空插件/人工单装/自动单装/双插件组合各自正确，重复和未知插件 fail closed。
- [x] 增加独立安装来源与 optional dependency，设备角色和位置声明只采用已批准语义；实际设备码、线号与位置取绑定，不从 KT11 示例写死。
- [x] 沿现有部署入口关联声明和逐步接入的运行对象；没有 handler 的合法事件只留证，不创建成功占位或自动消费历史观察。
- [x] GREEN：仅运行目标插件声明/组合 FAST；依赖配置变化后按仓库要求初始化环境和同步依赖，不复用其他 worktree 环境。

**恢复 owner：**尚无业务接管时基础服务继续保存结果。**验收：**独立安装/装配成立，不声称业务可执行，不引入新框架或人工业务依赖。

### 任务 5：任务准备、来源投料与工作位 Work

**依赖：**任务 4；投料使用既有 typed 端口，Work 请求/应用另依赖任务 3A 及其 DTO，首批超期路径受 G6 约束；三部分分开记录验收，不等待任务 3B 或 G2/G3/G5。**新增：**`P/prepare_policy.py`、`P/handlers/picking_task_plan_applied.py`；
`P/application/{work_model,work_repository,task_confirmation_model,task_confirmation_repository,batch_driver,completion_driver,flow}.py`。
**测试：**新增 `T/{test_prepare_and_plan,test_feed_flow,test_work_admission,test_task_confirmation}.py` 及 `T/integration/test_work_and_task_progress_postgresql.py`。
**接口：**`AutomaticPickingBatchDriver.advance_in_session(db, line, task)`、`advance_completed_in_session(db, line)`；`AutomaticPickingCompletionDriver.advance_in_session(db, line, task)`。
应用入口使用现有 `AsyncSession`、WorkLine、PickingTask；任务锁内调用同一插件完成协调器，返回本次推进计数，不执行网络 I/O。

- [x] 补齐跨 revision 复用当前物理进场而保持成员独立的插件验收，范围与出口见切片 5A；不受 G6 阻塞。
- [ ] G6 闭合后验收 PREPARING/revision 0 前置入口、期限重启不重置、计划先到和旧确认迟到。
- [ ] 首批确认分支待 G6 闭合后随片增加必要进度、Repository、迁移并接入同一个 completion driver；普通完成分支若具备本地前提可先实施，不为未决期限预建空进度。首批期限归插件唯一配置入口，不借派发 deadline 或新通用 timer 表，不重复创建已交付的 Work/Feed/FeedScan。
- [x] 实现插件计划准入/应用、来源到位后一次 inbound_batch、完整有序清单最多 4 箱分段及 feed_complete；直接依赖保留，独立架到位可推进。
- [x] 实现 SCAN2 实扫准入及 work_plan：Work 与任务 3A scheduler 创建的原请求引用同事务保存，经 reader 核对 typed 结果后同事务更新当前阶段；缺 SCAN1/乱序/来源取消后已入线按合同处理，同轮重扫不建新 Work，非预期箱只留证。
- [x] 在既有静态业务 Evidence 消费端口接入 work_plan；消费者与 batch driver 调用同一事务应用逻辑，按当前 Work/原请求防重。事件应用后发布与丢通知后的周期推进均不走单盘 FactBuilder，不等任务 6 才提供业务入口。
- [x] GREEN：READY 保存当前 Work 授权、NO_WORK 保存明确业务结束与点2交接、WAIT 保存原决定/到期时间并创建唯一新请求；未知沿原请求恢复。

**本轮进度（2026-10-16）：** 首批超期、任务确认进度及 completion driver 目前尚未实施，对应项保持未勾选；首批分支等待 G6 的期限值与合法范围，普通完成分支按其当前本地前提单独验收。跨 revision 来源接续已由切片 5A 的插件 PostgreSQL 测试闭合，未改生产代码。普通 prepare 复用宿主入口。已创建 Work、Feed、FeedScan 三表及迁移，FeedScan 只保存当前成员入线/已准入标记，避免依赖 CLOSED Work 历史。未另加重复的计划接收规则，继续使用共享严格 DTO 和启用时位置绑定校验。SCAN1/SCAN2 用户确认沿用 `A`＋9 位数字及 `-B`/`-C`；仅 SCAN1 已确认正常前进接线，未接异常分流或真实取盘。
`READY` 保存授权；`EXIT_PENDING` 保存明确 NO_WORK 与点2待交接；实际放行/回程属于任务 8。`FEED_COMPLETE` 由末段已发布的确定物理交接产生，不等 SCAN；中间分段才等本段实扫。Transport 结果接收器复用公共发布链；取消后乱序扫码沿原 Evidence 暂缓恢复。终态 Feed 退出投料扫描，原结果发布且成员交给 Work 后退出准入/阻塞统计，仍保留当前来源完成标记；不把可清理 Confirmation/CLOSED Work 作为运行依据。后续换面/离场门禁尚不属于本轮完整交付。当前状态、测试所有权和验收证据见[任务 4–5 实施记录](../../integration/2026-10-16-automatic-picking-task4-5-implementation.md)。不得将此进度宣称为整项任务 5 或整线业务闭环已完成。

**恢复 owner：**prepare/plan 通知由宿主入口驱动；首批超期与确认归插件 completion owner，经 batch 前置钩子在无 EXECUTING 任务时调用；Work/来源阶段归插件 batch owner。
**验收：**任务不能永久停在准备状态；投箱与 Work 可从持久记录恢复；新增表随片注册、迁移并通过干净临时库验证，不新增 Bin 库存主账。

### 任务 6：取盘、扫码与 ACCEPT 授权交接

**依赖：**任务 5 的 Work/来源授权与任务 3B；生产取盘受 G1/G2/G3/G5 的对应项约束，不扩大到任务 4、投料或 NO_WORK 路径。**新增：**`P/application/{pickup_model,pickup_repository,fact_factory}.py`、`P/handlers/material_flow.py`；扩展本插件 `flow.py` 与 batch driver。
**测试：**新增 `T/{test_pickup_flow,test_source_authorization,test_material_decisions}.py`、`T/integration/test_pickup_identity_postgresql.py`。
**接口：**`AutomaticPickingFlow.apply_in_session(db: AsyncSession, evidence_id: int, *, workline_id: int) -> BusinessEvidenceApplication`；fact_factory 实现现有 PluginFactFactory，返回不可变插件 Fact。

- [ ] RED：同 Cell 多盘 LIFO、同箱新轮次、扫码/来源结果/ACK 乱序、重复扫码、缺关联、并发消费同一来源授权及当前栈顶未知。
- [ ] 在一事务内冻结扫码前 Pickup、来源授权和 Command；关联成功扫码后经 MaterialExecution 服务创建单盘身份，原 Command 不改身份或正文。
- [ ] 使用宿主提供的具名 typed reader/facade 发起 material.decide，确定 WAIT 与未知请求分开恢复；应用 ACCEPT 时保存本盘目标并原子把后继授权交给 Work，省略则保存明确无后继。
- [ ] 消费 Work 授权时同事务创建/复用下一 Pickup/Command 并记录消费关联；旧 Evidence 重放不覆盖新授权，不查已闭合 M1 恢复 M2。
- [ ] GREEN：纯 Decision 与事务测试分别证明授权和幂等；未决设备参数只作为明确标记的测试输入，不实现生产 fallback，不用 fixture 宣称设备合同成立。

**恢复 owner：**Command 归设备基础服务；扫码汇合、当前栈顶及待消费 Work 授权归插件业务 driver；MaterialExecution Evidence 由 FactProcessor 调用冻结插件。
**验收：**后继创建在崩溃前后均至多一次，已闭合记录不成为运行依赖；G2/G3/G5 未闭合时不启用不可逆取盘。

### 任务 7：目标准备、PUT、位置报告与双臂并发

**依赖：**任务 6；完整生产闭环受 G3/G5 约束。**新增：**`P/application/target_preparation.py`；扩展本插件 Pickup、flow、material handler 和 composition。
**测试：**新增 `T/{test_target_preparation,test_placement_report,test_arm_concurrency}.py`、`T/integration/test_material_close_postgresql.py`。
**接口：**消费任务 2 的 ACCEPT typed outcome，经现有 ReliableRackTransportCreator、DeviceCommandService、WmsConfirmationService 创建对象，不新增外部 operation。

**当前离线进度：**7A 普通目标已就绪后的 PUT 创建、7B 确定位置/报告义务、7C 报告确认后的关闭与 Work 续接已分别验收。以下复选框仍代表完整任务（含其余目标准备/来源分支与生产合同准入），不把本轮离线通过视为任务 7 整体完成。实际关闭验收 owner 为 `T/integration/test_material_close_postgresql.py`。

- [ ] RED：普通目标、ROTATE、REPLACE 顺序、目标未知、PUT 回调早于 ACK，以及 M1 报告先确认但 M2 尚未创建时重启。
- [ ] 按被冻结目标和当前权威位置准备货架；REPLACE 执行 departure 决定→旧架离场→新架进场→PUT，已确定物料资格不重问。
- [ ] 匹配成功 PUT 后保存位置并同事务形成报告义务；PkgID 只来自 G5 已批准来源，不从本地 trace 或 barcode 推导。
- [ ] 仅报告 RECORDED/DUPLICATE 且授权交接完成后闭合 M1；M2 不必已创建，当前 Work 继续拥有授权；RACK_SLOT 无 Work 交接条件。
- [ ] GREEN：可靠取出且获后继授权即可与 M1 PUT/报告并行下发 M2；未知栈顶不越过，无平台锁、释放事件或跨臂统一等待。

**恢复 owner：**目标准备 Transport、PUT Command、报告 Confirmation 各由原基础对象恢复；插件 driver 根据其 typed 事实推进当前盘。
**验收：**本盘物理完成、WMS 记录、后继授权三个条件分别可追溯；真实接口闭环等待 G5，测试中的权威 PkgID 输入不构成合同修订。

### 任务 8：正常流出、回程 FIFO 与回架分配门禁

**依赖：**NO_WORK 入口只依赖任务 5 的 Work/点2交接、既有方向命令和回架 typed 端口；正常逐盘结束入口另依赖任务 6–7 的明确无后继与本轮逐盘收口。回程/FIFO/运动前门禁可独立验证，不等待全量取盘或 G5。**新增：**`P/application/{return_model,return_repository,return_flow}.py`、`P/handlers/bin_flow.py`；扩展 Work、batch driver 和部署模型注册。
**测试：**新增 `T/{test_bin_release,test_return_fifo,test_return_before_motion}.py`、`T/integration/test_returns_postgresql.py`。
**接口：**复用现有 BIN_MOVE、return_batch 与 departure typed 能力；回程 execution 为本插件私有，不导入人工 BinLineReturn。

- [ ] RED：NO_WORK/明确无后继后流出、首次 SCAN3 交接、SCAN4 首次物理时间排序、重复扫码、未知队头及同架面连续两轮分配。
- [ ] 随片增加回程记录和迁移；Work 业务结束与物理交接分开，READY 候选取连续前缀，未入线/位置未知不加入 FIFO。
- [ ] 将运动前阶段区分真正无候选、已应用发布 NO_BATCH、READY 分配、未知/未发布；先检查本轮原义务再判断候选，不能从空查询推断可离场。
- [ ] Return 可按明确 ACCEPTED 交接，但位置及同架面下一轮分配继续等待已发布权威终态和已知终位；本架直接 BIN_MOVE 未收口不换面/离场。
- [ ] GREEN：运动前求值一次，冻结后新候选不重开；其他独立架/面仍可推进，恢复不扩大为全线围栏；单独证明 NO_WORK→流出→回程不读取 PkgID、不创建虚构单盘、不等待取盘/PUT。

**恢复 owner：**回程与当前分配归插件 batch driver，已建请求/Transport/Command 归基础机制。**验收：**未知不当 EMPTY/NO_BATCH，首次 FIFO 顺序及原请求身份跨重启不变。

### 任务 9：跨任务 drain 与完成确认收口

**依赖：**任务 8 的当前 FIFO/回程能力、任务 5 的确认进度与 prepare 协调，不要求任务 6–7 或 DirectPick 设备路径先全部验收。实际任务完成仍逐项等待其必报事实。**新增：**`P/application/{drain_model,drain_repository,drain_flow}.py`；扩展 batch/completion driver 与插件 composition。
**测试：**新增 `T/{test_drain_flow,test_completion_flow,test_recovery_owners}.py`、`T/integration/test_drain_progress_postgresql.py`。
**接口：**drain 使用已有 workline-owned facade，完成确认使用 PickingTaskCompletionScheduler；下一任务准入调用 `PickingTaskPrepareCoordinator.prepare_next_in_session(db, line, now=now)`，注入插件应用层，不新建任务、claim 模型或 reservation API。

- [ ] RED：父任务完成且无新事件时 WAIT 到期、并发扫描、候选数量变化、无需求退出、READY 原范围与新任务并存；另覆盖可 claim 的 QUEUED 与 drain 同时推进、已有 PREPARING/EXECUTING、prepare 被阻断和整个准入事务崩溃。
- [ ] 新建 drain 或确定 WAIT 到期重问前，由 batch owner 在同一 WorkLine 锁事务内先核对当前 drain。原请求未决或 READY 时沿原引用恢复，不重问、不覆盖或释放 reservation；只有准备创建新决定时进入下一任务优先准入。
- [ ] 同锁内已有 PREPARING/EXECUTING 时不创建新 drain；否则先调用既有 prepare 协调器，claim、绑定、prepare 义务在当前事务原子提交。成功准备任务即退出本次 drain 准入，由下一任务的后续当前架承接 FIFO，不复制人工 drain 的容量窗口逻辑。
- [ ] 不能把 `prepared=False` 当成“无任务”：仅明确 `NO_ELIGIBLE_TASK` 且当前 FIFO 非空、其他准入成立时创建新 drain；WORKLINE_NOT_READY 等结果保留原因并退出本次扫描，由既有持久准备入口恢复，不越过候选自行创建决定。
- [ ] 随片保存 WorkLine 当前 drain 请求/决定、到期时间及冻结回程成员；WAIT 关闭原决定。到期无候选或下一任务优先时结束本次等待；只有重新通过上述准入时，才同事务以新 operation_id 创建一个当前请求并替换引用。
- [ ] READY 沿原 reservation 和原成员推进，新增候选不扩展原范围；WMS 决定容量及释放，插件不计算库存/容量，也不按最近历史响应恢复。
- [ ] 完成确认只看当前业务义务及已确认必报事实，保留 revision 0、STALE、BUSINESS_IN_PROGRESS 分支；任务完成不替代 Rack/Bin 清场。
- [ ] GREEN：通过前置 batch 钩子恢复已完成任务遗留义务，未闭合时阻止停用/换绑；不实现停线/切换触发 drain。

**恢复 owner：**drain/回程及新决定准入归插件 batch owner；它在事务内调用宿主 prepare 协调器，已 claim 的任务交回既有 prepare 生命周期，不另建恢复队列。确认业务时机归 completion owner，已建义务归基础派发。
**验收：**可准备任务与新 drain 不会在竞争中同时获准；WAIT 到期同样优先下一任务，原未知/READY 不被该判断覆盖；提交前崩溃全部回滚，提交后丢通知由对应持久入口恢复。每个非终态有当前记录、唯一 owner 和扫描入口，不新增通用 timer、整线 Session 或手工续行接口。

### 任务 10：直接取料、取消与 revision 范围完成

**依赖：**取消/范围完成依赖任务 4–5 的计划事实、成员关联及既有接收；设备动作复用任务 6–7 的 Pickup/单盘链并受 G4 约束，不要求任务 8–9 全部完成。**新增：**`P/application/direct_pick_flow.py`；扩展本插件 handler、flow、completion/batch driver。
**测试：**新增 `T/{test_direct_pick_flow,test_cancellation,test_return_rack_completion}.py`、`T/integration/test_direct_pick_progress_postgresql.py`。
**接口：**沿用现有 DirectPick 成员、arrival_report、direct_pick_completed、return_rack.task_completed 的基础接收及 typed reader；不导入人工结果消费者。

- [ ] RED：同架跨 revision 成员、单成员取消、through_plan_revision 范围完成、完成后追加、迟到结果与新的因果位置。
- [ ] 为有效直接成员复用 Pickup/MaterialExecution 正常路径，来源臂竞争时直接取料优先，不抢占已建动作、不停止无冲突 CTU/Bin 流；不创建虚构 Work。
- [ ] 到位报告独立可靠交付，整架完成先等连续版本齐全，只关闭覆盖范围，不补造逐面证据；取消停止无依据新动作，已发生事实和已有报告继续收口。
- [ ] GREEN：直接取料不会等待不存在的 Work 授权交接，不使用父任务状态代替成员取消事实；G4 未闭合只阻塞该设备分支，单独报告验收状态。

**恢复 owner：**范围完成 Evidence 延期由 FactProcessor 重领，业务关联由自动插件消费；直接取料及离场归插件 driver，物理对象沿原身份履约。
**验收：**旧 revision 不关闭新成员，原到位生命周期可复用，新轮次不依赖可清理历史；设备映射确认后才允许实际取盘。

### 任务 11：最终持久化、worker 与正常闭环验收

**依赖：**目标分支所需任务与未决项闭合。**新增：**`T/integration/test_worker_recovery.py`、`T/e2e/test_normal_picking.py`；核心有状态测试只补其新基础机制的主要 owner。
**范围：**全量需求对照、固定最终 diff/环境指纹，不扩大到未获批 NG/空取或供应商私有协议。

- [ ] 独占临时 PostgreSQL，逐片迁移按最终链在干净逻辑库完成规定 base→head；原模型、Alembic 注册/过滤和约束一致。
- [ ] 启动真实 Redis/worker/Beat 验证既有队列、序列化、提交后唤醒和周期扫描；逐个等待点验证重启/丢通知/租约回收，不以 skipped 计通过。
- [ ] 端到端覆盖来源架→投箱→SCAN2→work_plan→取盘/扫码→decide→PUT/report→后继→回程→完成，含 NO_WORK、WAIT、ROTATE、REPLACE。
- [ ] 在授权交接、请求创建、发送/结果保存、投影应用等边界注入中断，验证全部 Review Focus 及清理整体闭合历史后恢复新任务。
- [ ] 最终一次 full Review 同时闭合反馈和当前 diff；运行当前快照必选门禁，已有有效证据不重复。协议、本地 Mock、真实 worker 和供应商物理验收分开报告。

**恢复 owner：**逐项核对设计第 7 节，无“等消息”而没有持久查询依据的状态。**验收：**Bin 与 DirectPick 分别报告已实现、聚焦验证、持久化/worker、WMS 及物理验收，不把正常模拟闭环写成整线现场完成。

## 验证命令、门禁与交付证据

以下均为实施时运行的命令，本计划未执行这些测试。各切片先跑 Files/Test 所列的最小文件集，目标输出是相关断言通过且无非预期 skip。

```bash
# 合同首轮只跑变更文件；公共传播完成后再跑一次所需领域
rtk uv run pytest tests/contracts/wms_adapter/outbound_picking/test_material_decide.py tests/contracts/wms_adapter/outbound_picking/test_source_empty.py tests/contracts/wms_adapter/outbound_picking/test_movement_report.py -q
rtk uv run pytest tests/contracts/wms_adapter/outbound_picking/ src/wes_plugin_sdk/tests/ tests/api/test_qa_regression_transport_openapi.py -q
# 新插件 FAST 使用本切片明确文件，勿递归误收 integration/e2e
rtk uv run pytest workline_plugins/automatic-picking/tests/test_source_authorization.py workline_plugins/automatic-picking/tests/test_arm_concurrency.py -q
# provider 由现有入口导出；检查实际 schema/示例及受影响消费方
rtk uv run python scripts/export_release_provider.py --out-dir reports/automatic-picking/provider
rtk git diff --check
```

插件新增的 PostgreSQL/worker/E2E 测试只有在显式启用、数据库/Redis/worker 就绪时运行其精确文件；环境契约沿现有项目约定核对，不臆造环境变量。
Transport 专用 `docs/contracts/openapi/wes-wms-transport.openapi.json` 不因出库业务示例更新而重写；若对应 builder 真有变化，再生成并验证它。
前端若确有生成合同消费者则同步其实际生成物与校验，不自行增加页面或界面改造；没有影响时记录 NONE 的证据。

- [ ] 每个生产符号编辑前执行 upstream impact；每个迁移由 `uv run alembic revision -m "中文说明"` 生成随机 revision，不手写编号。
- [ ] 每片新表都同步 deployment 模型注册/过滤、迁移与相关测试资产；共享机器合同/核心模块补精确 HEAVY mapping，插件测试保持独立。
- [ ] 本地按实际差异使用 unstaged selector；最终含 Commit 时用 staged selector，仅执行输出 manifest，NONE 有效，不默认跑全量 HEAVY。
- [ ] 交付目标包含 Commit 时由 hook 产生完整 QUALITY，不使用 --no-verify；只有授权 Commit/Ship 才进入暂存、提交和后续动作。
- [ ] 验证证据记录命令、结果、执行树/环境指纹及日志；代码或环境变化只刷新失效部分。Review 不重复有状态门禁。
- [ ] 最终扫描限定范围的 `cell_ids`、`PickingSixInOne`、旧后继枚举、兼容导出及孤儿 helper；核对每个残留的所属语义，而非按关键词全仓删除。

## 需求覆盖与执行边界

| 设计要求 | 主实施切片 | 交付前必须具备 |
| --- | --- | --- |
| 已确认 WMS 合同与完整消费者替换 | 1–3 | 规范化、typed 转换、OpenAPI/fixture 一致及旧路径残留闭合；Work 的 3A 与单盘 3B 分开验收 |
| 独立声明、职责分离、渐进接线 | 4 | 四种静态组合，核心/SDK 不反向依赖插件 |
| 准备、投料、Work、revision 0 恢复 | 3A、5 | 无单盘时可创建/读取原 work_plan，当前进度、前置可达入口、唯一可靠请求及迁移验证 |
| Pickup、LIFO、单盘身份、原子授权交接 | 6–7 | 当前 Work 持有授权，M1 可独立闭合，崩溃重放不重复动作 |
| 目标与双臂并发、必报事实 | 7 | G3/G5 证据、匹配 PUT、原报告身份及并发直接依赖 |
| 回程、运动前求值、同面分配门禁 | 8 | FIFO、未知依赖和确定无分配分开；NO_WORK 回程不依赖 PkgID/逐盘闭环 |
| 跨任务 drain、业务完成与物理清场分离 | 9 | 新决定前同锁优先准备下一任务，WAIT 到期有 owner，READY 原范围、完成后扫描可恢复 |
| DirectPick、取消、范围整架完成 | 10 | G4 对应证据、成员身份及迟到事实保护 |
| 每个非终态的确定 owner、清理历史后可运行 | 5–11 | 插件与核心各自的持久恢复证据，不以测试绿灯替代物理事实 |

本计划推荐主 Agent 在同一执行现场顺序推进，避免 SDK、converter、共享接线和插件 driver 的交叉修改。
当前交付止于计划文件与文档校验；计划中的生产文件、迁移和测试均尚未创建或执行，后续实施按用户明确的交付目标启动。
