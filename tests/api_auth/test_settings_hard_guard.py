"""H6: APP_DEBUG=False 时禁止 SKIP_API_AUTH=True (启动 hard guard)。

主计划 §7.1 威胁模型 + H6: 生产/预发环境 (APP_DEBUG=False)
SKIP_API_AUTH=True 会旁路所有 callback 鉴权, 必须在 settings 加载时
(启动时) 即失败, 而非请求时才发现。

src/core/conf.py Settings.validate_security_settings 的 model_validator
在启动时执行 hard guard。本测试直接构造 Settings 实例 (走 model_validator)
验证该 guard 行为, 不依赖 .env 文件或模块单例 reload。
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from src.core.conf import Settings


def _fernet_key() -> str:
    """生成一个有效的 Fernet 密钥用于测试。"""
    return Fernet.generate_key().decode()


def _base_kwargs() -> dict:
    """构造合法的 Settings 基础参数 (生产环境默认, APP_DEBUG=False)。"""
    import secrets

    return {
        "_env_file": None,  # 跳过 .env 文件加载
        "JWT_SECRET_KEY": secrets.token_urlsafe(32),  # 避开弱密钥检查
        "API_SECRET_ENCRYPTION_KEY": _fernet_key(),
        "APP_DEBUG": False,
        "SKIP_API_AUTH": False,
    }


def test_skip_api_auth_allowed_when_app_debug_true():
    """APP_DEBUG=True 时 SKIP_API_AUTH=True 合法 (开发环境)。"""
    settings = Settings(**{**_base_kwargs(), "APP_DEBUG": True, "SKIP_API_AUTH": True})
    assert settings.SKIP_API_AUTH is True
    assert settings.APP_DEBUG is True


def test_skip_api_auth_false_allowed_when_app_debug_false():
    """APP_DEBUG=False 时 SKIP_API_AUTH=False 合法 (生产环境默认)。"""
    settings = Settings(**_base_kwargs())
    assert settings.SKIP_API_AUTH is False
    assert settings.APP_DEBUG is False


def test_skip_api_auth_true_rejected_when_app_debug_false():
    """H6: APP_DEBUG=False 时 SKIP_API_AUTH=True 必须启动即失败。"""
    with pytest.raises(ValidationError, match="SKIP_API_AUTH"):
        Settings(**{**_base_kwargs(), "APP_DEBUG": False, "SKIP_API_AUTH": True})
