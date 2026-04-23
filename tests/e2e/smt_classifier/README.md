# SMT 粗分机 E2E 测试

SMT 粗分机多组件链路测试，验证 WES、Celery、数据库与 Mock 设备的真实交互。

## 测试覆盖范围

### 当前测试类型

| 测试文件 | 说明 | 链路 |
|--------|------|------|
| `test_pipeline_material_arrived_e2e.py` | Pipeline 事件入口与 Inbox 异步消费 | WES ↔ DB ↔ Worker |
| `test_full_e2e_chain.py` | 完整链路 smoke | WES ↔ Mock ↔ WES |
| `test_full_business_flows.py` | 完整业务场景 | WES ↔ Mock ↔ WES |

### 相关但不属于 E2E 的测试

| 目录 | 文件 | 说明 |
|------|------|------|
| `tests/integration/workline_plugins/` | `test_smt_classifier_plugin_*.py` | 插件逻辑与状态迁移 |
| `tests/mock/smt_classifier/` | `test_smt_classifier_mock.py` | Mock 服务协议与本地 HTTP 合同 |

### ⚠️ 重要说明

- 插件逻辑测试已经移动到 `tests/integration/workline_plugins/test_smt_classifier_plugin_*.py`
- Mock 服务合同测试已经移动到 `tests/mock/smt_classifier/test_smt_classifier_mock.py`
- 要测试完整的端到端链路（包括插件编排、命令生成），请使用本目录下的 E2E 文件
- 完整端到端测试需要启动 **WES Backend + Celery Worker + Mock 服务**

### 完整端到端测试指南

📖 详细说明请查看: [FULL_E2E_GUIDE.md](./FULL_E2E_GUIDE.md)

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                        E2E 测试套件                          │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │  test_e2e_*.py  │  │    conftest.py   │                  │
│  │   (测试用例)     │  │ (Fixtures/环境)  │                  │
│  └────────┬────────┘  └────────┬────────┘                  │
│           │                    │                            │
│           └────────────────────┘                            │
│                    │                                        │
│           ┌────────┴────────┐                               │
│           │  MockServiceManager │                            │
│           │   (启动/停止服务)   │                            │
│           └────────┬────────┘                               │
└────────────────────┼────────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
   ┌────┴────┐  ┌────┴────┐  ┌────┴────┐
   │Pipeline │  │  ARM01  │  │  ARM02  │
   │  Mock   │  │  Mock   │  │  Mock   │
   │(:8005)  │  │(:8006)  │  │(:8007)  │
   └────┬────┘  └────┬────┘  └────┬────┘
        │            │            │
        └────────────┴────────────┘
                     │
              ┌──────┴──────┐
              │  WES Backend │
              │ (:8001)     │
              └─────────────┘
```

## 快速开始

### 1. 一键运行（推荐）

```bash
# 运行本目录下的 E2E 测试（需要 WES + Celery + Mock）
./tests/e2e/smt_classifier/run_e2e_tests.sh

# 完整端到端测试（需要 WES + Celery + Mock）
./tests/e2e/smt_classifier/run_e2e_tests.sh --full
```

### 2. 手动步骤

#### 步骤 1: 设置环境变量

```bash
uv run python tests/e2e/smt_classifier/setup_e2e_app.py
```

这会创建 `.env.e2e` 文件，包含：
- `API_APP_ID`: app_Gqnvr3dpjGwlrjtO
- `API_APP_SECRET`: sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao
- `WES_BASE_URL`: http://localhost:8001

#### 步骤 2: 初始化数据库

```bash
uv run python scripts/data/seed_e2e_test_data.py
```

这会创建：
- 作业线: WL-CONVEYOR-01
- 设备: ARM01(进料臂), PIPELINE01(流水线), ARM02(出料臂)
- API 应用: app_Gqnvr3dpjGwlrjtO（带回调权限）

设备拓扑: ARM01 -> PIPELINE01 -> ARM02

#### 步骤 3: 启动 WES 服务

```bash
uvicorn main:app --reload
```

#### 步骤 4: 运行 E2E 测试

```bash
# 加载环境变量并运行测试
source tests/e2e/smt_classifier/.env.e2e
uv run pytest tests/e2e/smt_classifier/ -v -m e2e
```

## 测试结构

### 测试类

| 测试文件 | 说明 | 标记 |
|--------|------|------|
| `test_pipeline_material_arrived_e2e.py` | Pipeline 事件入口与异步消费 | `e2e`, `integration` |
| `test_full_e2e_chain.py` | 完整链路 smoke | `e2e`, `integration` |
| `test_full_business_flows.py` | 完整业务场景 | `e2e`, `integration` |

### 主要测试场景

1. **完整 OK 流程** (`test_full_ok_flow`)
   - 扫码 OK → 检测 OK → 流水线传输 → 出料完成

2. **扫码 NG 流程** (`test_scan_ng_flow`)
   - 扫码 NG → 直接放入 NG 缓存位

3. **检测 NG 流程** (`test_inspection_ng_flow`)
   - 扫码 OK → 检测 NG → 放入 NG 缓存位

4. **Pipeline 物料到达入口** (`test_pipeline_material_arrived_event`)
   - 设备事件 → WES ACK → Inbox 异步消费完成

## Mock 服务

说明：

- 正式接口统一使用 `/api/v1/device/*`
- 为便于本地开发和 E2E 联调，mock 同服务内额外保留 `/debug/*` 调试接口
- `/debug/*` 仅用于开发辅助，不代表供应商正式协议
- 当前约定下，`/debug/*` 默认对本地开发环境开放，不额外鉴权

### Pipeline Mock (端口 8005)

模拟 SMT 粗分机单线流水线，支持：
- `POST /api/v1/device/command` - 正式执行命令（MOVE_FORWARD）
- `GET /api/v1/device/status` - 正式状态查询
- `POST /api/v1/device/cancel` - 正式取消命令
- `POST /debug/execute` - 调试执行传输
- 自动回调 WES 结果接口

### Arm Mock (端口 8006/8007)

模拟进料/出料机械臂，支持：
- `POST /api/v1/device/command` - 正式执行命令（PICK_AND_PUT）
- `GET /api/v1/device/status` - 正式状态查询
- `POST /api/v1/device/cancel` - 正式取消命令
- `POST /debug/execute` - 调试执行搬运
- `POST /debug/scan-completed` - ARM01 调试上报扫码事件
- `POST /debug/inspection-completed` - ARM01 调试上报检测完成事件
- 自动回调 WES 事件和结果接口

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_APP_ID` | API 应用 ID | app_Gqnvr3dpjGwlrjtO |
| `API_APP_SECRET` | API 应用密钥 | sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao |
| `WES_BASE_URL` | WES 服务地址 | http://localhost:8001 |
| `WES_EVENT_CALLBACK_URL` | 事件回调地址 | http://localhost:8001/api/v1/callback/event |
| `WES_RESULT_CALLBACK_URL` | 结果回调地址 | http://localhost:8001/api/v1/callback/result |
| `MOCK_STARTUP_TIMEOUT` | Mock 服务启动超时 | 30 秒 |

## 故障排查

### Mock 服务启动失败

```bash
# 检查端口占用
lsof -i :8005
lsof -i :8006
lsof -i :8007

# 手动停止残留的 Mock 进程
pkill -f "pipeline_mock"
pkill -f "arm_mock"
```

### API 认证失败

```bash
# 重新初始化数据库
uv run python scripts/data/seed_e2e_test_data.py

# 检查 API 应用是否存在
# 查询数据库: SELECT * FROM api_auth.api_applications WHERE app_id = 'app_Gqnvr3dpjGwlrjtO'
```

### WES 回调失败

```bash
# 检查 WES 服务是否运行
curl http://localhost:8001/health

# 检查环境变量是否正确加载
echo $API_APP_ID
echo $WES_EVENT_CALLBACK_URL
```

## 文件说明

```
tests/e2e/smt_classifier/
├── __init__.py                 # 包初始化
├── conftest.py                 # Pytest Fixtures 和配置
├── test_pipeline_material_arrived_e2e.py  # Pipeline 事件入口 E2E
├── test_full_e2e_chain.py      # 完整端到端集成测试 (WES ↔ Mock)
├── test_full_business_flows.py # 完整业务场景 E2E
├── setup_e2e_app.py            # 环境设置脚本
├── run_e2e_tests.sh            # 测试运行脚本
├── .env.e2e                    # 环境变量文件（自动生成）
├── README.md                   # 本文件
└── FULL_E2E_GUIDE.md           # 完整端到端测试详细指南
```

## 技术细节

### 为什么使用 spawn 启动方式?

macOS 上必须使用 `spawn` 方式启动多进程，因为:
1. `fork` 方式与 Objective-C runtime 不兼容
2. 可能导致崩溃或死锁

### 环境变量传递

由于使用 `spawn` 方式，子进程不会自动继承父进程的环境变量。
解决方案：
1. 父进程从 `.env.e2e` 加载环境变量
2. 通过 `multiprocessing.Process(kwargs={"env_vars": ...})` 传递给子进程
3. 子进程在启动时设置这些环境变量

### 会话级 Fixture

- `mock_services`: 会话级别，只启动一次所有 Mock 服务
  - **智能检测**：自动检测服务是否已运行，避免重复启动
  - **优雅管理**：只停止自己启动的服务，外部管理的服务不会被停止
- `clean_mock_state`: 函数级别，每个测试前重置 Mock 状态

### 手动启动 Mock 服务（可选）

如果希望手动管理 Mock 服务，可以提前启动：

```bash
# 启动所有 Mock 服务
python tests/mock/smt_classifier/run_all.py

# 在另一个终端运行测试
uv run pytest tests/e2e/smt_classifier/ -v
```

测试框架会自动检测已运行的服务并复用，测试结束后不会停止手动启动的服务。
