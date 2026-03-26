# E2E 测试开发经验教训总结

## 项目背景
SMT 粗分机插件 E2E 测试开发 - 验证插件与 Mock 设备的完整交互流程

---

## 教训 1: Mock 事件类型必须与 WES 规范保持一致

### 问题
Pipeline Mock 最初使用了自建的内部事件类型：
```python
# ❌ 错误：Mock 自建类型
SCAN_OK = "SCAN_OK"
SCAN_NG = "SCAN_NG"
DETECT_OK = "DETECT_OK"
...
```

但 WES 回调接口只接受标准事件类型：
```python
# ✅ 正确：WES 标准类型
SCAN_COMPLETED = "SCAN_COMPLETED"
PROCESS_COMPLETED = "PROCESS_COMPLETED"
```

导致回调时返回 422 错误：
```
Input should be 'ESTOP_PRESSED', 'DEVICE_ONLINE', ... 'SCAN_COMPLETED'
```

### 解决方案
Mock 直接使用 WES 标准事件类型，结果放在 data 中：
```python
{
    "device_code": "PIPELINE01",
    "event_type": "SCAN_COMPLETED",  # WES 标准类型
    "data": {
        "barcode": "TEST-001",
        "result": "OK"  # 结果放在 data 中
    }
}
```

### 关键认知
- **Mock 是 WES 规范的实现方**，不是规范的定义方
- Mock 代码必须严格遵循硬件接口文档和白皮书定义
- 扫码/检测结果应该通过 `data.result` 传递，而不是通过不同的事件类型

---

## 教训 2: 环境变量传递在 spawn 模式下需要特殊处理

### 问题
macOS 上使用 `multiprocessing.set_start_method("spawn")` 启动子进程时，子进程不会自动继承父进程的环境变量。

导致 Mock 服务无法读取 `API_APP_ID` 和 `API_APP_SECRET`。

### 解决方案
1. **在父进程读取 .env.e2e**：
```python
def _load_env_from_file(self) -> dict[str, str]:
    env_vars = {}
    _e2e_env_file = Path(__file__).parent / ".env.e2e"
    if _e2e_env_file.exists():
        content = _e2e_env_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()
    return env_vars
```

2. **通过 kwargs 传递给子进程**：
```python
process = multiprocessing.Process(
    target=run_server,
    args=(...),
    kwargs={"env_vars": self._env_vars},  # 传递环境变量
    name=service["name"],
)
```

3. **子进程启动时设置环境变量**：
```python
def run_server(..., env_vars: dict[str, str] | None = None) -> None:
    if env_vars:
        for key, value in env_vars.items():
            os.environ[key] = value
```

### 关键认知
- macOS 必须使用 `spawn` 方式（`fork` 与 Objective-C runtime 不兼容）
- `spawn` 模式下子进程是全新进程，不继承父进程环境
- 环境变量必须在子进程启动时显式设置

---

## 教训 3: 数据库迁移与数据初始化的顺序依赖

### 问题
运行 `seed_e2e_test_data.py` 时报错：
```
column work_lines.plugin_key does not exist
```

### 原因
数据库表结构缺少 `plugin_key` 字段，需要先运行迁移。

### 解决方案
```bash
# 1. 先运行数据库迁移
uv run alembic upgrade head

# 2. 再初始化 E2E 测试数据
uv run python scripts/data/seed_e2e_test_data.py
```

### 关键认知
- E2E 测试数据初始化依赖于完整的数据库 schema
- 在初始化数据前必须确保所有迁移已应用
- `seed_e2e_test_data.py` 是幂等的，可以重复运行

---

## 教训 4: API 认证凭证需要双向配置

### 问题
Mock 服务回调 WES 时返回 401/403 错误。

### 原因
- WES 后端需要知道 Mock 使用的 `app_id`/`app_secret`
- Mock 需要知道 WES 的回调地址

### 解决方案
1. **WES 数据库中创建 API 应用**：
```python
app_id = "app_Gqnvr3dpjGwlrjtO"
app_secret = "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao"
```

2. **分配回调权限**：
- `api:callback:result` - 任务结果回传权限
- `api:callback:event` - 设备事件上报权限

3. **Mock 读取环境变量**：
```python
API_APP_ID = os.getenv("API_APP_ID", "app_Gqnvr3dpjGwlrjtO")
API_APP_SECRET = os.getenv("API_APP_SECRET", "...")
```

### 关键认知
- API 认证是双向的：WES 需要验证 Mock，Mock 也需要知道 WES 地址
- 凭证必须在数据库和 Mock 中保持一致
- E2E 测试需要完整的基础设施：DB + Redis + WES 服务

---

## 教训 5: 签名算法必须完全一致

### 问题
API 认证返回 "签名验证失败"。

### 原因
Mock 和 WES 后端的签名算法实现不一致。

### 解决方案
确保双方使用相同的签名算法：
```python
# 签名字符串格式: {app_id}{timestamp}{method}{path}
# 注意: 不包含分隔符，避免 JSON 序列化导致的签名不一致问题
sign_string = f"{app_id}{timestamp}{method}{path}"
signature = hmac.new(
    app_secret.encode("utf-8"),
    sign_string.encode("utf-8"),
    hashlib.sha256
).hexdigest()
```

### 关键认知
- 签名算法是安全敏感代码，必须完全一致
- 避免使用冒号等分隔符，防止编码差异
- 时间戳使用 Unix 秒级时间戳，统一格式

---

## 教训 6: E2E 测试需要完整的外部依赖

### 问题
测试 `test_pipeline_scan_event` 失败，因为 Pipeline Mock 尝试回调 WES，但 WES 服务未启动。

### 原因
E2E 测试是端到端测试，需要完整的外部依赖：
1. PostgreSQL 数据库
2. Redis 缓存
3. WES 后端服务（uvicorn）
4. Mock 服务

### 解决方案
创建测试运行脚本，确保依赖启动：
```bash
# 1. 启动基础设施
docker-compose up -d postgres redis

# 2. 运行数据库迁移
uv run alembic upgrade head

# 3. 初始化 E2E 测试数据
uv run python scripts/data/seed_e2e_test_data.py

# 4. 启动 WES 服务
uv run uvicorn main:app --host 0.0.0.0 --port 8001 --reload

# 5. 运行 E2E 测试
source tests/e2e/smt_classifier/.env.e2e
PYTHONPATH=. uv run pytest tests/e2e/smt_classifier/ -v -m e2e
```

### 关键认知
- E2E 测试 ≠ 单元测试，需要完整的外部依赖
- Mock 服务是 E2E 测试的一部分，不是被测对象
- 测试前必须确保所有依赖服务已启动

---

## 教训 7: 文档一致性检查的重要性

### 问题
Mock 实现与硬件接口文档不一致。

### 解决方案
创建文档一致性验证报告，对比：
1. 硬件接口文档（`SMT粗分机接口调用说明书20260321-v1.md`）
2. WES 拓扑文档（`workline_topology_overview.md`）

### 关键发现
- 两份文档在核心接口定义上基本一致
- 位置ID命名存在差异（`STATION_INPUT1` vs `LEFT_STATION_INPUT`）
- 差异是预期内的，拓扑文档明确说明位置ID仅用于命名示例

### 关键认知
- Mock 代码必须与硬件接口文档保持一致
- 文档差异需要记录并说明原因
- 运行时归线逻辑不依赖于位置ID前缀

---

## 最佳实践总结

### Mock 开发规范
1. **严格遵循硬件接口文档**，不自建类型
2. **事件类型使用 WES 标准枚举**（`SCAN_COMPLETED`, `PROCESS_COMPLETED`）
3. **业务结果放在 data 字段**，不通过事件类型区分
4. **签名算法与 WES 完全一致**，使用相同的格式和编码

### E2E 测试规范
1. **环境变量统一配置**，使用 `.env.e2e` 文件
2. **子进程环境变量显式传递**，特别是在 spawn 模式下
3. **数据库迁移先于数据初始化**
4. **API 应用凭证双向配置**（WES DB + Mock 环境变量）

### 调试技巧
1. **直接调用接口测试**，隔离问题（`curl` + Mock 服务）
2. **查看详细错误日志**，Mock 服务会返回详细的 WES 错误信息
3. **验证数据库状态**，确保 API 应用和权限已正确创建
4. **检查端口占用**，确保 WES 和 Mock 服务端口未被占用

---

## 相关文件

- Mock 服务: `tests/mock/smt_classifier/pipeline_mock.py`
- Mock 服务: `tests/mock/smt_classifier/arm_mock.py`
- E2E 配置: `tests/e2e/smt_classifier/conftest.py`
- E2E 测试: `tests/e2e/smt_classifier/test_e2e_smt_classifier.py`
- 数据初始化: `scripts/data/seed_e2e_test_data.py`
- 文档验证: `docs/hardware/document_consistency_verification.md`
