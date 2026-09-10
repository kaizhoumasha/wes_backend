# 配置与固定约束索引

本索引覆盖当前 WMS Operation 及其宿主、工作线关联配置，帮助维护人员定位调整入口；不复制默认值，也不作为配置加载源。
默认值与校验以所属代码入口为准，业务含义及约束以当前合同为准。调整前先区分部署参数、实现策略和固定合同。

## 1. 已有配置入口

| 调整内容 | 唯一所属入口 | 生效方式与边界 |
| --- | --- | --- |
| EVENT_DEBUG 命令接入地址 | [Settings](../../src/core/conf.py) 的 `DEVICE_EVENT_DEBUG_ENDPOINT_BASE_URL` | 重启使用该配置的 API/worker 后生效；新建命令使用既有设备地址校验并冻结，旧命令不变。Docker 本机开发编排指向 ECS Mock |
| WMS 地址、Transport 提交路由 | [Settings](../../src/core/conf.py) 的 `WMS_BASE_URL`、`TRANSPORT_SUBMIT_PATH` | 环境提供值，启动时校验并冻结；重启使用它们的 API/worker 进程后生效。合法形式见 [Transport 合同](../contracts/transport-fulfillment-contract.md) |
| Transport 结果等待窗口 | [Settings](../../src/core/conf.py) 的 `TRANSPORT_RESULT_TIMEOUT_SECONDS` | 默认值与合法范围以入口为准；重启 API/worker 后影响首次接纳时新冻结的期限，已保存 deadline 不变；现场路线适配单独验收 |
| WMS 诊断保留时间、条数、单条字节和单次预算 | [DiagnosticsConfig](../../src/app/wms_diagnostics/config.py)；环境变量前缀 `WMS_DIAGNOSTICS_`，字段为 `RETENTION_HOURS`、`MAX_RECORDS`、`MAX_RECORD_BYTES`、`BUDGET_MS` | 默认值和范围只由该入口定义。环境变量优先于运行时 `.env`，修改后重启 API/worker；记录只用于联调观察，不是可靠业务证据 |
| 启用的已安装插件 | [Settings](../../src/core/conf.py) 的 `ENABLED_WORKLINE_PLUGINS`；[部署关联](../../deployment/plugin_composition.py) | 由部署显式关联并在启动时生效；不能通过配置自动安装插件或绕过工作线切换检查 |
| 基础 START 的设备状态时效、命令超时 | [Settings](../../src/core/conf.py) 的 `WORKLINE_DEVICE_STATUS_MAX_AGE_MS`、`WORKLINE_DEVICE_COMMAND_TIMEOUT_MS` | 重启使用该配置的 API/worker 后生效；下次无业务启动计划的 START 冻结到设备合同，既有合同不变；默认值与合法范围以入口为准 |
| 工作线插件配置、设备角色绑定 | [工作线配置 Service](../../src/app/workline/services/workline_configuration_service.py) 的 `config` 校验入口 | 由工作线配置流程保存，角色定义归插件；运行期间禁止修改；[START Service](../../src/app/workline/services/workline_start_service.py) 校验并保存 WorkLine 当前精确插件版本及必要执行合同 |
| WmsConfirmation 周期派发调度 | [Celery 配置](../../src/celery_app/config.py) 的 `beat_schedule` 对应任务条目 | 当前是代码配置，修改调度后重启 Beat；任务参数仍须满足 worker 和 Service 的约束，不可仅放大调度参数绕过批量上限 |

宿主 `Settings` 的读取优先级为进程环境变量、运行时 `.env`、代码默认值；进程内缓存读取结果，不提供热更新。
部署 profile 和编排负责提供值，不应在另一份 Python 配置中重复维护默认值。核实生效值时需检查目标进程的实际环境，不能只看 `.env` 文件。
本地环境的生成、启动和重建使用 [本地开发环境说明](local-development-environment.md) 的既有入口。

插件业务参数由插件声明和解释，经工作线配置流程进入 WorkLine；宿主环境配置不得依赖具体插件业务字段。
工作线完全收敛清线并停用后才能修改运行配置；配置变更不改写已冻结的请求正文、幂等身份或可靠义务期限。

## 2. 固定合同：修改前同步核对合同与消费者

| 约束 | 代码入口 | 合同依据 |
| --- | --- | --- |
| Decision/Fact 路径、WMS Event 正文上限、货架面字段约束 | [公共 wire 合同](../../src/app/wms_adapter/wire_common.py) | [自动出库交互合同](../contracts/wms-outbound-picking-task-integration-requirements.md)；Transport 提交路径是上一节明确允许的部署路由配置 |
| operation 名称、状态、错误码及专属 DTO | [WMS Adapter 各领域目录](../../src/app/wms_adapter/) | [当前 Operation 清单](../architecture/northbound-wms-operation-inventory.csv) 指向对应合同 |
| 共享 WMS Client 当前单次 HTTP 超时 | [Transport Composition](../../src/app/transport/composition.py) 的 `build_transport_runtime` | [Transport 合同](../contracts/transport-fulfillment-contract.md) 的提交超时约束；当前未提供环境变量入口 |
| prepare 接收窗口 | [执行能力配置](../../src/app/execution/config.py) 的 `WMS_CONFIRMATION_DISPATCH_WINDOW` | [WMS Operation 与 prepare 所有权设计](../superpowers/specs/2026-09-04-outbound-picking-task-plan-delta-design.md)；窗口不包含 WMS 后台规划时间 |

这些值不能仅为调整方便而配置化。协议调整需同步代码合同、相关消费者和合同测试；已经冻结的可靠义务按原记录继续处理。

## 3. 实现策略：保留在所属能力

| 策略 | 当前维护入口 | 调整要求 |
| --- | --- | --- |
| 派发领取租约、缺省重试间隔 | [WmsConfirmation Service](../../src/app/execution/services/wms_confirmation_service.py) 的 `dispatch_batch` 及结果处理 | 当前由代码控制；有实际调优需求时再决定是否进入宿主配置，须验证租约、deadline 和重试语义 |
| WmsConfirmation 派发批量 | [执行能力配置](../../src/app/execution/config.py) 的 `WMS_CONFIRMATION_BATCH_LIMIT` | Service 默认值及上限、worker 固定批量、Beat 调用参数直接引用同一定义；修改后重启相关进程并验证边界，不提供环境变量覆盖 |
| plan_delta SQL 写入分批大小 | [PlanDelta Repository](../../src/app/wms_integration/outbound_picking/repositories/plan_delta_repository.py) 的 `MEMBER_BATCH_SIZE` | 属于持久化实现策略；有数据库性能证据后在该能力内调整，不作为插件业务参数 |

内部策略不因使用了数值就自动成为运维配置。新增可调整参数时复用所属能力入口，明确默认值、合法范围、生效时机和测试所有者；不新增全仓常量中心或第二条加载通道。
