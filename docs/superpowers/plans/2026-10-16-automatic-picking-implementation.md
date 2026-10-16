# 自动拣料插件实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans` 按切片执行，使用复选框记录进度。推荐主 Agent 顺序实施；本计划不授权自动派生 Subagent、Commit、Push、Merge 或 Deploy。

**Goal:** 先完整对齐已确认的 WMS DTO、SDK、typed converter、OpenAPI 与测试，再交付独立 `automatic-picking` 插件的正常业务闭环及其持久恢复。

**Architecture:** 宿主提供 typed operation、可靠接收、执行、派发和重放，自动插件拥有当前业务授权、工作轮次、Cell 循环、目标准备及完成判断。复用既有插件端口和扫描入口，不抽取 `bin-line-common`，不导入人工插件业务实现。

**Tech Stack:** Python 3.13、现有 Pydantic/SQLModel/SQLAlchemy、PostgreSQL、Celery、pytest、`uv`；不增加依赖来替代既有基础能力。

**Spec:** [修订后的自动拣料插件设计](../specs/2026-09-22-automatic-picking-plugin-system-design.md)，SHA-256：`00fefedde6ed530dc2435a99b36845704df98c081c7a40506b9f3232b5777189`。

**状态与基线:** 2026-10-17，原基线 `develop / d38cced4201e0a09e46ceff5fe7b9f84dce20f13`。任务 1–3 已提交为 `e558fe0b`；任务 4、任务 5 已确认路径及五项 Review 修订提交为 `2da3990c`；任务 5A 验收及计划更新已在 `codex/automatic-picking-workflow` 提交为 `26deca33`。本轮推进任务 6A 的纯规则和离线事务验收，不提交、不部署、不推进真实取盘；G6 首批确认及任务 6 的生产持久化、设备/扫码接线仍未实施，不改变外部合同状态。
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

以下按已提交基线及后续 5A/6A 的明确验收范围核对，不把基础端口已有、单项测试通过或本计划的复选框当成整项业务已完成。

| 路径 | 当前事实与归属 | 继续条件 |
| --- | --- | --- |
| 任务 5 普通 prepare、投料、SCAN2/Work | 已实现并提交，Work 的 READY/WAIT/NO_WORK、结果应用/发布及持久恢复已有验收 | 不受 G2/G3/G5/G6 阻塞；后续只补本业务缺口，不重复实现共享可靠机制 |
| 任务 5 跨 revision 来源接续 | 切片 5A 的 6 项 PostgreSQL 验收已通过：当前进场复用原 Transport，各 revision 成员独立申请，重启/并发扫描防重、事务回滚、取消及闭合历史清理均已覆盖；未改生产代码 | 本项已闭合，不依赖首批期限或机械臂参数；首批确认和任务 6 按各自条件继续 |
| 任务 5 首批等待与 revision 0 确认 | 宿主在查找 EXECUTING 前已有 batch 钩子；自动插件目前只恢复 Feed/Work，尚无任务确认进度、配置入口或 completion driver | G6 需给出期限值、单位、合法范围及具体配置落点；不得借 30 秒技术派发期限、重启时间或 WMS 重试间隔代替 |
| 任务 5 正常完成确认的共用协调器 | completion_confirm 基础合同/调度可用，自动插件的业务发起与重问进度尚未接入 | 首批分支受 G6 限制；普通完成分支不因 G6 一概停工，但必须具备可证明的当前本地完成谓词。涉及逐盘报告、Work 放行及回程的完整验收随任务 7–9 闭合，不把空查询当成完成 |
| 任务 6 已确认纯业务规则 | 6A 已实现来源规则与显式注入端口的事务协调器，并完成离线验收；生产 Work 仍仅保存初始 authorization_evidence_id，未新增生产 Pickup 表或消费/交接持久端口 | LIFO、原身份、授权归 Work、旧 Evidence 防覆盖及双臂独立条件已验证；中性 typed fake 与 SQLite 证明协调器的原子性/至多一次，不代表 PostgreSQL 行锁、持久适配或设备生产接线已验收 |
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
| 插件回程与排空 | `P/application/{return_model,return_repository,return_flow,drain_model,drain_repository,drain_flow}.py` | 本线回程 FIFO 和当前排空进度；随任务 8–9 增加 |
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
**测试：**新增 `T/{test_target_preparation,test_placement_report,test_arm_concurrency}.py`、`T/integration/test_material_closure_postgresql.py`。
**接口：**消费任务 2 的 ACCEPT typed outcome，经现有 ReliableRackTransportCreator、DeviceCommandService、WmsConfirmationService 创建对象，不新增外部 operation。

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
