"""
请求解析工具测试

测试 IP 解析、User-Agent 解析等功能
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import Request

from src.utils.request_parse import (
    IpInfo,
    UserAgentInfo,
    get_location_offline,
    get_location_online,
    get_request_ip,
    parse_ip_info,
    parse_user_agent_info,
)

# ==================== get_request_ip 测试 ====================


class TestGetRequestIp:
    """测试获取请求 IP 地址"""

    def test_get_ip_from_x_real_ip(self):
        """测试：从 X-Real-IP header 获取 IP"""
        request = Mock(spec=Request)
        request.headers = {"X-Real-IP": "192.168.1.100"}
        request.client = Mock(host="127.0.0.1")

        ip = get_request_ip(request)
        assert ip == "192.168.1.100"

    def test_get_ip_from_x_forwarded_for(self):
        """测试：从 X-Forwarded-For header 获取 IP"""
        request = Mock(spec=Request)
        request.headers = {"X-Forwarded-For": "203.0.113.1, 198.51.100.1"}
        request.client = Mock(host="127.0.0.1")

        ip = get_request_ip(request)
        assert ip == "203.0.113.1"  # 应该取第一个 IP

    def test_get_ip_no_client(self):
        """测试：没有 client 信息时返回 Unknown Host"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = None

        ip = get_request_ip(request)
        assert ip == "Unknown Host"


# ==================== get_location_online 测试 ====================


class TestGetLocationOnline:
    """测试在线获取 IP 位置"""

    @pytest.mark.asyncio
    async def test_get_location_online_success(self):
        """测试：在线获取 IP 位置成功"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "country": "中国",
            "regionName": "广东",
            "city": "深圳",
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_location_online("8.8.8.8", "test-agent")

            assert result is not None
            assert result["country"] == "中国"
            assert result["regionName"] == "广东"
            assert result["city"] == "深圳"

    @pytest.mark.asyncio
    async def test_get_location_online_failure(self):
        """测试：在线获取 IP 位置失败"""
        mock_response = Mock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)

            result = await get_location_online("8.8.8.8", "test-agent")
            assert result is None


# ==================== get_location_offline 测试 ====================


class TestGetLocationOffline:
    """测试离线获取 IP 位置"""

    @pytest.mark.asyncio
    async def test_get_location_offline_success(self):
        """测试：离线获取 IP 位置成功"""
        mock_searcher = Mock()
        mock_searcher.search.return_value = "中国|0|广东|深圳|电信"
        mock_searcher.close = Mock()

        with patch("src.utils.request_parse.XdbSearcher.loadContentFromFile") as mock_load:
            mock_load.return_value = b"mock_content"
            with patch("src.utils.request_parse.XdbSearcher") as mock_searcher_class:
                mock_searcher_class.return_value = mock_searcher

                result = await get_location_offline("8.8.8.8")

                assert result is not None
                assert result["country"] == "中国"
                assert result["regionName"] == "广东"
                assert result["city"] == "深圳"
                mock_searcher.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_location_offline_exception(self):
        """测试：离线获取 IP 位置异常"""
        with patch("src.utils.request_parse.XdbSearcher.loadContentFromFile") as mock_load:
            mock_load.side_effect = Exception("File not found")

            result = await get_location_offline("8.8.8.8")
            assert result is None


# ==================== parse_ip_info 测试 ====================


class TestParseIpInfo:
    """测试解析 IP 信息"""

    @pytest.mark.asyncio
    async def test_parse_ip_info_from_cache(self):
        """测试：从 Redis 缓存获取 IP 信息"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="8.8.8.8")

        mock_redis = AsyncMock()
        mock_redis.get.return_value = "中国 广东 深圳"

        with patch("src.utils.request_parse.get_redis", return_value=mock_redis):
            result = await parse_ip_info(request)

            assert isinstance(result, IpInfo)
            assert result.ip == "8.8.8.8"
            assert result.country == "中国"
            assert result.region == "广东"
            assert result.city == "深圳"
            mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_ip_info_online_mode(self):
        """测试：在线模式获取 IP 信息"""
        request = Mock(spec=Request)
        request.headers = {"User-Agent": "test-agent"}
        request.client = Mock(host="8.8.8.8")

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_redis.set = AsyncMock()

        with patch("src.utils.request_parse.get_redis", return_value=mock_redis):
            with patch("src.utils.request_parse.settings") as mock_settings:
                mock_settings.IP_LOCATION_PARSE = "online"
                mock_settings.IP_LOCATION_REDIS_PREFIX = "ip_location"
                mock_settings.IP_LOCATION_EXPIRE_SECONDS = 3600

                with patch("src.utils.request_parse.get_location_online") as mock_online:
                    mock_online.return_value = {
                        "country": "美国",
                        "regionName": "加州",
                        "city": "旧金山",
                    }

                    result = await parse_ip_info(request)

                    assert result.ip == "8.8.8.8"
                    assert result.country == "美国"
                    assert result.region == "加州"
                    assert result.city == "旧金山"
                    mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_ip_info_no_location_info(self):
        """测试：无法获取位置信息时返回 None 值"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="8.8.8.8")

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        with patch("src.utils.request_parse.get_redis", return_value=mock_redis):
            with patch("src.utils.request_parse.settings") as mock_settings:
                mock_settings.IP_LOCATION_PARSE = "disabled"

                result = await parse_ip_info(request)

                assert result.ip == "8.8.8.8"
                assert result.country is None
                assert result.region is None
                assert result.city is None


# ==================== parse_user_agent_info 测试 ====================


class TestParseUserAgentInfo:
    """测试解析 User-Agent 信息"""

    def test_parse_user_agent_chrome(self):
        """测试：解析 Chrome User-Agent"""
        request = Mock(spec=Request)
        request.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        result = parse_user_agent_info(request)

        assert isinstance(result, UserAgentInfo)
        assert result.user_agent is not None
        assert result.browser and "Chrome" in result.browser
        assert result.os and "Windows" in result.os
        assert result.device is not None

    def test_parse_user_agent_mobile(self):
        """测试：解析移动设备 User-Agent"""
        request = Mock(spec=Request)
        request.headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1"
        }

        result = parse_user_agent_info(request)

        assert isinstance(result, UserAgentInfo)
        assert result.os and ("iOS" in result.os or "iPhone" in result.os)
        assert result.device and "iPhone" in result.device

    def test_parse_user_agent_none(self):
        """测试：没有 User-Agent header"""
        request = Mock(spec=Request)
        request.headers = {}

        result = parse_user_agent_info(request)

        assert isinstance(result, UserAgentInfo)
        assert result.user_agent is None
