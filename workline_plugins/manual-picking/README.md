# 人工拣料插件

插件标识 `manual-picking`，显示名称“人工拣料”，仅支持 `MANUAL` 工作线。
负责传送带料箱人工拣料和退料货架直接取料两条出库路径，不承担人工入库。

当前交付声明、工作线装配、基础启用与事件观察；业务 handler 后续逐步接入。旧 `manual_bin_processing` 插件保留。

## 声明与装配

`src/manual_picking/definition.py` 是插件身份、版本和资源需求的唯一来源，包版本从声明提取。
设备角色为 `SCAN1`、`SCAN2`、`SCAN3`、`SCAN4`；工作位为 `FIVE_RACK`、`RETURN_RACK`、
`TRANSFER_RACK`、`INLET`、`OUTLET`，每个插槽绑定一个本线实际资源。

五层货架区对 WES 只有一个点位。工作线配置的容量 x 包含一个作业位和 x−1 个排队位；
插件声明不保存容量、现场编码或排队位清单。实际工作位编码与货架编号是不同身份。

后续业务直接引用具名对象，例如 `from manual_picking.definition import SCAN2, FIVE_RACK`；
需要字符串的现有端口使用 `SCAN2.role_key` 或 `FIVE_RACK.slot_key`。

## 安装与验证

在后端根目录运行 `uv sync --dev --extra manual-picking` 安装本插件；需要同时保留粗分插件时增加
`--extra rough-sorter`。镜像构建 extra 为 `manual-picking`，部署启用键为 `manual-picking`。
本机开发 Compose 已同时安装并启用粗分和人工拣料声明；依赖变化后通过 `scripts/dev-env.sh up` 重建。

工作线页面可选择本插件、绑定资源并保存草稿或完整配置。完整装配通过归属、位置类型和设备实时准入后可 START；
没有业务启动计划时 `flow_mode` 为 `null`。基础准入使用统一设备合同，状态时效与命令超时由宿主
`src/core/conf.py` 的 `WORKLINE_DEVICE_STATUS_MAX_AGE_MS`、`WORKLINE_DEVICE_COMMAND_TIMEOUT_MS` 管理，START 时冻结。

普通设备事件可靠留证后，在没有 handler 时标记为 `IGNORED`，不生成业务执行、成功 Decision 或外部指令；
后续添加 handler 不自动重放历史观察事件。急停、已发命令和已关联业务执行的结果仍走已有可靠处理。
本轮不执行 SCAN1—SCAN4 扫码业务判断、WMS 到位通知或退箱 FIFO 入队。

在后端根目录运行 `uv run --extra manual-picking pytest workline_plugins/manual-picking/tests -q` 验证插件装配。
基础声明校验与绑定规则由 SDK / 宿主测试承接，不复制到插件测试。
