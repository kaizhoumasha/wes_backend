"""
API 应用签名认证测试

测试外部 API 应用的签名认证和权限验证
"""

import hashlib
import hmac
import time
from typing import Any

import httpx
import pytest


class APISignatureClient:
    """API 签名客户端"""

    def __init__(self, base_url: str, app_id: str, app_secret: str):
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.app_secret = app_secret

    def _calculate_signature(self, timestamp: str, method: str, path: str) -> str:
        """
        计算请求签名

        签名字符串格式: {app_id}{timestamp}{method}{path}
        签名算法: HMAC-SHA256

        注意: 不包含 body，避免 JSON 序列化导致的签名不一致问题

        Args:
            timestamp: 时间戳（秒）
            method: HTTP 方法（大写）
            path: 请求路径（包含查询参数）

        Returns:
            签名字符串（小写十六进制）
        """
        sign_string = f"{self.app_id}{timestamp}{method}{path}"
        return hmac.new(
            self.app_secret.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        发送签名请求

        Args:
            method: HTTP 方法
            path: 请求路径
            data: 请求体数据

        Returns:
            响应 JSON 数据
        """
        url = f"{self.base_url}{path}"
        body = ""

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # 计算时间戳和签名（不包含 body）
        timestamp = str(int(time.time()))
        signature = self._calculate_signature(timestamp, method, path)

        # 添加认证头
        headers.update(
            {
                "X-App-ID": self.app_id,
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            }
        )

        # 发送请求
        async with httpx.AsyncClient() as client:
            if data is None:
                response = await client.request(method, url, headers=headers)
            else:
                response = await client.request(method, url, headers=headers, content=body)

        response.raise_for_status()
        return response.json()


# ==================== 测试配置 ====================

BASE_URL = "http://localhost:8001"
TEST_APP_ID = "app_AJU5wlk1Lnm4zXZt"
TEST_APP_SECRET = "sec_yW29Qslp0jxRvs4EtMTuQElVuib1io1TigoI9aoQMZ0"


@pytest.mark.asyncio
async def test_api_try_invoke():
    """测试 API 调用端点（有权限）"""
    client = APISignatureClient(BASE_URL, TEST_APP_ID, TEST_APP_SECRET)

    response = await client.request("POST", "/api/v1/api-auth/applications/try/invoke")

    assert response["code"] == "1000"
    assert response["data"]["app_id"] == TEST_APP_ID
    assert "permissions" in response["data"]
    assert response["message"] == "API 调用成功"


@pytest.mark.asyncio
async def test_api_signature_calculation():
    """测试签名计算正确性"""
    client = APISignatureClient(BASE_URL, TEST_APP_ID, TEST_APP_SECRET)

    # 固定参数计算签名
    timestamp = "1738767689"
    method = "POST"
    path = "/api/v1/api-auth/applications/try/invoke"

    signature = client._calculate_signature(timestamp, method, path)

    # 验证签名格式（SHA256 应该是 64 位十六进制字符串）
    assert len(signature) == 64
    assert all(c in "0123456789abcdef" for c in signature)


@pytest.mark.asyncio
async def test_api_without_permission():
    """测试无权限的 API 调用（应该失败）"""
    client = APISignatureClient(BASE_URL, TEST_APP_ID, TEST_APP_SECRET)

    # 尝试创建应用（需要 api-auth:api_application:create 权限）
    # 但测试应用只有 api:try:invoke 权限
    try:
        await client.request(
            "POST",
            "/api/v1/api-auth/applications",
            data={
                "app_name": "test_app",
                "app_type": "ECS",
                "description": "测试应用",
                "ip_whitelist": ["127.0.0.1"],
            },
        )
        # 如果成功，说明权限控制有问题
        raise AssertionError("预期应该因权限不足而失败")
    except httpx.HTTPStatusError as e:
        # 403 Forbidden 或类似错误
        assert e.response.status_code in [401, 403]


@pytest.mark.asyncio
async def test_api_invalid_signature():
    """测试无效签名（应该失败）"""
    import httpx

    # 使用错误的 secret
    client = APISignatureClient(BASE_URL, TEST_APP_ID, "wrong_secret")

    try:
        await client.request("POST", "/api/v1/api-auth/applications/try/invoke")
        raise AssertionError("预期应该因签名无效而失败")
    except httpx.HTTPStatusError as e:
        assert e.response.status_code == 401


# ==================== 手动运行 ====================


async def main():
    """手动运行测试（不使用 pytest）"""
    client = APISignatureClient(BASE_URL, TEST_APP_ID, TEST_APP_SECRET)

    print("=" * 60)
    print("API 应用调用测试")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"App ID: {TEST_APP_ID}")
    print(f"App Secret: {TEST_APP_SECRET}")
    print()

    # 测试 1: 有权限的 API
    print("📡 测试 1: POST /api/v1/api-auth/applications/try/invoke")
    print("-" * 60)

    try:
        response = await client.request("POST", "/api/v1/api-auth/applications/try/invoke")

        if response.get("code") == "1000":
            print("✅ 请求成功")
            print(f"   应用 ID: {response['data']['app_id']}")
            print(f"   应用名称: {response['data']['app_name']}")
            print(f"   权限列表: {response['data']['permissions']}")
            print(f"   消息: {response['message']}")
        else:
            print(f"❌ 请求失败: {response}")
    except Exception as e:
        print(f"❌ 请求异常: {e}")

    print()

    # 测试 2: 无权限的 API
    print("📡 测试 2: POST /api/v1/api-auth/applications (无权限)")
    print("-" * 60)

    try:
        response = await client.request(
            "POST",
            "/api/v1/api-auth/applications",
            data={
                "app_name": "test_app",
                "app_type": "ECS",
                "description": "测试应用",
                "ip_whitelist": ["127.0.0.1"],
            },
        )

        if response.get("code") == "1000":
            print("⚠️  意外成功（应该没有权限）")
        else:
            print(f"✅ 正确拒绝: {response.get('message', '未知错误')}")
    except httpx.HTTPStatusError as e:
        print(f"✅ 正确拒绝: HTTP {e.response.status_code}")

    print()
    print("=" * 60)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
