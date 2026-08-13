# 集成文档索引

本目录保存 WES 与外部系统、固定式设备供应商之间的当前集成入口、统一接口和联调规则。面向外部团队的公共接口以本目录明确标注
的外发真源为准；`docs/contracts/` 是 WES 内部治理合同，不向外部团队增加额外依赖。硬件厂商原始资料位于 `docs/hardware/`。

| 文档 | 用途 | 生命周期 |
| --- | --- | --- |
| [WES - WMS 对接接口需求](wes-wms-interface-requirements.md) | 按现场场景说明调用接口、参数来源、WMS 事务和回调数据生成方式 | `ReviewRequired`；按场景区分可实施范围 |
| [第三方设备统一接口白皮书](third_party_integration_whitepaper.md) | WES 与 ECS/网关之间的固定设备 wire 真源 | `Approved` |
| [Callback/Event 验证原则](callback_event_validation_principles.md) | 设备 callback/event 的合同校验、身份和冲突原则 | 当前设备集成规则 |
| [WorkLine 设备错误码标准化](workline_device_error_code_standardization.md) | 设备错误码归一化与业务映射边界 | 当前设备集成规则 |
| [WMS Caller Checklist](wms_caller_checklist.md) | 收敛前 typed port 调用方的异常处理基线 | `implementation_baseline`，不是目标业务合同 |

使用顺序：

- 开发 WMS 对接：只使用 [WES - WMS 对接接口需求](wes-wms-interface-requirements.md) 中标为 `Approved` 的范围；发现冲突或缺项时停止实现并由双方先修正文档。
- 开发 ECS/设备接入：从 [第三方设备统一接口白皮书](third_party_integration_whitepaper.md) 开始，再读取批准的设备合同附录。
- 查阅旧 WMS/RCS 约定：只读 `docs/hardware/wms_rcs_interface_requirements.md`，不得据此恢复旧路径或字段。

本目录不保存历史设计副本、转发文件或供应商私有 Adapter 实现。被后续合同取代的过程文档应移至项目外
`../archive_docs/wes_backend/`。
