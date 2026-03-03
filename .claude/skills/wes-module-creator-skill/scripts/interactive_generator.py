#!/usr/bin/env python3
"""
WES Backend 交互式模块生成器

通过问答式交互创建模块，提供智能辅助和代码预览
"""

import inquirer
import yaml
from pathlib import Path
from typing import Any

from template_engine import ModuleContext, TemplateEngine
from module_generator_v2 import ModuleGeneratorV2


class InteractiveModuleGenerator:
    """交互式模块生成器"""

    def __init__(self, project_root: Path | None = None):
        if project_root is None:
            project_root = Path(__file__).parent.parent.parent.parent

        self.project_root = Path(project_root)

    def run(self):
        """运行交互式生成器"""
        print("=" * 60)
        print("🚀 WES Backend 模块生成器 - 交互式模式")
        print("=" * 60)
        print()

        # 阶段 1：基本信息
        print("📋 阶段 1/5：基本信息")
        basic_info = self._ask_basic_info()

        # 阶段 2：结构配置
        print("\n🏗️  阶段 2/5：结构配置")
        structure_config = self._ask_structure_config()

        # 阶段 3：Mixin 选择
        print("\n🧩 阶段 3/5：Mixin 选择")
        mixins = self._ask_mixins(structure_config["is_tree"])

        # 阶段 4：字段定义
        print("\n📝 阶段 4/5：字段定义")
        fields = self._ask_fields()

        # 阶段 5：关系定义
        print("\n🔗 阶段 5/5：关系定义")
        relationships = self._ask_relationships()

        # 创建上下文
        context = ModuleContext(
            module_name=basic_info["module_name"],
            class_name=basic_info.get("class_name"),
            app_name=basic_info["app_name"],
            is_tree=structure_config["is_tree"],
            schema=structure_config["schema"],
            mixins=mixins,
            fields=fields,
            relationships=relationships,
            description=basic_info.get("description", ""),
        )

        # 代码预览
        self._preview_code(context)

        # 确认生成
        if self._confirm_generation():
            generator = ModuleGeneratorV2(context, self.project_root)
            generator.generate()

            # 生成配置文件
            self._save_config(context)

            # 提示后续步骤
            self._show_next_steps(context)
        else:
            print("❌ 已取消生成")

    def _ask_basic_info(self) -> dict[str, Any]:
        """询问基本信息"""
        questions = [
            inquirer.Text("module_name", message="模块名称（snake_case，如：warehouse）"),
            inquirer.Text(
                "app_name",
                message="应用名称（sys/biz）",
                default="biz",
                validate=lambda _, x: x in ["sys", "biz"] or "请输入 sys 或 biz",
            ),
            inquirer.Text("description", message="模块描述（可选）"),
        ]

        answers = inquirer.prompt(questions)

        # 自动生成类名
        class_name = "".join(word.capitalize() for word in answers["module_name"].split("_"))

        confirm = inquirer.confirm(
            f"自动生成的类名是 {class_name}，是否使用？",
            default=True,
        )

        if not confirm:
            answers["class_name"] = inquirer.text(
                message="请输入类名（PascalCase）",
                default=class_name,
            )

        return answers

    def _ask_structure_config(self) -> dict[str, Any]:
        """询问结构配置"""
        questions = [
            inquirer.List(
                "structure_type",
                message="模块结构类型",
                choices=["平面结构", "树形结构"],
            ),
            inquirer.List(
                "schema",
                message="Schema 类型",
                choices=["BIZ（业务数据）", "SYS（系统管理）"],
            ),
        ]

        answers = inquirer.prompt(questions)

        is_tree = answers["structure_type"] == "树形结构"
        schema = "BIZ" if "BIZ" in answers["schema"] else "SYS"

        return {"is_tree": is_tree, "schema": schema}

    def _ask_mixins(self, is_tree: bool) -> list[str]:
        """询问 Mixin 选择"""
        # 基础 Mixin
        base_mixins = ["DataTableMixin"]
        if is_tree:
            base_mixins.append("TreeMixin")

        # 可选 Mixin
        optional_mixins = [
            {"name": "EnterpriseMixin", "value": "企业字段（created_by, updated_by, remark）"},
            {"name": "SoftDeleteMixin", "value": "软删除（is_deleted, deleted_at, deleted_by）"},
            {"name": "OptimisticLockMixin", "value": "乐观锁（version 字段，并发控制）"},
            {"name": "AuditableMixin", "value": "审计日志（自动记录操作历史）"},
        ]

        choices = [
            inquirer.Checkbox(
                "mixins",
                message="选择需要的 Mixin（空格选择，回车确认）",
                choices=[m["value"] for m in optional_mixins],
            )
        ]

        answers = inquirer.prompt(choices)

        selected = []
        for i, mixin in enumerate(optional_mixins):
            if mixin["value"] in answers["mixins"]:
                selected.append(mixin["name"])

        return base_mixins + selected

    def _ask_fields(self) -> list[dict[str, Any]]:
        """询问字段定义"""
        fields = []

        while True:
            print(f"\n当前字段数：{len(fields)}")

            add_field = inquirer.confirm("是否添加字段？", default=True)
            if not add_field:
                break

            field = self._ask_field()
            fields.append(field)

            # 显示当前字段列表
            print("\n当前字段列表：")
            for i, f in enumerate(fields, 1):
                print(f"  {i}. {f['name']}: {f['type']}")

        return fields

    def _ask_field(self) -> dict[str, Any]:
        """询问单个字段定义"""
        questions = [
            inquirer.Text("name", message="字段名称（snake_case）"),
            inquirer.List(
                "type",
                message="字段类型",
                choices=[
                    "str",
                    "int",
                    "float",
                    "bool",
                    "str | None",
                    "int | None",
                    "float | None",
                ],
            ),
            inquirer.Text("description", message="字段描述"),
        ]

        answers = inquirer.prompt(questions)

        field = {
            "name": answers["name"],
            "type": answers["type"],
            "description": answers["description"],
        }

        # 可选属性
        optional_questions = []

        if "str" in answers["type"]:
            optional_questions.append(
                inquirer.Text("max_length", message="最大长度（可选）", filter=lambda x: x.isdigit())
            )
        elif answers["type"] in ["int", "float"]:
            optional_questions.append(
                inquirer.Text("default", message="默认值（可选）")
            )

        optional_questions.extend([
            inquirer.Confirm("index", message="是否创建索引？", default=False),
            inquirer.Confirm("unique", message="是否唯一？", default=False),
        ])

        optional_answers = inquirer.prompt(optional_questions)

        for key, value in optional_answers.items():
            if value is not None and value != "" and value is not False:
                field[key] = value

        # 枚举类型
        if "str" in answers["type"]:
            is_enum = inquirer.confirm("是否为枚举类型？", default=False)
            if is_enum:
                enum_values = inquirer.text(
                    "枚举值（逗号分隔，如：DRAFT,CONFIRMED,COMPLETED）",
                    validate=lambda _, x: len(x) > 0,
                )
                field["enum"] = True
                field["enum_name"] = f"{answers['name'].capitalize()}Type"
                field["enum_values"] = [v.strip() for v in enum_values.split(",")]

        # 外键
        is_fk = inquirer.confirm("是否为外键？", default=False)
        if is_fk:
            fk_table = inquirer.text("外键表（如：wes_biz.work_lines）")
            field["foreign_key"] = f"{fk_table}.id"
            field["type"] = "int | None"

        return field

    def _ask_relationships(self) -> list[dict[str, Any]]:
        """询问关系定义"""
        relationships = []

        while True:
            add_rel = inquirer.confirm("是否添加关系？", default=False)
            if not add_rel:
                break

            rel = self._ask_relationship()
            relationships.append(rel)

        return relationships

    def _ask_relationship(self) -> dict[str, Any]:
        """询问单个关系定义"""
        questions = [
            inquirer.Text("name", message="关系名称（如：work_line）"),
            inquirer.Text("target_model", message="目标模型（如：WorkLine）"),
            inquirer.Text(
                "back_populates",
                message="反向关系名称（如：devices）",
                default="",
            ),
        ]

        answers = inquirer.prompt(questions)

        rel = {
            "name": answers["name"],
            "target_model": answers["target_model"],
        }

        if answers["back_populates"]:
            rel["back_populates"] = answers["back_populates"]

        return rel

    def _preview_code(self, context: ModuleContext):
        """预览生成的代码"""
        print("\n" + "=" * 60)
        print("📄 代码预览")
        print("=" * 60)

        # 渲染模型文件预览
        engine = TemplateEngine()
        model_preview = engine.render(
            "module/models/model.py.j2" if not context.is_tree else "module/models/tree_model.py.j2",
            context.to_dict(),
        )

        # 只显示前 50 行
        lines = model_preview.split("\n")[:50]
        print("\n📁 models/" + context.module_name + ".py（前 50 行）：")
        print("─" * 60)
        for line in lines:
            print(line)
        print("─" * 60)
        print(f"...（共 {len(model_preview.split(chr(10)))} 行）")

    def _confirm_generation(self) -> bool:
        """确认生成"""
        print("\n" + "=" * 60)
        confirm = inquirer.confirm(
            "确认生成模块？",
            default=True,
        )
        return confirm

    def _save_config(self, context: ModuleContext):
        """保存配置文件"""
        config_dir = self.project_root / ".claude" / "module_configs"
        config_dir.mkdir(parents=True, exist_ok=True)

        config_file = config_dir / f"{context.module_name}.yaml"
        config_file.write_text(yaml.dump(context.to_dict(), default_flow_style=False))

        print(f"\n💾 配置已保存到：{config_file}")

    def _show_next_steps(self, context: ModuleContext):
        """显示后续步骤"""
        print("\n" + "=" * 60)
        print("✅ 模块生成完成！")
        print("=" * 60)
        print("\n📋 后续步骤：")
        print(f"   1. ✨ 路由已在 src/register.py 中注册")
        print(f"   2. ✨ 模型已在 migrations/env.py 中导入")
        print(f"   3. 📝 运行代码检查：")
        print(f"      ruff format . && ruff check .")
        print(f"   4. 🗄️  生成数据库迁移：")
        print(f"      ./scripts/generate_migration.sh 'Add {context.module_name} module'")
        print(f"   5. ⬆️  运行迁移：")
        print(f"      ./scripts/migrate.sh upgrade")
        print(f"   6. 🧪 启动开发服务器测试：")
        print(f"      uvicorn main:app --reload")
        print(f"\n💡 配置文件已保存，可以重复使用：")
        print(f"   python scripts/module_generator_v2.py --config .claude/module_configs/{context.module_name}.yaml")


def main():
    import sys

    try:
        generator = InteractiveModuleGenerator()
        generator.run()
    except KeyboardInterrupt:
        print("\n\n❌ 已取消")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 发生错误：{e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
