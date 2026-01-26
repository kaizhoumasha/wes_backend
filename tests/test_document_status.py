"""
单据状态机测试

验证 DocumentStateMachine 的所有功能是否正常工作。
"""

import pytest

from src.database.document_status import DocStatus, DocumentStateMachine


class TestDocumentStateMachine:
    """测试 DocumentStateMachine 类"""

    def test_to_enum_with_enum(self):
        """测试 _to_enum 方法 - 输入枚举"""
        result = DocumentStateMachine._to_enum(DocStatus.DRAFT)
        assert result == DocStatus.DRAFT

    def test_to_enum_with_string(self):
        """测试 _to_enum 方法 - 输入字符串"""
        result = DocumentStateMachine._to_enum("draft")
        assert result == DocStatus.DRAFT

    def test_to_enum_with_invalid_string(self):
        """测试 _to_enum 方法 - 输入无效字符串"""
        result = DocumentStateMachine._to_enum("invalid")
        assert result is None

    def test_can_transition_valid(self):
        """测试合法的状态转换"""
        # DRAFT → CONFIRMED
        assert DocumentStateMachine.can_transition(DocStatus.DRAFT, DocStatus.CONFIRMED) is True
        # DRAFT → CANCELLED
        assert DocumentStateMachine.can_transition(DocStatus.DRAFT, DocStatus.CANCELLED) is True
        # CONFIRMED → COMPLETED
        assert DocumentStateMachine.can_transition(DocStatus.CONFIRMED, DocStatus.COMPLETED) is True

    def test_can_transition_invalid(self):
        """测试非法的状态转换"""
        # COMPLETED → DRAFT (终态不能转换)
        assert DocumentStateMachine.can_transition(DocStatus.COMPLETED, DocStatus.DRAFT) is False
        # CANCELLED → CONFIRMED (终态不能转换)
        assert DocumentStateMachine.can_transition(DocStatus.CANCELLED, DocStatus.CONFIRMED) is False

    def test_can_transition_with_strings(self):
        """测试使用字符串的状态转换"""
        assert DocumentStateMachine.can_transition("draft", "confirmed") is True
        assert DocumentStateMachine.can_transition("completed", "draft") is False

    def test_can_transition_with_invalid_status(self):
        """测试使用无效状态的转换"""
        assert DocumentStateMachine.can_transition("invalid", "draft") is False
        assert DocumentStateMachine.can_transition("draft", "invalid") is False

    def test_validate_transition_success(self):
        """测试合法转换验证 - 成功"""
        # 不应该抛出异常
        DocumentStateMachine.validate_transition(DocStatus.DRAFT, DocStatus.CONFIRMED)

    def test_validate_transition_failure(self):
        """测试非法转换验证 - 失败"""
        with pytest.raises(ValueError, match="不允许从"):
            DocumentStateMachine.validate_transition(DocStatus.COMPLETED, DocStatus.DRAFT)

    def test_can_edit_draft(self):
        """测试草稿状态可以编辑"""
        assert DocumentStateMachine.can_edit(DocStatus.DRAFT) is True
        assert DocumentStateMachine.can_edit("draft") is True

    def test_can_edit_rejected(self):
        """测试拒绝状态可以编辑"""
        assert DocumentStateMachine.can_edit(DocStatus.REJECTED) is True

    def test_can_edit_confirmed(self):
        """测试已确认状态不可编辑"""
        assert DocumentStateMachine.can_edit(DocStatus.CONFIRMED) is False

    def test_can_edit_invalid_status(self):
        """测试无效状态不可编辑"""
        assert DocumentStateMachine.can_edit("invalid") is False

    def test_validate_edit_success(self):
        """测试编辑验证 - 成功"""
        # 不应该抛出异常
        DocumentStateMachine.validate_edit(DocStatus.DRAFT)

    def test_validate_edit_failure(self):
        """测试编辑验证 - 失败"""
        with pytest.raises(ValueError, match="不允许修改"):
            DocumentStateMachine.validate_edit(DocStatus.CONFIRMED)

    def test_can_delete_draft(self):
        """测试草稿状态可以删除"""
        assert DocumentStateMachine.can_delete(DocStatus.DRAFT) is True

    def test_can_delete_confirmed(self):
        """测试已确认状态不可删除"""
        assert DocumentStateMachine.can_delete(DocStatus.CONFIRMED) is False

    def test_validate_delete_success(self):
        """测试删除验证 - 成功"""
        # 不应该抛出异常
        DocumentStateMachine.validate_delete(DocStatus.DRAFT)

    def test_validate_delete_failure(self):
        """测试删除验证 - 失败"""
        with pytest.raises(ValueError, match="不允许删除"):
            DocumentStateMachine.validate_delete(DocStatus.CONFIRMED)

    def test_all_transitions_defined(self):
        """测试所有状态都有转换规则定义"""
        for status in DocStatus:
            assert status in DocumentStateMachine.TRANSITIONS

    def test_editable_statuses_are_valid(self):
        """测试可编辑状态都是有效的 DocStatus"""
        for status in DocumentStateMachine.EDITABLE_STATUSES:
            assert isinstance(status, DocStatus)

    def test_deletable_statuses_are_valid(self):
        """测试可删除状态都是有效的 DocStatus"""
        for status in DocumentStateMachine.DELETABLE_STATUSES:
            assert isinstance(status, DocStatus)
