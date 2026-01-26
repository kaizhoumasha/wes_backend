"""
测试 RelationMetadata 类

验证从 SQLAlchemy inspect() 自动提取关联关系信息的功能。
"""

from typing import ClassVar, Optional

import pytest
from sqlmodel import Field, Relationship, SQLModel

from src.database.relation_metadata import (
    ForeignKeyInfo,
    RelationInfo,
    RelationMetadata,
    RelationType,
)


# ==================== 测试模型定义 ====================


class Parent(SQLModel, table=True):
    """父表模型（用于测试一对多关系）"""

    __tablename__ = "test_parent"

    id: int | None = Field(default=None, primary_key=True)
    name: str

    # 一对多关系
    children: list["Child"] = Relationship(back_populates="parent")


class Child(SQLModel, table=True):
    """子表模型（用于测试多对一关系）"""

    __tablename__ = "test_child"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    parent_id: int = Field(foreign_key="test_parent.id")

    # 多对一关系
    parent: Optional["Parent"] = Relationship(back_populates="children")


class User(SQLModel, table=True):
    """用户模型（用于测试一对一关系）"""

    __tablename__ = "test_user"

    id: int | None = Field(default=None, primary_key=True)
    username: str

    # 一对一关系（通过 uselist=False 实现）
    profile: Optional["UserProfile"] = Relationship(
        back_populates="user", sa_relationship_kwargs={"uselist": False}
    )


class UserProfile(SQLModel, table=True):
    """用户资料模型（用于测试一对一关系）"""

    __tablename__ = "test_user_profile"

    id: int | None = Field(default=None, primary_key=True)
    bio: str
    user_id: int = Field(foreign_key="test_user.id")

    # 一对一关系
    user: Optional["User"] = Relationship(back_populates="profile")


class SimpleModel(SQLModel, table=True):
    """简单模型（无关联关系）"""

    __tablename__ = "test_simple"

    id: int | None = Field(default=None, primary_key=True)
    name: str


class LegacyModel(SQLModel, table=True):
    """遗留模型（使用旧的 __relation_info__ 元数据）"""

    __tablename__ = "test_legacy"

    id: int | None = Field(default=None, primary_key=True)
    name: str

    # 旧的元数据定义（用于测试向后兼容性）
    # 注意：这里只是为了测试向后兼容性，不需要真正的 Relationship
    __relation_info__: ClassVar[dict[str, RelationInfo]] = {
        "items": {
            "relation_model": Child,  # type: ignore[typeddict-item]
            "relation_type": "ONETOMANY",
            "uselist": True,
        }
    }


# ==================== 测试用例 ====================


class TestRelationMetadata:
    """测试 RelationMetadata 类"""

    def test_get_relation_info_one_to_many(self):
        """测试获取一对多关系信息"""
        relation_info = RelationMetadata.get_relation_info(Parent)

        assert "children" in relation_info
        assert relation_info["children"]["relation_model"] == Child
        assert relation_info["children"]["relation_type"] == "ONETOMANY"
        assert relation_info["children"]["uselist"] is True

    def test_get_relation_info_many_to_one(self):
        """测试获取多对一关系信息"""
        relation_info = RelationMetadata.get_relation_info(Child)

        assert "parent" in relation_info
        assert relation_info["parent"]["relation_model"] == Parent
        assert relation_info["parent"]["relation_type"] == "MANYTOONE"
        assert relation_info["parent"]["uselist"] is False

    def test_get_relation_info_one_to_one(self):
        """测试获取一对一关系信息"""
        relation_info = RelationMetadata.get_relation_info(User)

        assert "profile" in relation_info
        assert relation_info["profile"]["relation_model"] == UserProfile
        # 一对一关系应该被识别为 ONETOONE
        assert relation_info["profile"]["relation_type"] == "ONETOONE"
        assert relation_info["profile"]["uselist"] is False

    def test_get_relation_info_no_relations(self):
        """测试无关联关系的模型"""
        relation_info = RelationMetadata.get_relation_info(SimpleModel)

        assert relation_info == {}

    def test_get_relation_info_backward_compatibility(self):
        """测试向后兼容性（使用 __relation_info__）"""
        relation_info = RelationMetadata.get_relation_info(LegacyModel)

        # 应该返回 __relation_info__ 中定义的内容
        assert "items" in relation_info
        assert relation_info["items"]["relation_model"] == Child
        assert relation_info["items"]["relation_type"] == "ONETOMANY"

    def test_get_foreign_info(self):
        """测试获取外键信息"""
        foreign_info = RelationMetadata.get_foreign_info(Child)

        assert "parent_id" in foreign_info
        assert foreign_info["parent_id"]["target_table"] == "test_parent"
        assert foreign_info["parent_id"]["target_column"] == "id"

    def test_get_foreign_info_no_foreign_keys(self):
        """测试无外键的模型"""
        foreign_info = RelationMetadata.get_foreign_info(Parent)

        assert foreign_info == {}

    def test_has_relations_true(self):
        """测试 has_relations 返回 True"""
        assert RelationMetadata.has_relations(Parent) is True
        assert RelationMetadata.has_relations(Child) is True
        assert RelationMetadata.has_relations(User) is True

    def test_has_relations_false(self):
        """测试 has_relations 返回 False"""
        assert RelationMetadata.has_relations(SimpleModel) is False

    def test_get_relation_type(self):
        """测试获取关系类型"""
        # 一对多
        relation_type = RelationMetadata.get_relation_type(Parent, "children")
        assert relation_type == RelationType.ONETOMANY

        # 多对一
        relation_type = RelationMetadata.get_relation_type(Child, "parent")
        assert relation_type == RelationType.MANYTOONE

        # 一对一
        relation_type = RelationMetadata.get_relation_type(User, "profile")
        assert relation_type == RelationType.ONETOONE

        # 不存在的关系
        relation_type = RelationMetadata.get_relation_type(Parent, "nonexistent")
        assert relation_type is None

    def test_is_one_to_many(self):
        """测试 is_one_to_many 方法"""
        assert RelationMetadata.is_one_to_many(Parent, "children") is True
        assert RelationMetadata.is_one_to_many(Child, "parent") is False
        assert RelationMetadata.is_one_to_many(User, "profile") is False

    def test_is_one_to_one(self):
        """测试 is_one_to_one 方法"""
        assert RelationMetadata.is_one_to_one(User, "profile") is True
        assert RelationMetadata.is_one_to_one(Parent, "children") is False
        assert RelationMetadata.is_one_to_one(Child, "parent") is False

    def test_find_foreign_key_for_table(self):
        """测试查找外键字段"""
        # 找到外键
        foreign_key = RelationMetadata.find_foreign_key_for_table(Child, "test_parent")
        assert foreign_key == "parent_id"

        # 找不到外键
        foreign_key = RelationMetadata.find_foreign_key_for_table(Parent, "test_child")
        assert foreign_key is None

    def test_relation_info_type_safety(self):
        """测试关系信息的类型安全性"""
        relation_info = RelationMetadata.get_relation_info(Parent)

        # relation_model 应该是实际的类，不是字符串
        assert isinstance(relation_info["children"]["relation_model"], type)
        assert relation_info["children"]["relation_model"].__name__ == "Child"

        # relation_type 应该是字符串
        assert isinstance(relation_info["children"]["relation_type"], str)

        # uselist 应该是布尔值
        assert isinstance(relation_info["children"]["uselist"], bool)


class TestRelationType:
    """测试 RelationType 枚举"""

    def test_relation_type_values(self):
        """测试关系类型枚举值"""
        assert RelationType.ONETOONE.value == "ONETOONE"
        assert RelationType.ONETOMANY.value == "ONETOMANY"
        assert RelationType.MANYTOMANY.value == "MANYTOMANY"
        assert RelationType.MANYTOONE.value == "MANYTOONE"

    def test_relation_type_from_string(self):
        """测试从字符串创建枚举"""
        assert RelationType("ONETOONE") == RelationType.ONETOONE
        assert RelationType("ONETOMANY") == RelationType.ONETOMANY
        assert RelationType("MANYTOMANY") == RelationType.MANYTOMANY
        assert RelationType("MANYTOONE") == RelationType.MANYTOONE

    def test_relation_type_invalid_string(self):
        """测试无效字符串"""
        with pytest.raises(ValueError):
            RelationType("INVALID")


class TestEdgeCases:
    """测试边界情况"""

    def test_invalid_model(self):
        """测试无效模型（非 SQLModel）"""

        class NotAModel:
            pass

        relation_info = RelationMetadata.get_relation_info(NotAModel)  # type: ignore[arg-type]
        assert relation_info == {}

        foreign_info = RelationMetadata.get_foreign_info(NotAModel)  # type: ignore[arg-type]
        assert foreign_info == {}

        assert RelationMetadata.has_relations(NotAModel) is False  # type: ignore[arg-type]

    def test_multiple_foreign_keys(self):
        """测试多个外键的情况"""

        class MultiFK(SQLModel, table=True):
            __tablename__ = "test_multi_fk"

            id: int | None = Field(default=None, primary_key=True)
            parent_id: int = Field(foreign_key="test_parent.id")
            user_id: int = Field(foreign_key="test_user.id")

        foreign_info = RelationMetadata.get_foreign_info(MultiFK)

        assert "parent_id" in foreign_info
        assert "user_id" in foreign_info
        assert foreign_info["parent_id"]["target_table"] == "test_parent"
        assert foreign_info["user_id"]["target_table"] == "test_user"
