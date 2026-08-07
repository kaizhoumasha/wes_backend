# 硬件资料边界

本目录保留硬件厂商提供的原始协议、联调资料以及便于检索的派生转写。所有文件均作为具体 Adapter 或插件的
外部输入保留，不是 WES 核心架构真源。

## 原始资料

- 厂商 PDF、图纸和原始流程材料保持原貌。
- 资料较旧或与实现不一致时，不修改原文，也不因架构收敛而归档。
- 当前差异由对应 Adapter 合同、映射和测试处理。

## 派生资料

- Markdown 转写可能压缩示例、补充检索说明或记录当时联调口径，不能覆盖配对的原始 PDF。
- `粗分机硬件供应商联调操作手册.md` 是面向供应商的 WES implementation baseline，不是厂商原始协议。
- `wms_rcs_interface_requirements.md` 是 2026-03 与 WMS 交互约定的初稿，保留当时的 MCS 命名、路径、字段样例和架构假设；
  Phase 3 只建立 WMS 薄访问标准，不选择或实现具体业务 API；具体 method/path/DTO 在后续业务开发中按 WMS 批准合同
  逐项实现。搬运、换面、入退线及 RCS 状态归 Phase 4 Transport 合同。本文件仍不是实施真源，也不得用历史编号补齐
  当前合同。
  首次纳入仓库的原始字节 SHA-256 为
  `a1cb99eb76678f8e06c98eae74c56fb76d8963e244f13c3a07028fb9675dc1c4`；不通过格式化改写来源空白。
- 派生资料中的字段归一化、插件名称以及 Session、Outbox、Inbox、Timeline、Hold 等实现描述只用于偏差识别，
  不得约束顶层 SPEC、目标 Adapter 或插件设计。

## 所有权

- 共享传输和基础传输错误由 WES 核心合同验证；厂商认证只在真实合同要求时由对应 Adapter 拥有，当前 WMS outbound
  固定为 `NONE`。
- Phase 3 WMS 薄访问标准由 `docs/contracts/wms-northbound-interaction-contract.md` 与 `src/app/wms_adapter/` 拥有；
  具体 WMS method/path/DTO/业务结果由后续业务模块及其批准合同拥有。
- WMS 转发 RCS 的运输 wire 由 Phase 4 Transport 合同与对应 Adapter 拥有，不属于 Phase 3 WMS 薄访问标准。
- 厂商 DTO、Payload、原始码和事件/命令映射由 `device_adapters/<adapter_key>/` 拥有。
- WMS 业务结果到执行 Decision 的映射、对象推进和场景测试由 `workline_plugins/<plugin_key>/` 拥有。
