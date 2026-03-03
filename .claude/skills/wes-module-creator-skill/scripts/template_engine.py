#!/usr/bin/env python3
"""
WES Backend 模板引擎

基于 Jinja2 的代码生成模板引擎
"""

import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


class TemplateEngine:
    """Jinja2 模板引擎"""

    def __init__(self, template_dir: Path | None = None):
        """
        初始化模板引擎

        Args:
            template_dir: 模板目录路径，默认为 scripts/templates/
        """
        if template_dir is None:
            template_dir = Path(__file__).parent / "templates"

        self.template_dir = Path(template_dir)
        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            undefined=StrictUndefined,  # 严格模式，未定义变量会报错
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # 注册自定义过滤器
        self._register_filters()

    def _register_filters(self):
        """注册自定义 Jinja2 过滤器"""

        def snake_to_camel(s: str) -> str:
            """snake_case 转 CamelCase"""
            return "".join(word.capitalize() for word in s.split("_"))

        def snake_to_pascal(s: str) -> str:
            """snake_case 转 PascalCase（同 snake_to_camel）"""
            return snake_to_camel(s)

        def pluralize(s: str) -> str:
            """单数转复数（简单规则）"""
            if s.endswith("y"):
                return s[:-1] + "ies"
            return f"{s}s"

        # 注册到 Jinja2 环境
        self.env.filters["snake_to_camel"] = snake_to_camel
        self.env.filters["snake_to_pascal"] = snake_to_pascal
        self.env.filters["pluralize"] = pluralize

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        """
        渲染模板

        Args:
            template_name: 模板名称（相对于模板目录的路径）
            context: 模板变量上下文

        Returns:
            渲染后的字符串
        """
        template = self.env.get_template(template_name)
        return template.render(**context)

    def render_to_file(
        self,
        template_name: str,
        output_path: Path,
        context: dict[str, Any],
    ) -> None:
        """
        渲染模板并写入文件

        Args:
            template_name: 模板名称
            output_path: 输出文件路径
            context: 模板变量上下文
        """
        content = self.render(template_name, context)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)

    def get_template_names(self, pattern: str = "*.j2") -> list[str]:
        """
        获取所有模板文件名

        Args:
            pattern: 文件匹配模式

        Returns:
            模板文件名列表
        """
        templates = []
        for root, dirs, files in os.walk(self.template_dir):
            for file in files:
                if file.endswith(".j2"):
                    full_path = Path(root) / file
                    rel_path = full_path.relative_to(self.template_dir)
                    templates.append(str(rel_path))
        return templates


class ModuleContext:
    """模块生成上下文数据"""

    def __init__(
        self,
        module_name: str,
        class_name: str | None = None,
        app_name: str = "biz",
        is_tree: bool = False,
        mixins: list[str] | None = None,
        schema: str = "BIZ",
        fields: list[dict[str, Any]] | None = None,
        relationships: list[dict[str, Any]] | None = None,
        response_relationships: list[dict[str, Any]] | None = None,
        description: str = "",
    ):
        """
        初始化模块上下文

        Args:
            module_name: 模块名称（snake_case）
            class_name: 类名（PascalCase），默认自动生成
            app_name: 应用名称（默认: biz）
            is_tree: 是否树形结构
            mixins: Mixin 列表
            schema: Schema 类型（SYS 或 BIZ）
            fields: 自定义字段定义
            relationships: 关系定义
            response_relationships: Response Schema 中的关系
            description: 模块描述
        """
        self.module_name = module_name
        self.app_name = app_name
        self.is_tree = is_tree
        self.schema = schema
        self.description = description or f"{module_name} 模块"

        # 类名（首字母大写）
        self.class_name = class_name or "".join(
            word.capitalize() for word in module_name.split("_")
        )

        # 表名（复数）
        self.table_name = f"{module_name}s"

        # Mixin 处理
        self.mixins = mixins or []
        if is_tree and "TreeMixin" not in self.mixins:
            self.mixins.insert(0, "TreeMixin")
        if "DataTableMixin" not in self.mixins:
            self.mixins.insert(0, "DataTableMixin")

        # 字段定义
        self.fields = fields or []

        # 关系定义
        self.relationships = relationships or []
        self.response_relationships = response_relationships or []

        # 导入路径
        self.model_import = f"src.app.{app_name}.{module_name}.models.{module_name}"
        self.repo_import = f"src.app.{app_name}.{module_name}.repositories"
        self.service_import = f"src.app.{app_name}.{module_name}.services"

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于模板渲染）"""
        return {
            "module_name": self.module_name,
            "class_name": self.class_name,
            "table_name": self.table_name,
            "app_name": self.app_name,
            "is_tree": self.is_tree,
            "schema": self.schema,
            "description": self.description,
            "mixins": self.mixins,
            "fields": self.fields,
            "relationships": self.relationships,
            "response_relationships": self.response_relationships,
            "model_import": self.model_import,
            "repo_import": self.repo_import,
            "service_import": self.service_import,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModuleContext":
        """从字典创建上下文"""
        return cls(
            module_name=data["module_name"],
            class_name=data.get("class_name"),
            app_name=data.get("app_name", "biz"),
            is_tree=data.get("is_tree", False),
            mixins=data.get("mixins"),
            schema=data.get("schema", "BIZ"),
            fields=data.get("fields"),
            relationships=data.get("relationships"),
            response_relationships=data.get("response_relationships"),
            description=data.get("description", ""),
        )

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "ModuleContext":
        """从 YAML 文件创建上下文"""
        try:
            import yaml

            data = yaml.safe_load(yaml_path.read_text())
            return cls.from_dict(data)
        except ImportError:
            raise ImportError("需要安装 PyYAML: pip install pyyaml")
