# src/utils/request_parse.py
# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Date    : 2024/12/30
# @Author  : Aaron Zhou
# @File    : request_parse.py
# @Software: Cursor
# @Description: 请求解析工具

from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

import httpx
from asgiref.sync import sync_to_async
from fastapi import Request
from pydantic import dataclasses
from redis.exceptions import RedisError
from user_agents import parse  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]
from XdbSearchIP.xdbSearcher import XdbSearcher  # pyright: ignore[reportMissingTypeStubs]

from src.core.conf import settings
from src.core.logger import logger
from src.core.path_conf import IP2REGION_XDB
from src.database.redis_client import ensure_redis_connection, get_redis

if TYPE_CHECKING:
    from collections.abc import Callable


class LocationInfo(TypedDict):
    country: str | None
    regionName: str | None
    city: str | None


class ParsedUserAgent(Protocol):
    def get_os(self) -> str | None: ...

    def get_browser(self) -> str | None: ...

    def get_device(self) -> str | None: ...


@dataclasses.dataclass
class IpInfo:
    """IP 信息"""

    ip: str
    country: str | None
    region: str | None
    city: str | None


@dataclasses.dataclass
class UserAgentInfo:
    """用户代理信息"""

    user_agent: str | None
    os: str | None
    browser: str | None
    device: str | None


def get_request_ip(request: Request) -> str:
    """获取请求的 ip 地址"""
    real = request.headers.get("X-Real-IP")
    if real:
        ip = real
    else:
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0] if forwarded else request.client.host if request.client else "Unknown Host"
    # 忽略 pytest
    if ip == "testclient":
        ip = "127.0.0.1"
    return ip


async def get_location_online(ip: str, user_agent: str) -> LocationInfo | None:
    """
    在线获取 ip 地址属地，无法保证可用性，准确率较高

    :param ip:
    :param user_agent:
    :return:
    """
    async with httpx.AsyncClient(timeout=3) as client:
        ip_api_url = f"http://ip-api.com/json/{ip}?lang=zh-CN"
        headers = {"User-Agent": user_agent}
        try:
            response = await client.get(ip_api_url, headers=headers)
            if response.status_code == 200:
                return cast("LocationInfo", response.json())
        except Exception as e:
            logger.error(f"在线获取 ip 地址属地失败，错误信息：{getattr(e, 'data', e)!s}")
            return None
        return None


@sync_to_async
def get_location_offline(ip: str) -> LocationInfo | None:
    """
    离线获取 ip 地址属地，无法保证准确率，100%可用

    :param ip:
    :return:
    """
    try:
        searcher_cls = cast("Any", XdbSearcher)
        cb = cast("bytes | None", searcher_cls.loadContentFromFile(dbfile=IP2REGION_XDB))
        searcher = cast("Any", searcher_cls(contentBuff=cb))
        data = cast("str", searcher.search(ip))
        searcher.close()
        parts = data.split("|")
        return {
            "country": parts[0] if parts[0] != "0" else None,
            "regionName": parts[2] if parts[2] != "0" else None,
            "city": parts[3] if parts[3] != "0" else None,
        }
    except Exception as e:
        logger.error(f"离线获取 ip 地址属地失败，错误信息：{getattr(e, 'data', e)!s}")
        return None


async def parse_ip_info(request: Request) -> IpInfo:
    """
    解析 ip 信息
    """
    country, region, city = None, None, None
    ip = get_request_ip(request)
    redis_client = get_redis()
    if redis_client:
        try:
            location = await redis_client.get(f"{settings.IP_LOCATION_REDIS_PREFIX}:{ip}")
            if location:
                country, region, city = location.split(" ")
                return IpInfo(ip=ip, country=country, region=region, city=city)
        except RedisError as exc:
            # IP 属地缓存是辅助能力，Redis 池异常不能阻断主请求链路。
            logger.warning(f"读取 IP 属地缓存失败，已降级: {exc}")
            await ensure_redis_connection()
    user_agent = request.headers.get("User-Agent") or ""
    if settings.IP_LOCATION_PARSE == "online":
        location_info = await get_location_online(ip, user_agent)
    elif settings.IP_LOCATION_PARSE == "offline":
        location_info = await get_location_offline(ip)
    else:
        location_info = None
    if location_info:
        country = location_info.get("country")
        region = location_info.get("regionName")
        city = location_info.get("city")
        if redis_client:
            try:
                await redis_client.set(
                    f"{settings.IP_LOCATION_REDIS_PREFIX}:{ip}",
                    f"{country} {region} {city}",
                    ex=settings.IP_LOCATION_EXPIRE_SECONDS,
                )
            except RedisError as exc:
                logger.warning(f"写入 IP 属地缓存失败，已降级: {exc}")
                await ensure_redis_connection()
    return IpInfo(ip=ip, country=country, region=region, city=city)


def parse_user_agent_info(request: Request) -> UserAgentInfo:
    """
    解析 user_agent 信息
    """
    user_agent = request.headers.get("User-Agent")
    if user_agent:
        parse_user_agent = cast("Callable[[str], ParsedUserAgent]", parse)
        _user_agent = parse_user_agent(user_agent)
        os = _user_agent.get_os()
        browser = _user_agent.get_browser()
        device = _user_agent.get_device()
    else:
        os = None
        browser = None
        device = None
    return UserAgentInfo(user_agent=user_agent, device=device, os=os, browser=browser)
