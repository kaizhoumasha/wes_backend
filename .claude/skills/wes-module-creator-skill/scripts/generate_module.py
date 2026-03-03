#!/usr/bin/env python3
"""
WES Backend 模块生成器

自动生成符合项目架构规范的模块代码（Models、Repository、Service、API）
"""

import argparse
from pathlib import Path


class ModuleGenerator:
    """模块代码生成器"""

    def __init__(
        self,
        module_name: str,
        is_tree: bool = False,
        mixins: list[str] | None = None,
        app_name: str = "biz",
    ):
        self.module_name = module_name
        self.is_tree = is_tree
        self.mixins = mixins or ["DataTableMixin", "EnterpriseMixin"]
        self.app_name = app_name

        # 类名（首字母大写）
        self.class_name = "".join(word.capitalize() for word in module_name.split("_"))
        # 表名（复数）
        self.table_name = f"{module_name}s"

        # 项目根目录
        self.project_root = Path(__file__).parent.parent.parent
        self.module_path = self.project_root / "src" / "app" / app_name / module_name

    def generate(self):
        """生成完整模块"""
        print(f"🚀 开始生成模块: {self.module_name}")
        print(f"   类型: {'树形结构' if self.is_tree else '平面结构'}")
        print(f"   Mixins: {', '.join(self.mixins)}")

        # 创建目录结构
        self._create_directories()

        # 生成文件
        self._generate_models()
        self._generate_repository()
        self._generate_service()
        self._generate_api()

        print(f"\n✅ 模块生成完成: {self.module_path}")
        print("\n📋 后续步骤:")
        print("   1. 在 src/register.py 中注册路由")
        print("   2. 运行代码检查: ruff format . && ruff check .")
        print(f"   3. 生成数据库迁移: ./scripts/generate_migration.sh 'Add {self.module_name} module'")
        print("   4. 运行迁移: ./scripts/migrate.sh upgrade")

    def _create_directories(self):
        """创建目录结构"""
        dirs = [
            self.module_path / "models",
            self.module_path / "repositories",
            self.module_path / "services",
            self.module_path / "v1",
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)
            # 创建 __init__.py
            (dir_path / "__init__.py").touch()

    def _generate_models(self):
        """生成模型文件"""
        # 确定需要导入的 Mixin
        mixin_imports = []
        if self.is_tree:
            mixin_imports.append("TreeMixin")
        mixin_imports.extend(self.mixins)

        # 生成模型代码
        content = f'''"""
{self.class_name} 模型定义
"""

from datetime import datetime
from typing import Literal

from sqlmodel import Field

from src.core.mixins import BaseMixin, {", ".join(mixin_imports)}
from src.database.model_factory import ModelFactory
from src.database.schema_conf import SchemaType


class {self.class_name}Base({"TreeMixin, " if self.is_tree else ""}BaseMixin):
    """
    {self.class_name} 基础字段（用于 Schema 复用）

    TODO: 添加业务字段
    示例：
    name: str = Field(max_length=50, description="名称")
    code: str = Field(max_length=20, description="编码", index=True)
    """
    pass


class {self.class_name}({self.class_name}Base, {", ".join(self.mixins)}, table=True):
    """
    {self.class_name} 数据库表模型
    """

    __tablename__: Literal["{self.table_name}"] = "{self.table_name}"
    __schema__ = SchemaType.BIZ.value

    # TODO: 添加表特有字段（不在 Base 中的字段）
    # 示例：
    # capacity: int | None = Field(default=None, description="容量")


class {self.class_name}Create(ModelFactory({self.class_name}Base).for_create()):
    """
    {self.class_name} 创建 Schema（基于 {self.class_name}Base，所有字段必需）
    """
    pass


class {self.class_name}Update(ModelFactory({self.class_name}Base).for_update()):
    """
    {self.class_name} 更新 Schema（基于 {self.class_name}Base，所有字段可选）
    """
    pass


class {self.class_name}Response({self.class_name}Base):
    """
    {self.class_name} 响应 Schema（基于 {self.class_name}Base，添加系统字段）
    """

    id: int
    created_at: datetime
    updated_at: datetime

    # TODO: 添加关联对象（如果需要）
    # 示例：
    # items: list[ItemResponse] = []
'''

        # 如果是树形结构，添加树形响应 Schema
        if self.is_tree:
            content += f'''

class {self.class_name}TreeResponse({self.class_name}Response):
    """
    {self.class_name} 树形响应 Schema
    """

    children: list["{self.class_name}TreeResponse"] = []
'''

        file_path = self.module_path / "models" / f"{self.module_name}.py"
        file_path.write_text(content)
        print(f"   ✓ 生成模型: {file_path}")

    def _generate_repository(self):
        """生成 Repository 文件"""
        base_class = "TreeRepository" if self.is_tree else "BaseRepository"
        import_path = "src.database.tree_repository" if self.is_tree else "src.database.base_repository"

        content = f'''"""
{self.class_name} Repository
"""

from {import_path} import {base_class}
from src.app.{self.app_name}.{self.module_name}.models.{self.module_name} import {self.class_name}


class {self.class_name}Repository({base_class}[{self.class_name}]):
    """
    {self.class_name} 数据访问层
    """

    def __init__(self):
        super().__init__({self.class_name})

    # TODO: 添加自定义查询方法（如果需要）
    # 示例：
    # async def get_by_code(self, db: AsyncSession, code: str) -> {self.class_name} | None:
    #     result = await db.execute(
    #         select({self.class_name}).where({self.class_name}.code == code)
    #     )
    #     return result.scalar_one_or_none()


# 创建单例
{self.module_name}_repository = {self.class_name}Repository()
'''

        file_path = self.module_path / "repositories" / f"{self.module_name}_repository.py"
        file_path.write_text(content)

        # 更新 __init__.py
        init_file = self.module_path / "repositories" / "__init__.py"
        init_content = f"""from .{self.module_name}_repository import {self.class_name}Repository, {self.module_name}_repository

__all__ = ["{self.class_name}Repository", "{self.module_name}_repository"]
"""
        init_file.write_text(init_content)

        print(f"   ✓ 生成 Repository: {file_path}")

    def _generate_service(self):
        """生成 Service 文件"""
        if self.is_tree:
            base_class = "TreeServiceMixin, BaseService"
            import_line = "from src.core.tree_service import TreeServiceMixin"
        else:
            base_class = "BaseService"
            import_line = ""

        content = f'''"""
{self.class_name} Service
"""

from src.core.base_service import BaseService
{import_line}
from src.app.{self.app_name}.{self.module_name}.models.{self.module_name} import {self.class_name}
from src.app.{self.app_name}.{self.module_name}.repositories import {self.module_name}_repository, {self.class_name}Repository


class {self.class_name}Service({base_class}[{self.class_name}, {self.class_name}Repository]):
    """
    {self.class_name} 业务逻辑层
    """

    def __init__(self):
        super().__init__(
            {self.module_name}_repository,
            enable_cache=True,
            cache_prefix="app:{self.module_name}:detail",
        )

    # TODO: 添加自定义业务方法（如果需要）
    # 示例：
    # async def get_by_code(self, db: AsyncSession, code: str) -> {self.class_name} | None:
    #     return await self.repo.get_by_code(db, code)


# 创建单例
{self.module_name}_service = {self.class_name}Service()
'''

        file_path = self.module_path / "services" / f"{self.module_name}_service.py"
        file_path.write_text(content)

        # 更新 __init__.py
        init_file = self.module_path / "services" / "__init__.py"
        init_content = f"""from .{self.module_name}_service import {self.class_name}Service, {self.module_name}_service

__all__ = ["{self.class_name}Service", "{self.module_name}_service"]
"""
        init_file.write_text(init_content)

        print(f"   ✓ 生成 Service: {file_path}")

    def _generate_api(self):
        """生成 API 文件"""
        api_class = "TreeAPI" if self.is_tree else "BaseAPI"
        import_path = "src.core.tree_api" if self.is_tree else "src.core.base_api"

        response_schema = f"{self.class_name}TreeResponse" if self.is_tree else f"{self.class_name}Response"

        content = f'''"""
{self.class_name} API 路由
"""

from {import_path} import {api_class}
from src.app.{self.app_name}.{self.module_name}.models.{self.module_name} import (
    {self.class_name},
    {self.class_name}Create,
    {self.class_name}Update,
    {response_schema},
)
from src.app.{self.app_name}.{self.module_name}.services import {self.module_name}_service


{self.module_name}_api = {api_class}(
    module_name="{self.app_name}",
    model={self.class_name},
    service={self.module_name}_service,
    create_schema={self.class_name}Create,
    update_schema={self.class_name}Update,
    response_schema={response_schema},
    prefix="/{self.table_name}",
    tags=["{self.class_name}管理"],
    gen_create=True,
    gen_update=True,
    gen_delete=True,
    gen_bulk_delete=False,
    enable_permission=True,
)

router = {self.module_name}_api.router

# TODO: 添加自定义路由（如果需要）
# 示例：
# @router.get("/custom")
# async def custom_endpoint():
#     return {{"message": "Custom endpoint"}}
'''

        file_path = self.module_path / "v1" / f"{self.module_name}.py"
        file_path.write_text(content)

        print(f"   ✓ 生成 API: {file_path}")


def main():
    parser = argparse.ArgumentParser(description="WES Backend 模块生成器")
    parser.add_argument("--name", required=True, help="模块名称（snake_case）")
    parser.add_argument("--tree", action="store_true", help="生成树形结构模块")
    parser.add_argument("--flat", action="store_true", help="生成平面结构模块（默认）")
    parser.add_argument(
        "--mixins",
        help="Mixin 列表（逗号分隔），默认: DataTableMixin,EnterpriseMixin",
    )
    parser.add_argument("--app", default="biz", help="应用名称（默认: biz）")

    args = parser.parse_args()

    # 解析 Mixins
    mixins = None
    if args.mixins:
        mixins = [m.strip() for m in args.mixins.split(",")]

    # 生成模块
    generator = ModuleGenerator(
        module_name=args.name,
        is_tree=args.tree,
        mixins=mixins,
        app_name=args.app,
    )
    generator.generate()


if __name__ == "__main__":
    main()
