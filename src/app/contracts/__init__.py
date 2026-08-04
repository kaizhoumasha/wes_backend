"""src/app/contracts/ 共享 typed contract 层。

承载跨域共享 typed DTO, 避免 capability implementation import boundary 误报和反向 ACL。

包含:
- ExternalContractProfile: WMS/ECS provider 外部合同 (CEO-013 升级自 tests/support/)
- RuntimeCapabilityProfile: capability 注入合同 (CEO-009)
- InboundNormalizerProfile: callback normalizer 合同 (CEO-009)
- SecurityProfile: HMAC canonical 占位（外部 callback 签名完整实现）

导入方向: wms_integration / device / runtime 域 import 本包; 本包不
import 任何 src/app/{wms_integration,device,runtime}/* 实现, 仅依赖
pydantic 共享类型；权责边界以 docs/architecture/authority-matrix.md 为准。

目标边界见:
docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md。
"""
