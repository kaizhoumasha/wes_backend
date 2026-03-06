#!/usr/bin/env python3
"""
WES Backend 模块生成器 V2

基于 Jinja2 模板系统的模块代码生成器
支持灵活的代码生成和自动化配置
"""

import re
from pathlib import Path

from template_engine import ModuleContext, TemplateEngine
from test_generator import TestGenerator


class ModuleGeneratorV2:
    """模块代码生成器 V2（基于模板系统）"""

    def __init__(self, context: ModuleContext, project_root: Path | None = None, generate_tests: bool = False):
        """
        初始化生成器

        Args:
            context: 模块上下文数据
            project_root: 项目根目录，默认自动检测
            generate_tests: 是否生成单元测试
        """
        self.context = context
        self.template_engine = TemplateEngine()
        self.generate_tests = generate_tests

        # 项目根目录
        if project_root is None:
            project_root = Path(__file__).parent.parent.parent.parent

        self.project_root = Path(project_root)
        self.module_path = self.project_root / "src" / "app" / context.app_name / context.module_name

    def generate(self):
        """生成完整模块"""
        print(f"🚀 开始生成模块: {self.context.module_name}")
        print(f"   类型: {'树形结构' if self.context.is_tree else '平面结构'}")
        print(f"   Mixins: {', '.join(self.context.mixins)}")

        # 创建目录结构
        self._create_directories()

        # 生成文件
        self._generate_models()
        self._generate_repository()
        self._generate_service()
        self._generate_api()
        self._generate_module_init()

        # 自动更新配置文件
        self._update_migrations_env()
        self._update_register_py()

        # 生成测试（如果启用）
        if self.generate_tests:
            self._generate_tests()

        print(f"\n✅ 模块生成完成: {self.module_path}")
        print("\n📋 后续步骤:")
        print("   1. ✨ 路由已在 src/register.py 中注册")
        print("   2. ✨ 模型已在 migrations/env.py 中导入")
        print("   3. 📝 运行代码检查: ruff format . && ruff check .")
        print(f"   4. 🗄️  生成数据库迁移: ./scripts/generate_migration.sh 'Add {self.context.module_name} module'")
        print("   5. ⬆️  运行迁移: ./scripts/migrate.sh upgrade")
        if self.generate_tests:
            print(f"   6. 🧪 运行测试: pytest tests/test_{self.context.module_name}.py")

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

    def _generate_models(self):
        """生成模型文件"""
        template_name = "module/models/tree_model.py.j2" if self.context.is_tree else "module/models/model.py.j2"

        self.template_engine.render_to_file(
            template_name,
            self.module_path / "models" / f"{self.context.module_name}.py",
            self.context.to_dict(),
        )

        # 生成 __init__.py
        self.template_engine.render_to_file(
            "module/models/__init__.py.j2",
            self.module_path / "models" / "__init__.py",
            self.context.to_dict(),
        )

        print(f"   ✓ 生成模型: {self.module_path / 'models'}")

    def _generate_repository(self):
        """生成 Repository 文件"""
        self.template_engine.render_to_file(
            "module/repositories/repository.py.j2",
            self.module_path / "repositories" / f"{self.context.module_name}_repository.py",
            self.context.to_dict(),
        )

        self.template_engine.render_to_file(
            "module/repositories/__init__.py.j2",
            self.module_path / "repositories" / "__init__.py",
            self.context.to_dict(),
        )

        print(f"   ✓ 生成 Repository: {self.module_path / 'repositories'}")

    def _generate_service(self):
        """生成 Service 文件"""
        self.template_engine.render_to_file(
            "module/services/service.py.j2",
            self.module_path / "services" / f"{self.context.module_name}_service.py",
            self.context.to_dict(),
        )

        self.template_engine.render_to_file(
            "module/services/__init__.py.j2",
            self.module_path / "services" / "__init__.py",
            self.context.to_dict(),
        )

        print(f"   ✓ 生成 Service: {self.module_path / 'services'}")

    def _generate_api(self):
        """生成 API 文件"""
        self.template_engine.render_to_file(
            "module/v1/api.py.j2",
            self.module_path / "v1" / f"{self.context.module_name}.py",
            self.context.to_dict(),
        )

        self.template_engine.render_to_file(
            "module/v1/__init__.py.j2",
            self.module_path / "v1" / "__init__.py",
            self.context.to_dict(),
        )

        print(f"   ✓ 生成 API: {self.module_path / 'v1'}")

    def _generate_module_init(self):
        """生成模块根目录的 __init__.py"""
        self.template_engine.render_to_file(
            "module/__init__.py.j2",
            self.module_path / "__init__.py",
            self.context.to_dict(),
        )

    def _update_migrations_env(self):
        """自动更新 migrations/env.py"""
        env_path = self.project_root / "migrations" / "env.py"

        if not env_path.exists():
            print("   ⚠️  migrations/env.py 不存在，跳过")
            return

        content = env_path.read_text()

        # 检查是否已经导入
        import_line = f"from {self.context.model_import} import {self.context.class_name}  # noqa: F401"
        if import_line in content:
            print("   ℹ️  模型已在 migrations/env.py 中导入")
            return

        # 查找模型导入区域
        pattern = r"(# 导入所有模型以确保它们被 SQLModel\.metadata 识别\n)"
        match = re.search(pattern, content)

        if match:
            # 在标记后添加导入
            new_content = content.replace(match.group(0), match.group(0) + import_line + "\n")
            env_path.write_text(new_content)
            print("   ✓ 更新 migrations/env.py")
        else:
            print("   ⚠️  无法在 migrations/env.py 中找到导入区域，请手动添加:")
            print(f"      {import_line}")

    def _update_register_py(self):
        """自动更新 src/register.py"""
        register_path = self.project_root / "src" / "register.py"

        if not register_path.exists():
            print("   ⚠️  src/register.py 不存在，跳过")
            return

        content = register_path.read_text()

        # 检查是否已经注册
        import_line = f"from src.app.{self.context.app_name}.{self.context.module_name} import router_v1 as {self.context.module_name}_router"
        if import_line in content:
            print("   ℹ️  路由已在 src/register.py 中注册")
            return

        # 查找 register_routers 函数
        pattern = r"(def register_routers\(app: FastAPI\).*?:)"
        match = re.search(pattern, content, re.DOTALL)

        if match:
            func_content = match.group(0)

            # 检查是否已有 app.include_router
            if "app.include_router" in func_content:
                # 在最后一个 app.include_router 后添加
                lines = func_content.split("\n")
                insert_pos = 0
                for i, line in enumerate(lines):
                    if "app.include_router" in line:
                        insert_pos = i + 1

                # 添加导入和注册
                indent = "    "
                new_import = f"{indent}from src.app.{self.context.app_name}.{self.context.module_name} import router_v1 as {self.context.module_name}_router\n"
                new_register = (
                    f"{indent}app.include_router({self.context.module_name}_router, prefix=settings.API_PATH)\n"
                )

                # 更新内容
                updated_lines = lines.copy()
                updated_lines.insert(insert_pos, new_register)

                new_func_content = "\n".join(updated_lines)
                new_content = content.replace(func_content, new_import + new_func_content)
            else:
                # 函数为空，直接添加
                new_content = content.replace(
                    func_content,
                    func_content
                    + "\n"
                    + f"    from src.app.{self.context.app_name}.{self.context.module_name} import router_v1 as {self.context.module_name}_router\n"
                    f"    app.include_router({self.context.module_name}_router, prefix=settings.API_PATH)\n",
                )

            register_path.write_text(new_content)
            print("   ✓ 更新 src/register.py")
        else:
            print("   ⚠️  无法在 src/register.py 中找到 register_routers 函数，请手动添加路由注册")

    def _generate_tests(self):
        """生成单元测试文件"""
        test_generator = TestGenerator(self.context, self.project_root)
        test_generator.generate()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="WES Backend 模块生成器 V2（基于模板系统）")
    parser.add_argument("--name", required=True, help="模块名称（snake_case）")
    parser.add_argument("--class", help="类名（PascalCase），默认自动生成")
    parser.add_argument("--tree", action="store_true", help="生成树形结构模块")
    parser.add_argument("--flat", action="store_true", help="生成平面结构模块（默认）")
    parser.add_argument("--mixins", help="Mixin 列表（逗号分隔）")
    parser.add_argument("--app", default="biz", help="应用名称（默认: biz）")
    parser.add_argument("--schema", choices=["SYS", "BIZ"], default="BIZ", help="Schema 类型（默认: BIZ）")
    parser.add_argument("--config", help="从 YAML 配置文件生成模块")
    parser.add_argument("--description", help="模块描述")
    parser.add_argument("--tests", action="store_true", help="同时生成单元测试")

    args = parser.parse_args()

    # 从配置文件或命令行参数创建上下文
    if args.config:
        config_path = Path(args.config)
        context = ModuleContext.from_yaml(config_path)
    else:
        # 解析 Mixins
        mixins = None
        if args.mixins:
            mixins = [m.strip() for m in args.mixins.split(",")]

        context = ModuleContext(
            module_name=args.name,
            class_name=getattr(args, "class", None),  # class 是保留字，使用 getattr
            app_name=args.app,
            is_tree=args.tree,
            mixins=mixins,
            schema=args.schema,
            description=args.description or "",
        )

    # 生成模块
    generator = ModuleGeneratorV2(context, generate_tests=args.tests)
    generator.generate()


if __name__ == "__main__":
    main()
