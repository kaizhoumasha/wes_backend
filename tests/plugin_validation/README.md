# 插件验证测试环境

## 📋 概述

本测试环境用于验证 `SimplifiedSmtPlugin` 与 `SmtClassifierPlugin` 的功能等价性和性能对比。

## 🚀 快速开始

### 1. 准备测试环境

```bash
# 1. 启动基础设施（如果还没启动）
docker-compose up -d

# 2. 运行数据库迁移
python migrations/plugin_validation/001_add_test_worklines.py

# 3. 记录创建的 WorkLine ID
# 传统插件: SMT粗分机-传统插件（验证）
# 简化插件: SMT粗分机-简化插件（验证）
```

### 2. 配置测试脚本

编辑 `tests/plugin_validation/run_validation_test.py`：

```python
traditional_workline_id = 1  # 替换为实际的 WorkLine ID
simplified_workline_id = 2   # 替换为实际的 WorkLine ID
base_url = "http://localhost:8000"  # API 地址
```

### 3. 运行测试

```bash
# 方式1: 直接运行
python tests/plugin_validation/run_validation_test.py

# 方式2: 使用 pytest
pytest tests/plugin_validation/ -v

# 方式3: 使用 uv
uv run pytest tests/plugin_validation/ -v
```

## 📁 文件结构

```
tests/plugin_validation/
├── README.md                    # 本文件
├── run_validation_test.py      # 测试运行脚本
├── test_functional.py          # 功能等价性测试
├── test_performance.py         # 性能基准测试
└── __init__.py

tests/mock/
├── device_simulator.py         # 设备模拟器
├── test_data_generator.py      # 测试数据生成器
└── __init__.py

migrations/plugin_validation/
├── __init__.py
└── 001_add_test_worklines.py  # 测试 WorkLine 迁移
```

## 🎯 测试场景

### 功能验证场景

| 场景 ID | 场景名称 | 描述 | 验证点 |
|---------|---------|------|--------|
| S001 | 正常扫码OK流程 | 扫码OK → 检测OK → 流水线传输 → 出料 | 状态迁移一致性 |
| S002 | 正常扫码NG流程 | 扫码NG → NG缓存 → 完成 | NG分流一致性 |
| S003 | 检测NG流程 | 扫码OK → 检测NG → NG缓存 → 完成 | 检测分流一致性 |
| S004 | 条码过短 | 扫码条码 < 3位 | 数据验证一致性 |
| S005 | 条码特殊字符 | 扫码条码包含特殊字符 | 数据验证一致性 |
| S006 | 抓取失败 | 扫码OK → 抓取失败 → 错误 | 错误处理一致性 |
| S007 | 流水线传输失败 | 检测OK → 传输失败 → 错误 | 错误处理一致性 |
| S008 | 超时场景 | 设备响应超时 | 超时处理一致性 |
| S009 | 并发场景 | 10个并发扫码请求 | 并发控制一致性 |
| S010 | 长时间运行 | 100个连续扫码请求 | 稳定性验证 |

## 🛠️ 工具使用

### DeviceSimulator（设备模拟器）

```python
from tests.mock.device_simulator import DeviceSimulator, SimulationScenario

# 创建模拟器
simulator = DeviceSimulator(base_url="http://localhost:8000")

# 发送扫码事件
await simulator.send_scan_event(workline_id=1, barcode="ABC123")

# 模拟完整流程
result = await simulator.simulate_full_workflow(
    workline_id=1,
    scenario=SimulationScenario.NORMAL_OK
)

# 批量测试
batch_result = await simulator.run_batch_test(
    traditional_workline_id=1,
    simplified_workline_id=2,
    scenario=SimulationScenario.RANDOM,
    count=10
)

# 关闭模拟器
await simulator.close()
```

### TestDataGenerator（测试数据生成器）

```python
from tests.mock.test_data_generator import (
    TestDataGenerator,
    TestDataScenario,
    generate_batch_scan_events,
)

# 生成单个扫码事件
generator = TestDataGenerator()
event = generator.generate_scan_event(TestDataScenario.VALID_BARCODE_OK)

# 批量生成扫码事件
events = generate_batch_scan_events(count=100, ok_ratio=0.8)
```

## 📊 预期输出

### 功能等价性报告

```
功能等价性验证报告

状态迁移验证:
  ✅ IDLE → WAITING_INSPECTION: 通过
  ✅ WAITING_INSPECTION → WAITING_CONVEYOR: 通过
  ✅ WAITING_INSPECTION → WAITING_PICK_PLACE: 通过
  ✅ WAITING_CONVEYOR → WAITING_OUTPUT: 通过
  ✅ WAITING_OUTPUT → COMPLETED: 通过

命令派发验证:
  ✅ PICK_AND_PUT: 命令参数一致
  ✅ MOVE_FORWARD: 命令参数一致
  ✅ PICK_NG: 命令参数一致
  ✅ OUTPUT: 命令参数一致

错误处理验证:
  ✅ 条码无效: 错误码一致
  ✅ 抓取失败: 错误码一致
  ✅ 设备超时: 错误码一致
  ✅ 状态不匹配: 错误码一致
```

### 性能对比报告

```
性能基准测试报告

| 指标 | 传统插件 | 简化插件 | 改善 |
|------|---------|---------|------|
| 端到端延迟 | 150ms | 145ms | -3% ✅ |
| 内存消耗 | 2.5MB | 2.1MB | -16% ✅ |
| CPU 使用 | 45ms | 42ms | -7% ✅ |
| 并发吞吐 | 50 QPS | 55 QPS | +10% ✅ |

结论: 简化插件性能优于或等于传统插件 ✅
```

## 🔍 故障排查

### 问题1: WorkLine 未找到

**错误**: `WorkLine not found`

**解决**:
```bash
# 检查 WorkLine 是否创建
psql -h localhost -U postgres -d wes_postgres -c "
  SELECT id, name, plugin_key, status
  FROM wes_biz.work_lines
  WHERE name LIKE '%验证%';
"

# 如果没有结果，重新运行迁移
python migrations/plugin_validation/001_add_test_worklines.py
```

### 问题2: 设备未配置

**错误**: `Device role 'INPUT_ARM' not found`

**解决**:
```bash
# 检查设备配置
psql -h localhost -U postgres -d wes_postgres -c "
  SELECT id, name, devices_by_role
  FROM wes_biz.work_lines
  WHERE name LIKE '%验证%';
"

# 确保 devices_by_role 包含所有需要的角色
```

### 问题3: API 连接失败

**错误**: `Connection refused`

**解决**:
```bash
# 检查 API 服务是否运行
curl http://localhost:8000/api/v1/health

# 如果没有运行，启动服务
uv run uvicorn main:app --reload
```

## 📝 下一步

1. **完成基础验证**: 运行所有测试场景
2. **生成验证报告**: 汇总测试结果
3. **问题修复**: 修复发现的问题
4. **生产环境准备**: 部署到生产环境

## 📚 相关文档

- **验证计划**: `docs/plugin_validation_plan.md`
- **插件开发指南**: `docs/plugin_development_guide.md`
- **系统与插件能力边界**: `docs/system_vs_plugin_capabilities.md`
- **Transition 流程详解**: `docs/transition_flow_guide.md`

---

**准备好了吗？开始验证吧！** 🚀
