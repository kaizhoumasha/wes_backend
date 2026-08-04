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
- 派生资料中的字段归一化、插件名称以及 Session、Outbox、Inbox、Timeline、Hold 等实现描述只用于偏差识别，
  不得约束顶层 SPEC、目标 Adapter 或插件设计。

## 所有权

- 共享传输、认证和基础错误映射由 WES 核心合同验证。
- 厂商 DTO、Payload、原始码和事件/命令映射由 `device_adapters/<adapter_key>/` 拥有。
- 工作线业务 Decision、对象推进和场景测试由 `workline_plugins/<plugin_key>/` 拥有。
