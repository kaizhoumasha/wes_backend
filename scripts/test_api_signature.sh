#!/bin/bash
# API 应用签名认证测试脚本（curl 版本）
#
# 使用方法:
#   chmod +x scripts/test_api_signature.sh
#   ./scripts/test_api_signature.sh

set -e

# ==================== 配置 ====================
BASE_URL="${API_BASE_URL:-http://localhost:8001}"
APP_ID="app_AJU5wlk1Lnm4zXZt"
APP_SECRET="sec_yW29Qslp0jxRvs4EtMTuQElVuib1io1TigoI9aoQMZ0"

# ==================== 测试 1: 有权限的 API ====================
echo "========================================"
echo "API 应用签名测试"
echo "========================================"
echo "Base URL: $BASE_URL"
echo "App ID: $APP_ID"
echo "App Secret: $APP_SECRET"
echo ""

METHOD="POST"
PATH="/api/v1/api-auth/applications/try/invoke"
BODY=""

# 计算时间戳（秒）
TIMESTAMP=$(date +%s)

# 计算签名
# 签名字符串格式: {app_id}{timestamp}{method}{path} (不包含 body)
SIGN_STRING="${APP_ID}${TIMESTAMP}${METHOD}${PATH}"
SIGNATURE=$(echo -n "$SIGN_STRING" | openssl dgst -sha256 -hmac "$APP_SECRET" | awk '{print $2}')

echo "📡 测试 1: POST /api/v1/api-auth/applications/try/invoke"
echo "----------------------------------------"
echo "Timestamp: $TIMESTAMP"
echo "Sign String: $SIGN_STRING"
echo "Signature: $SIGNATURE"
echo ""

# 发送请求
RESPONSE=$(curl -s -w "\n%{http_code}" -X "$METHOD" "${BASE_URL}${PATH}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "X-App-ID: $APP_ID" \
  -H "X-Timestamp: $TIMESTAMP" \
  -H "X-Signature: $SIGNATURE" \
  -d "$BODY")

# 分离响应体和状态码
HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
RESPONSE_BODY=$(echo "$RESPONSE" | sed '$d')

echo "HTTP Status: $HTTP_CODE"
echo "Response:"
echo "$RESPONSE_BODY" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE_BODY"
echo ""

if [ "$HTTP_CODE" = "200" ]; then
    echo "✅ 测试通过"
else
    echo "❌ 测试失败 (HTTP $HTTP_CODE)"
fi

echo ""
echo "========================================"
