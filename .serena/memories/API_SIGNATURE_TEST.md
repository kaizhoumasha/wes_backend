# API 签名测试文档

## 签名算法（方案 B：不包含 body）

```
签名字符串格式: {app_id}{timestamp}{method}{path}
签名算法: HMAC-SHA256(签名字符串, app_secret)
```

**注意**：不包含 body 参数，避免 JSON 序列化导致的签名不一致问题。

## 请求头

| Header | 说明 | 示例 |
|--------|------|------|
| X-App-ID | 应用 ID | `app_AJU5wlk1Lnm4zXZt` |
| X-Timestamp | 时间戳（秒） | `1738767689` |
| X-Signature | 签名 | `abc123...` |

## 测试 API 应用信息

- **App ID**: `app_AJU5wlk1Lnm4zXZt`
- **App Secret**: `sec_yW29Qslp0jxRvs4EtMTuQElVuib1io1TigoI9aoQMZ0`
- **权限**: `api:try:invoke`
- **IP 白名单**: `127.0.0.1`, `192.168.65.1`, `::1` (需根据实际网络环境配置)

---

## 测试方式

### 1. Python 版本（推荐）

```bash
# 运行 Python 测试脚本
uv run python tests/api/test_signature.py
```

### 2. Bash 版本（快速测试）

```bash
# 运行 Bash 测试脚本
chmod +x scripts/test_api_signature.sh
./scripts/test_api_signature.sh
```

### 3. 手动测试（curl）

```bash
# 设置变量
APP_ID="app_AJU5wlk1Lnm4zXZt"
APP_SECRET="sec_yW29Qslp0jxRvs4EtMTuQElVuib1io1TigoI9aoQMZ0"
TIMESTAMP=$(date +%s)
METHOD="POST"
PATH="/api/v1/api-auth/applications/try/invoke"

# 计算签名（不包含 body）
SIGN_STRING="${APP_ID}${TIMESTAMP}${METHOD}${PATH}"
SIGNATURE=$(echo -n "$SIGN_STRING" | openssl dgst -sha256 -hmac "$APP_SECRET" | awk '{print $2}')

# 发送请求
curl -X POST "http://localhost:8001${PATH}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "X-App-ID: $APP_ID" \
  -H "X-Timestamp: $TIMESTAMP" \
  -H "X-Signature: $SIGNATURE" \
  -d '{"data":{"command_name":"test","command_description":"测试","command_parameters":["p1"],"command_response":"ok"}}'
```

### 4. Apifox 调试

#### 步骤 1：配置环境变量

在 Apifox 的 **环境管理** 中添加（重要：不要把 `APP_SECRET` 放在请求头中发送！）：

| 变量名 | 值 |
|--------|-----|
| `APP_ID` | `app_AJU5wlk1Lnm4zXZt` |
| `APP_SECRET` | `sec_yW29Qslp0jxRvs4EtMTuQElVuib1io1TigoI9aoQMZ0` |

#### 步骤 2：添加前置脚本

在请求的 **"前置脚本"** 标签页中添加：

```javascript
// ===== 计算签名 =====
console.log("===== 签名调试 =====");

// 动态生成时间戳（秒级）
var timestamp = Math.floor(Date.now() / 1000);
pm.environment.set("timestamp", String(timestamp));

// 从环境变量获取（不要从请求头获取 X-App-Secret）
var app_id = pm.environment.get("APP_ID");
var app_secret = pm.environment.get("APP_SECRET");

console.log("app_id:", app_id);
console.log("app_secret:", app_secret);

if (!app_id || !app_secret) {
    console.error("请先在环境管理中配置 APP_ID 和 APP_SECRET");
    throw new Error("缺少 APP_ID 或 APP_SECRET");
}

// 获取请求方法
var method = pm.request.method.toUpperCase();
console.log("method:", method);

// URL 对象解析
var urlObj = pm.request.url;
console.log("URL 对象:", JSON.stringify(urlObj));

// 提取 path（URL 对象的 path 是数组）
var path = "/" + urlObj.path.join("/");
console.log("提取的 Path:", path);

// 构造签名字符串（不包含 body）
var signString = app_id + timestamp + method + path;
console.log("Sign String:", signString);

// 使用 CryptoJS 计算 HMAC-SHA256
var secretWordArray = CryptoJS.enc.Utf8.parse(app_secret);
var hash = CryptoJS.HmacSHA256(signString, secretWordArray);
var signature = hash.toString(CryptoJS.enc.Hex);

console.log("Signature:", signature);

// 设置环境变量和请求头
pm.environment.set("signature", signature);
pm.request.headers.add({
    key: "X-Signature",
    value: signature
});

// 确保 X-App-ID 也被添加到请求头
pm.request.headers.add({
    key: "X-App-ID",
    value: app_id
});

// 确保 X-Timestamp 也被添加到请求头
pm.request.headers.add({
    key: "X-Timestamp",
    value: timestamp
});
```

#### 步骤 3：配置请求

| 字段 | 值 |
|------|-----|
| **Method** | POST |
| **URL** | `http://localhost:8001/api/v1/api-auth/applications/try/invoke` |
| **Headers** | `X-App-ID: {{APP_ID}}`<br>`X-Timestamp: {{timestamp}}`<br>`X-Signature: {{signature}}`<br>`Content-Type: application/json` |
| **Body** | ```json<br>{<br>  "data": {<br>    "command_name": "test",<br>    "command_description": "测试",<br>    "command_parameters": ["p1"],<br>    "command_response": "ok"<br>  }<br>}<br>``` |

**注意**：
- ❌ **不要**在 Headers 中添加 `X-App-Secret`（密钥不能发送到服务器）
- ✅ `APP_SECRET` 只在环境变量中存储，仅用于本地签名计算

#### 步骤 4：发送请求并查看调试输出

在 Console 标签页查看输出：

```
===== 签名调试 =====
app_id: app_AJU5wlk1Lnm4zXZt
app_secret: sec_yW29Qslp0jxRvs4EtMTuQElVuib1io1TigoI9aoQMZ0
method: POST
URL 对象: {"protocol":"http","port":"8001","path":["api","v1","api-auth","applications","try","invoke"],"host":["localhost"],"query":[],"variable":[]}
提取的 Path: /api/v1/api-auth/applications/try/invoke
Sign String: app_AJU5wlk1Lnm4zXZt1770284774POST/api/v1/api-auth/applications/try/invoke
Signature: 3d8db1054e41bf90fc7ca5f5dbb08918b85bd027a7d300ed478b868d833d4477
```

#### 常见问题排查

**问题 1：返回 "需要 API 认证"**
- 原因：前置脚本执行失败，或签名没有正确添加
- 解决：检查 Console 是否有脚本错误，确认环境变量已配置

**问题 2：返回 "签名验证失败"**
- 原因：签名计算不一致
- 解决：确认签名字符串格式为 `{app_id}{timestamp}{method}{path}`（不含 body）

---

## 重要提示

### 权限类型更新
- 权限类型已从 `api` 分裂为 `user_api`（用户 RBAC）和 `external_api`（外部 API 应用）
- 扫描器和查询逻辑已同步更新
- `get_api_permissions()` 方法现在查询 `type IN ('user_api', 'external_api')`

### IP 白名单配置
- **本地测试**：添加 `127.0.0.1`, `::1`
- **Docker 网络**：可能需要添加容器网络 IP（如 `192.168.65.1`）
- **生产环境**：配置实际的服务器 IP 或 CIDR
- **更新后**：必须清除 Redis 缓存 `docker exec wes_redis_dev redis-cli FLUSHDB`

### 权限分配
- 使用 `POST /api/v1/api-auth/applications/{id}/permissions` 分配权限
- 请求体：`{"permission_ids": [38]}` （38 是 `api:try:invoke` 的权限 ID）
- 权限 ID 可通过 `GET /api/v1/api-auth/applications/available-permissions?sync=true` 查询

### 清除缓存
```bash
# 清除 Redis 缓存（更新白名单或权限后必须执行）
docker exec wes_redis_dev redis-cli FLUSHDB
```

---

## 测试验证结果

```bash
POSTGRES_HOST=localhost uv run python tests/api/test_signature.py

# 输出:
✅ 测试 1: POST /api/v1/api-auth/applications/try/invoke
   - 应用 ID: app_AJU5wlk1Lnm4zXZt
   - 应用名称: string
   - 权限列表: ['api:try:invoke']
   - 消息: API 调用成功

✅ 测试 2: POST /api/v1/api-auth/applications (无权限)
   - 正确拒绝: HTTP 401
```
