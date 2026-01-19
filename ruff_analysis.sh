#!/bin/bash
echo "================================"
echo "Ruff 代码质量分析"
echo "================================"
echo ""

echo "1️⃣ 基础代码检查"
echo "-------------------"
uv run ruff check src/ --select=E,W,F --output-format=concise || echo "无基础错误"
echo ""

echo "2️⃣ 命名规范检查"
echo "---------------"
uv run ruff check src/ --select=N --output-format=concise || echo "无命名问题"
echo ""

echo "3️⃣ 代码复杂度检查"
echo "-------------------"
uv run ruff check src/ --select=CPG --output-format=concise || echo "无复杂度问题"
echo ""

echo "4️⃣ 性能检查"
echo "---------"
uv run ruff check src/ --select=PERF --output-format=concise || echo "无性能问题"
echo ""

echo "5️⃣ 代码风格检查"
echo "-------------"
uv run ruff check src/ --select=RUF,UP,SIM --output-format=concise || echo "无风格问题"
echo ""

echo "6️⃣ 异步代码检查"
echo "-------------"
uv run ruff check src/ --select=ASYNC,RET --output-format=concise || echo "无异步问题"
echo ""

echo "7️⃣ 导入检查"
echo "---------"
uv run ruff check src/ --select=I,TID,ICN --output-format=concise || echo "无导入问题"
echo ""

echo "================================"
echo "✅ 分析完成"
echo "================================"
