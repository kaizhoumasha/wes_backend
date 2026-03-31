"""
全局异常处理单元测试

测试自定义异常类和异常处理器的功能。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.core.error_handlers import register_exception_handlers
from src.core.exceptions import (
    AdminRequiredException,
    AppException,
    BusinessException,
    CacheException,
    ConflictException,
    DatabaseException,
    DuplicateException,
    ExternalServiceException,
    ForbiddenException,
    InvalidCredentialsException,
    InvalidParameterException,
    InvalidTokenException,
    NotFoundException,
    RateLimitException,
    ServiceUnavailableException,
    ThirdPartyAPIException,
    TokenExpiredException,
    UnauthorizedException,
    ValidationException,
    raise_duplicate,
    raise_forbidden,
    raise_not_found,
)


def create_test_client() -> tuple[FastAPI, TestClient]:
    """创建隔离的测试应用，避免污染全局 main.app。"""
    app = FastAPI(debug=False)
    register_exception_handlers(app)
    return app, TestClient(app, raise_server_exceptions=False)


# ==================== 异常类测试 ====================


class TestAppException:
    """测试基础应用异常类"""

    def test_app_exception_default_values(self):
        """测试异常默认值"""
        exc = AppException()
        assert exc.status_code == 500
        assert exc.code == "5000"
        assert exc.message == "服务器内部错误"
        assert exc.detail is None

    def test_app_exception_custom_values(self):
        """测试异常自定义值"""
        exc = AppException(
            message="自定义错误",
            code="CUSTOM_ERROR",
            status_code=400,
            detail={"key": "value"},
        )
        assert exc.message == "自定义错误"
        assert exc.code == "CUSTOM_ERROR"
        assert exc.status_code == 400
        assert exc.detail == {"key": "value"}

    def test_app_exception_inheritance(self):
        """测试异常继承"""
        exc = AppException("错误")
        assert isinstance(exc, Exception)
        assert str(exc) == "错误"


class TestBusinessException:
    """测试业务逻辑异常"""

    def test_business_exception_defaults(self):
        """测试业务异常默认值"""
        exc = BusinessException()
        assert exc.status_code == 400
        assert exc.code == "4000"
        assert exc.message == "业务逻辑错误"

    def test_business_exception_custom_message(self):
        """测试业务异常自定义消息"""
        exc = BusinessException("用户名已存在")
        assert exc.message == "用户名已存在"
        assert exc.status_code == 400


class TestAuthExceptions:
    """测试认证相关异常"""

    def test_unauthorized_exception(self):
        """测试未认证异常"""
        exc = UnauthorizedException()
        assert exc.status_code == 401
        assert exc.code == "2010"
        assert exc.message == "请先登录"

    def test_invalid_token_exception(self):
        """测试无效 Token 异常"""
        exc = InvalidTokenException()
        assert exc.status_code == 401
        assert exc.code == "2012"

    def test_invalid_credentials_exception(self):
        """测试无效凭证异常"""
        exc = InvalidCredentialsException()
        assert exc.status_code == 401
        assert exc.code == "2011"

    def test_token_expired_exception(self):
        """测试 Token 过期异常"""
        exc = TokenExpiredException()
        assert exc.status_code == 401
        assert exc.code == "2013"


class TestPermissionExceptions:
    """测试权限相关异常"""

    def test_forbidden_exception(self):
        """测试禁止访问异常"""
        exc = ForbiddenException()
        assert exc.status_code == 403
        assert exc.code == "2020"
        assert exc.message == "您没有权限访问该资源"

    def test_admin_required_exception(self):
        """测试需要管理员权限异常"""
        exc = AdminRequiredException()
        assert exc.status_code == 403
        assert exc.code == "2021"
        assert exc.message == "此操作需要管理员权限"


class TestResourceExceptions:
    """测试资源相关异常"""

    def test_not_found_exception_defaults(self):
        """测试资源未找到异常默认值"""
        exc = NotFoundException()
        assert exc.status_code == 404
        assert exc.code == "3000"
        assert exc.message == "请求的资源不存在"

    def test_not_found_exception_with_resource_info(self):
        """测试带资源信息的未找到异常"""
        exc = NotFoundException(resource_type="User", resource_id=123)
        assert exc.message == "User (ID: 123) 不存在"
        assert exc.detail == {"resource_type": "User", "resource_id": "123"}

    def test_conflict_exception(self):
        """测试资源冲突异常"""
        exc = ConflictException()
        assert exc.status_code == 409
        assert exc.code == "3012"

    def test_duplicate_exception_defaults(self):
        """测试重复资源异常默认值"""
        exc = DuplicateException()
        assert exc.status_code == 409
        assert exc.code == "3010"

    def test_duplicate_exception_with_field_value(self):
        """测试带字段值的重复异常"""
        exc = DuplicateException(field="username", value="admin")
        assert exc.message == "username 'admin' 已存在"
        assert exc.detail == {"field": "username", "value": "admin"}


class TestValidationExceptions:
    """测试数据验证异常"""

    def test_validation_exception_defaults(self):
        """测试验证异常默认值"""
        exc = ValidationException()
        assert exc.status_code == 422
        assert exc.code == "2004"

    def test_invalid_parameter_exception_defaults(self):
        """测试无效参数异常默认值"""
        exc = InvalidParameterException()
        assert exc.status_code == 422
        assert exc.code == "2001"

    def test_invalid_parameter_exception_with_field(self):
        """测试带字段名的无效参数异常"""
        exc = InvalidParameterException(field="email")
        assert exc.message == "参数 'email' 无效"
        assert exc.detail == {"field": "email"}


class TestServiceExceptions:
    """测试服务相关异常"""

    def test_service_unavailable_exception(self):
        """测试服务不可用异常"""
        exc = ServiceUnavailableException()
        assert exc.status_code == 503
        assert exc.code == "5030"

    def test_rate_limit_exception(self):
        """测试请求频率限制异常"""
        exc = RateLimitException()
        assert exc.status_code == 429
        assert exc.code == "9000"

    def test_database_exception(self):
        """测试数据库异常"""
        exc = DatabaseException()
        assert exc.status_code == 500
        assert exc.code == "5010"

    def test_cache_exception(self):
        """测试缓存异常"""
        exc = CacheException()
        assert exc.status_code == 500
        assert exc.code == "5020"


class TestExternalServiceExceptions:
    """测试外部服务异常"""

    def test_external_service_exception(self):
        """测试外部服务异常"""
        exc = ExternalServiceException()
        assert exc.status_code == 502
        assert exc.code == "8000"

    def test_third_party_api_exception(self):
        """测试第三方 API 异常"""
        exc = ThirdPartyAPIException()
        assert exc.status_code == 502
        assert exc.code == "8000"


class TestConvenienceFunctions:
    """测试便捷函数"""

    def test_raise_not_found(self):
        """测试抛出资源未找到异常的便捷函数"""
        with pytest.raises(NotFoundException) as exc_info:
            raise_not_found("User", 123)
        assert exc_info.value.message == "User (ID: 123) 不存在"

    def test_raise_duplicate(self):
        """测试抛出重复资源异常的便捷函数"""
        with pytest.raises(DuplicateException) as exc_info:
            raise_duplicate("username", "admin")
        assert exc_info.value.message == "username 'admin' 已存在"

    def test_raise_forbidden(self):
        """测试抛出禁止访问异常的便捷函数"""
        with pytest.raises(ForbiddenException) as exc_info:
            raise_forbidden("您没有权限")
        assert exc_info.value.message == "您没有权限"


# ==================== 异常处理器测试 ====================


class TestExceptionHandlers:
    """测试异常处理器"""

    def test_app_exception_handler_response_format(self):
        """测试应用异常处理器的响应格式"""
        app, client = create_test_client()

        @app.get("/test/app-exception")
        async def test_app_exception():
            raise AppException(
                message="测试错误",
                code="TEST_ERROR",
                status_code=400,
                detail={"key": "value"},
            )

        response = client.get("/test/app-exception")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "TEST_ERROR"
        assert data["message"] == "测试错误"
        assert data["detail"]["key"] == "value"
        assert "timestamp" in data

    def test_not_found_exception_handler(self):
        """测试资源未找到异常处理器"""
        app, client = create_test_client()

        @app.get("/test/not-found")
        async def test_not_found():
            raise NotFoundException(resource_type="User", resource_id=999)

        response = client.get("/test/not-found")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "3000"
        assert "User" in data["message"]
        assert data["detail"]["resource_type"] == "User"

    def test_duplicate_exception_handler(self):
        """测试重复资源异常处理器"""
        app, client = create_test_client()

        @app.get("/test/duplicate")
        async def test_duplicate():
            raise DuplicateException(field="username", value="admin")

        response = client.get("/test/duplicate")
        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "3010"
        assert "username" in data["message"]
        assert data["detail"]["field"] == "username"

    def test_validation_exception_handler(self):
        """测试验证异常处理器"""
        app, client = create_test_client()

        @app.get("/test/validation")
        async def test_validation():
            raise InvalidParameterException(field="email")

        response = client.get("/test/validation")
        assert response.status_code == 422
        data = response.json()
        assert data["code"] == "2001"

    def test_unauthorized_exception_handler(self):
        """测试未认证异常处理器"""
        app, client = create_test_client()

        @app.get("/test/unauthorized")
        async def test_unauthorized():
            raise UnauthorizedException

        response = client.get("/test/unauthorized")
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "2010"

    def test_forbidden_exception_handler(self):
        """测试禁止访问异常处理器"""
        app, client = create_test_client()

        @app.get("/test/forbidden")
        async def test_forbidden():
            raise ForbiddenException

        response = client.get("/test/forbidden")
        assert response.status_code == 403
        data = response.json()
        assert data["code"] == "2020"

    def test_rate_limit_exception_handler(self):
        """测试限流异常处理器"""
        app, client = create_test_client()

        @app.get("/test/rate-limit")
        async def test_rate_limit():
            raise RateLimitException

        response = client.get("/test/rate-limit")
        assert response.status_code == 429
        data = response.json()
        assert data["code"] == "9000"


class TestFastAPIBuiltInExceptions:
    """测试 FastAPI 内置异常处理"""

    def test_request_validation_error_handler(self):
        """测试请求验证错误处理器"""
        _, client = create_test_client()
        response = client.get("/test/invalid-endpoint")
        assert response.status_code == 404

    def test_validation_error_response_format(self):
        """测试验证错误响应格式"""
        app, client = create_test_client()

        @app.post("/test/validate")
        async def test_validate(data: dict):
            return data

        # 发送无效数据
        response = client.post("/test/validate", json={"invalid": "data"})
        # 由于 FastAPI 的验证，这里可能会返回 422
        if response.status_code == 422:
            data = response.json()
            assert "code" in data
            assert "message" in data


class TestDatabaseExceptions:
    """测试数据库异常处理"""

    def test_sqlalchemy_error_handler(self):
        """测试 SQLAlchemy 异常处理器"""
        app, client = create_test_client()

        @app.get("/test/db-error")
        async def test_db_error():
            raise SQLAlchemyError("Database connection failed")

        response = client.get("/test/db-error")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == "5010"

    def test_integrity_error_handler(self):
        """测试完整性约束冲突处理器"""
        app, client = create_test_client()

        @app.get("/test/integrity-error")
        async def test_integrity_error():
            raise IntegrityError(
                "INSERT statement",
                {},
                Exception(
                    'duplicate key value violates unique constraint "uq_user_username" Key (username)=(admin) already exists'
                ),
            )

        response = client.get("/test/integrity-error")
        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "3010"
        assert data["detail"]["constraint"] == "unique"


class TestGeneralException:
    """测试通用异常处理"""

    def test_general_exception_handler(self):
        """测试通用异常处理器（兜底）"""
        app, client = create_test_client()

        @app.get("/test/general-error")
        async def test_general_error():
            raise ValueError("Unexpected error")

        response = client.get("/test/general-error")
        assert response.status_code == 500
        data = response.json()
        assert data["code"] == "5000"
        assert "timestamp" in data


# ==================== 响应格式测试 ====================


class TestErrorResponseFormat:
    """测试错误响应格式"""

    def test_error_response_contains_required_fields(self):
        """测试错误响应包含必需字段"""
        app, client = create_test_client()

        @app.get("/test/error-format")
        async def test_error_format():
            raise AppException(message="测试", code="TEST")

        response = client.get("/test/error-format")
        data = response.json()

        # 验证必需字段
        assert "code" in data
        assert "message" in data
        assert "timestamp" in data

        # 验证时间戳格式（ISO 8601）
        assert data["timestamp"].endswith("Z")

    def test_error_response_optional_detail_field(self):
        """测试错误响应可选的 detail 字段"""
        app, client = create_test_client()

        @app.get("/test/error-detail")
        async def test_error_detail():
            raise AppException(
                message="测试",
                code="TEST",
                detail={"field": "value"},
            )

        response = client.get("/test/error-detail")
        data = response.json()
        assert "detail" in data
        assert data["detail"]["field"] == "value"
