# 自动拣料 WMS 合同对齐与联调部署记录（2026-10-16）

本次完成已批准实施计划的任务 1–3，并部署至 `CANTAISYS@100.94.216.118`。
交付边界为共享协议与可靠接线；未实现独立 automatic-picking 插件的完整业务闭环，未抽取 bin-line-common，未 Commit/Push。

## 实现与恢复所有权

| 切片 | 实现结果 | 持久恢复 owner |
| --- | --- | --- |
| 任务 1 | material.decide、source.empty_decide、movement_report 的 RACK_SLOT 请求必带原成员 plan_revision；BIN_CELL 省略且拒绝显式 null | 沿用原 WmsConfirmation；技术重试保持身份与正文 |
| 任务 2 | READY 改为单 Cell；出库 six_in_one 改 barcode；ACCEPT 增加目标层列、下一来源对象或省略；退出旧出库类型及后继枚举 | Confirmation 保存确定结果；业务 WAIT 重问仍由后续插件决定 |
| 任务 3A | Work 的固定 typed scheduler/reader 与 deployment runtime 端口；不要求 MaterialExecution | Confirmation 唯一 PickingTask owner；reader 是读取端口。任务 5 的插件 Work 保存原 operation 引用并周期发现、应用结果 |
| 任务 3B | material.decide / movement_report 可进入封闭 typed Fact/Decision；固定 converter、摘要 discriminator 与派发 wake 接线 | Confirmation 唯一 MaterialExecution owner；FactProcessor 领取与重放 Evidence。业务资格、顺序和结果解释仍归插件 |

单盘 intent 的 material_execution_id / fact_id 仅是本地 Decision 因果引用，不发送给 WMS。
基础层没有新增 Cell/双臂业务状态、重问循环、持久化表或默认业务消费者。

字段按本轮 WMS 确认：cell_index 为 int/1–7；bin_type 为 int/2、3、6；reel_layer、rack_layer、rack_column 为 int；
reel_thickness / reel_diameter 为 float（有限 JSON number）；barcode 沿用非空 UTF-8 原文、最多 1000 字符，按 Unicode code point 计数，不裁剪、不拆分。
未给定的层数、坐标、尺寸物理范围不额外推定；设备映射、cell_id 编码对应和 PkgID 来源继续在对应设备切片处理。

合同与计划：

- [WMS 出库主合同](../contracts/wms-outbound-picking-task-integration-requirements.md)
- [双方确认记录](wms-joint-confirmation-automatic-picking.md#60-字段类型确认2026-10-16)
- [实施计划](../superpowers/plans/2026-10-16-automatic-picking-implementation.md)

## 验证

- 最终聚焦回归：`uv run pytest tests/contracts/wms_adapter/outbound_picking tests/runtime/execution tests/deployment/test_core_plugin_installation.py tests/api/test_qa_regression_transport_openapi.py src/wes_plugin_sdk/tests -q`，1260 passed。
- 完整 QUALITY：`./scripts/git-quality-gate.sh --profile quality` 通过。默认 FAST 为 4274 passed / 5 skipped；SDK 测试另在上述显式聚焦集执行。
- selector 选定 28 个 HEAVY 文件，使用独占临时 PostgreSQL、Redis 和真实 worker：144 passed、0 skipped，耗时 376.01 秒。包括 Work 的 READY/NO_WORK/WAIT 持久保存、WAIT 不自动重问、单盘实际事务 owner 与旧入库回归。
- OpenAPI 正常响应及新增三条 DirectPick revision 请求示例均通过实际严格 parser；provider 合同导出至 `reports/automatic-picking-wms-2026-10-16/`。
- 最终范围 Review 未发现遗留阻断问题；git diff --check 通过。

QUALITY 首次因 SDK 测试断言被 Bandit B101 识别而失败；按既有 SDK 测试风格标注测试断言后完整重跑通过，未削弱断言或绕过门禁。

## 部署证据

基线 HEAD：`d38cced4201e0a09e46ceff5fe7b9f84dce20f13`。
仅更新 20 个生产 Python 文件，应用前逐项核对远端内容与基线一致；源代码指纹为：

`5b4998b204fe126571c238729611b90d50606cadef4fea99492f67a6619da0cb`

备份与前后 SHA 清单：

`/srv/wes/app/current-single/.integration-hot/patches/automatic-picking-wms-20261016-5b4998b204fe/`

其中 `backup/` 保存修改前已有文件，`deploy-manifest.json` 记录新增文件原本不存在及全部前后 SHA。

API、两个 worker、Beat、Flower 全部重启，最终均 healthy；启动时间晚于本次源文件更新时间。
5 个容器均核对全部 20 个文件 SHA 并通过无外部 I/O 的 typed 合同检查；SDK 实际来自 `/app/src/wes_plugin_sdk/src/wes_plugin_sdk/__init__.py`。
两个 worker 均返回 pong；新启动日志未发现 ImportError、模块缺失、Traceback 或未注册任务。
运行结果以源码指纹和实际进程为准，原镜像标签不能代表本次源码补丁。

本次无 schema/依赖变化；服务器与本地 Alembic head 同为 `f72bd038e915`。

## WMS 联调条件与入口

| 检查项 | 当前结果 |
| --- | --- |
| WES 健康 | LAN 与 Tailnet 的 /health 均 200 JSON |
| 文档与新示例 | [LAN Swagger](http://10.24.199.219/api/docs)、[Tailnet Swagger](http://100.94.216.118/api/docs) 均可访问；/api/openapi.json 已包含新合同 |
| WMS → WES | POST `http://10.24.199.219/api/v1/wms/events`；固定隔离内网 NONE 鉴权，按已批准 operation 校验和接收。GET 返回 405 / Allow: POST |
| WES → WMS | 基址 `http://10.24.199.217:8284`；`/api/v1/wes/decisions`、`/facts`、`/transport-requests` 均返回 405 / Allow: POST，证明服务及入口可达 |
| 自动业务驱动 | 当前只启用 KT16 / MANUAL / manual-picking；没有 AUTO 工作线或 automatic-picking 插件，不会自动发起完整新流程 |

检查快照为 2026-10-16 03:41（Asia/Shanghai）：1 个 MANUAL 任务 EXECUTING、13 个 EXECUTION_COMPLETED；
work_plan / material.decide / movement_report / source.empty_decide 没有业务义务记录。部署没有造任务或发送业务 POST。

WMS 可先根据 Swagger 中完整请求/响应样例对齐四条出库协议：字段使用 JSON 数字，barcode 原样传递，
RACK_SLOT 的三条请求保留同一来源成员 revision；work_plan READY 与下一来源对象使用同一组 Cell 参数；
可选动作不存在时省略，不传 null。技术重放必须复用原 operation_id 和正文，并返回首次业务结果。

WMS 实际 POST 的业务响应、WMS 自己主机到 WES 的反向网络访问尚未验收；405 不代表业务接受成功。
完整 AUTO 请求触发需要后续插件/工作线实施，本次不扩展至该范围。
