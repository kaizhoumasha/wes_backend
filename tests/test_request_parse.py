"""
请求解析工具测试

测试 IP 解析、User-Agent 解析等功能
"""

import logging
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

# 配置测试日志
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

# ==================== get_request_ip 测试 ====================


class TestGetRequestIp:
    """测试获取请求 IP 地址"""

    def test_get_ip_from_x_real_ip(self):
        """测试：从 X-Real-IP header 获取 IP"""
        logger.info("🧪 测试开始: 从 X-Real-IP header 获取 IP")
        request = Mock(spec=Request)
        request.headers = {"X-Real-IP": "192.168.1.100"}
        request.client = Mock(host="127.0.0.1")
        logger.info(f"   📝 设置 X-Real-IP: {request.headers['X-Real-IP']}")

        ip = get_request_ip(request)
        logger.info(f"   ✅ 获取到 IP: {ip}")
        assert ip == "192.168.1.100"
        logger.info("   ✓ 断言通过")

    def test_get_ip_from_x_forwarded_for(self):
        """测试：从 X-Forwarded-For header 获取 IP"""
        request = Mock(spec=Request)
        request.headers = {"X-Forwarded-For": "203.0.113.1, 198.51.100.1"}
        request.client = Mock(host="127.0.0.1")

        ip = get_request_ip(request)
        assert ip == "203.0.113.1"  # 应该取第一个 IP

    def test_get_ip_from_client_host(self):
        """测试：从 request.client.host 获取 IP"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="10.0.0.5")

        ip = get_request_ip(request)
        assert ip == "10.0.0.5"

    def test_get_ip_no_client(self):
        """测试：没有 client 信息时返回 Unknown Host"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = None

        ip = get_request_ip(request)
        assert ip == "Unknown Host"

    def test_get_ip_testclient(self):
        """测试：pytest testclient 返回 127.0.0.1"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="testclient")

        ip = get_request_ip(request)
        assert ip == "127.0.0.1"


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

    @pytest.mark.asyncio
    async def test_get_location_online_exception(self):
        """测试：在线获取 IP 位置异常"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(side_effect=Exception("Network error"))

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
    async def test_get_location_offline_with_zeros(self):
        """测试：离线获取 IP 位置，处理 0 值"""
        mock_searcher = Mock()
        mock_searcher.search.return_value = "0|0|0|0|0"
        mock_searcher.close = Mock()

        with patch("src.utils.request_parse.XdbSearcher.loadContentFromFile") as mock_load:
            mock_load.return_value = b"mock_content"
            with patch("src.utils.request_parse.XdbSearcher") as mock_searcher_class:
                mock_searcher_class.return_value = mock_searcher

                result = await get_location_offline("8.8.8.8")

                assert result is not None
                assert result["country"] is None
                assert result["regionName"] is None
                assert result["city"] is None

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
        logger.info("🧪 测试开始: 从 Redis 缓存获取 IP 信息")
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="8.8.8.8")
        logger.info(f"   📝 设置 IP: {request.client.host}")

        mock_redis = AsyncMock()
        mock_redis.get.return_value = "中国 广东 深圳"
        logger.info("   🔧 Mock Redis 返回缓存数据: '中国 广东 深圳'")

        with patch("src.utils.request_parse.get_redis", return_value=mock_redis):
            logger.info("   🔍 调用 parse_ip_info...")
            result = await parse_ip_info(request)
            logger.info(
                f"   📊 解析结果: IP={result.ip}, 国家={result.country}, 地区={result.region}, 城市={result.city}"
            )

            assert isinstance(result, IpInfo)
            assert result.ip == "8.8.8.8"
            assert result.country == "中国"
            assert result.region == "广东"
            assert result.city == "深圳"
            logger.info("   ✓ 所有断言通过")
            mock_redis.get.assert_called_once()
            logger.info("   ✓ Redis.get 被调用一次")

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
    async def test_parse_ip_info_offline_mode(self):
        """测试：离线模式获取 IP 信息"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="8.8.8.8")

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_redis.set = AsyncMock()

        with patch("src.utils.request_parse.get_redis", return_value=mock_redis):
            with patch("src.utils.request_parse.settings") as mock_settings:
                mock_settings.IP_LOCATION_PARSE = "offline"
                mock_settings.IP_LOCATION_REDIS_PREFIX = "ip_location"
                mock_settings.IP_LOCATION_EXPIRE_SECONDS = 3600

                with patch("src.utils.request_parse.get_location_offline") as mock_offline:
                    mock_offline.return_value = {
                        "country": "中国",
                        "regionName": "北京",
                        "city": "北京",
                    }

                    result = await parse_ip_info(request)

                    assert result.ip == "8.8.8.8"
                    assert result.country == "中国"
                    assert result.region == "北京"
                    assert result.city == "北京"

    @pytest.mark.asyncio
    async def test_parse_ip_info_no_redis(self):
        """测试：Redis 不可用时仍能获取 IP 信息"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = Mock(host="8.8.8.8")

        with patch("src.utils.request_parse.get_redis", return_value=None):
            with patch("src.utils.request_parse.settings") as mock_settings:
                mock_settings.IP_LOCATION_PARSE = "offline"

                with patch("src.utils.request_parse.get_location_offline") as mock_offline:
                    mock_offline.return_value = {
                        "country": "中国",
                        "regionName": "上海",
                        "city": "上海",
                    }

                    result = await parse_ip_info(request)

                    assert result.ip == "8.8.8.8"
                    assert result.country == "中国"
                    assert result.region == "上海"
                    assert result.city == "上海"

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

    def test_parse_user_agent_firefox(self):
        """测试：解析 Firefox User-Agent"""
        request = Mock(spec=Request)
        request.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
        }

        result = parse_user_agent_info(request)

        assert isinstance(result, UserAgentInfo)
        assert result.browser and "Firefox" in result.browser
        assert result.os and "Windows" in result.os

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

    def test_parse_user_agent_empty(self):
        """测试：空 User-Agent"""
        request = Mock(spec=Request)
        request.headers = {"User-Agent": ""}

        result = parse_user_agent_info(request)

        assert isinstance(result, UserAgentInfo)
        assert result.user_agent == ""


# ==================== 集成测试 ====================


class TestRequestParseIntegration:
    """请求解析集成测试"""

    @pytest.mark.asyncio
    async def test_full_request_parsing(self):
        """测试：完整的请求解析流程"""
        request = Mock(spec=Request)
        request.headers = {
            "X-Real-IP": "8.8.8.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        }
        request.client = Mock(host="127.0.0.1")

        # Mock Redis
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        mock_redis.set = AsyncMock()

        with patch("src.utils.request_parse.get_redis", return_value=mock_redis):
            with patch("src.utils.request_parse.settings") as mock_settings:
                mock_settings.IP_LOCATION_PARSE = "offline"
                mock_settings.IP_LOCATION_REDIS_PREFIX = "ip_location"
                mock_settings.IP_LOCATION_EXPIRE_SECONDS = 3600

                with patch("src.utils.request_parse.get_location_offline") as mock_offline:
                    mock_offline.return_value = {
                        "country": "美国",
                        "regionName": "加州",
                        "city": "山景城",
                    }

                    # 解析 IP 信息
                    ip_info = await parse_ip_info(request)
                    assert ip_info.ip == "8.8.8.8"
                    assert ip_info.country == "美国"

                    # 解析 User-Agent 信息
                    ua_info = parse_user_agent_info(request)
                    assert ua_info.browser and "Chrome" in ua_info.browser
                    assert ua_info.os and "Windows" in ua_info.os
