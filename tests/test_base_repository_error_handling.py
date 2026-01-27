"""
测试 BaseRepository 的错误处理功能

验证数据库完整性约束错误能够被正确转换为用户友好的中文提示。
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, SQLModel

from src.core.exceptions import ConflictException, ValidationException
from src.database.base_repository import BaseRepository
from src.database.handlers.error_translator import ErrorTranslator


class ErrorTestModel(SQLModel, table=True):
    """测试用模型"""

    __tablename__ = "test_table"
    __table_args__ = {"comment": "测试表"}

    id: int = Field(primary_key=True)
    code: str = Field(sa_column_kwargs={"comment": "编码"})
    name: str = Field(sa_column_kwargs={"comment": "名称"})


class TestErrorTranslator:
    """测试 ErrorTranslator 类"""

    def setup_method(self):
        """设置测试环境"""
        self.translator = ErrorTranslator(ErrorTestModel)

    def test_check_foreign_key_constraint_pattern1(self):
        """测试外键约束错误识别 - 模式1"""
        error_msg = 'violates foreign key constraint "fk_test" on table "related_table"'

        with pytest.raises(ConflictException) as exc_info:
            self.translator._check_foreign_key_constraint(error_msg)

        assert "related_table" in str(exc_info.value)
        assert "关联" in str(exc_info.value)

    def test_check_foreign_key_constraint_pattern2(self):
        """测试外键约束错误识别 - 模式2"""
        error_msg = 'still referenced from table "wms_inbound_detail"'

        with pytest.raises(ConflictException) as exc_info:
            self.translator._check_foreign_key_constraint(error_msg)

        assert "wms_inbound_detail" in str(exc_info.value)
        assert "关联" in str(exc_info.value)

    def test_check_duplicate_key_constraint_single_column(self):
        """测试唯一约束错误识别 - 单列"""
        error_msg = 'duplicate key value violates unique constraint "uq_code"\nDETAIL:  Key (code)=(TEST001) already exists.'

        with pytest.raises(ConflictException) as exc_info:
            self.translator._check_duplicate_key_constraint(error_msg)

        # 验证消息包含字段值且使用中性表达
        assert "TEST001" in str(exc_info.value)
        assert "已被使用" in str(exc_info.value)
        # 验证 detail 包含值
        assert exc_info.value.detail["values"] == ["TEST001"]

    def test_check_duplicate_key_constraint_multiple_columns(self):
        """测试唯一约束错误识别 - 多列"""
        error_msg = 'duplicate key value violates unique constraint "uq_code_name"\nDETAIL:  Key (code, name)=(TEST001, Test) already exists.'

        with pytest.raises(ConflictException) as exc_info:
            self.translator._check_duplicate_key_constraint(error_msg)

        # 验证消息包含字段值且使用中性表达
        assert "TEST001" in str(exc_info.value)
        assert "Test" in str(exc_info.value)
        assert "已被使用" in str(exc_info.value)
        # 验证 detail 包含值
        assert exc_info.value.detail["values"] == ["TEST001", "Test"]

    def test_check_not_null_constraint(self):
        """测试非空约束错误识别"""
        error_msg = 'null value in column "code" violates not-null constraint'

        with pytest.raises(ValidationException) as exc_info:
            self.translator._check_not_null_constraint(error_msg)

        assert "不能为空" in str(exc_info.value)

    def test_get_table_cn_name_with_comment(self):
        """测试获取中文表名 - 有注释"""
        cn_name = self.translator._get_table_cn_name("test_table")
        assert cn_name == "测试表"

    def test_get_table_cn_name_without_comment(self):
        """测试获取中文表名 - 无注释"""
        cn_name = self.translator._get_table_cn_name("unknown_table")
        assert cn_name == "unknown_table"

    def test_get_column_cn_name_with_comment(self):
        """测试获取中文字段名 - 有注释"""
        cn_name = self.translator._get_column_cn_name("code")
        assert cn_name == "编码"

    def test_get_column_cn_name_without_comment(self):
        """测试获取中文字段名 - 无注释"""
        cn_name = self.translator._get_column_cn_name("unknown_column")
        assert cn_name == "unknown_column"


class TestBaseRepositoryErrorHandling:
    """测试 BaseRepository 的错误处理集成"""

    def setup_method(self):
        """设置测试环境"""
        self.repo = BaseRepository[ErrorTestModel](ErrorTestModel)

    def test_handle_integrity_error_foreign_key(self):
        """测试处理外键约束错误"""
        # 模拟 IntegrityError
        class MockOrig:
            def __init__(self, msg):
                self.msg = msg

            def __str__(self):
                return self.msg

        class MockIntegrityError(IntegrityError):
            def __init__(self, msg):
                self.orig = MockOrig(msg)

        error = MockIntegrityError('still referenced from table "wms_inbound_detail"')

        with pytest.raises(ConflictException) as exc_info:
            self.repo._handle_integrity_error(error)

        assert "关联" in str(exc_info.value)

    def test_handle_integrity_error_duplicate_key(self):
        """测试处理唯一约束错误"""

        class MockOrig:
            def __init__(self, msg):
                self.msg = msg

            def __str__(self):
                return self.msg

        class MockIntegrityError(IntegrityError):
            def __init__(self, msg):
                self.orig = MockOrig(msg)

        error = MockIntegrityError(
            'duplicate key value violates unique constraint "uq_code"\nDETAIL:  Key (code)=(TEST001) already exists.'
        )

        with pytest.raises(ConflictException) as exc_info:
            self.repo._handle_integrity_error(error)

        assert "已存在" in str(exc_info.value)

    def test_handle_integrity_error_not_null(self):
        """测试处理非空约束错误"""

        class MockOrig:
            def __init__(self, msg):
                self.msg = msg

            def __str__(self):
                return self.msg

        class MockIntegrityError(IntegrityError):
            def __init__(self, msg):
                self.orig = MockOrig(msg)

        error = MockIntegrityError('null value in column "code" violates not-null constraint')

        with pytest.raises(ValidationException) as exc_info:
            self.repo._handle_integrity_error(error)

        assert "不能为空" in str(exc_info.value)

    def test_handle_integrity_error_unknown(self):
        """测试处理未知错误"""

        class MockOrig:
            def __init__(self, msg):
                self.msg = msg

            def __str__(self):
                return self.msg

        class MockIntegrityError(IntegrityError):
            def __init__(self, msg):
                self.orig = MockOrig(msg)

        error = MockIntegrityError("some unknown database error")

        with pytest.raises(ValidationException) as exc_info:
            self.repo._handle_integrity_error(error)

        assert "数据库操作失败" in str(exc_info.value)
