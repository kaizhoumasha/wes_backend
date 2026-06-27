"""WMS Provider Simulator Registry (Phase 1 CEO-013 最小骨架)。

Phase 1 起步版本: 仅加载 fixture_set_path 下的 FixtureCase 列表, 提供按
case_id 查找的 API。Phase 3 之后扩展为实际 stub 模拟 WMS HTTP 响应
(目前 fixture 仅作为 contract test 输入)。

设计原则 (主计划 §3.5.1):
- simulator 走正式 port contract, 不直接注入业务 capability
- simulator/sandbox provider 不进入生产 fallback (I3 不变量)
- 环境隔离: fixture 只在 sandbox 环境被引用

Phase 1 实施: 加载 FixtureCase + 按 case_id 查找; 不模拟 HTTP 响应
(Phase 1c normalizer 落地后接实际响应路径)。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.app.contracts.external_contract_profile import (
    ExternalContractProfile,
    FixtureCase,
)


class ProviderSimulatorRegistry:
    """Provider simulator fixture registry (Phase 1 CEO-013 最小版本)。

    加载 ExternalContractProfile.fixture_set_path 下的 *.json fixture 文件,
    解析为 FixtureCase 列表, 提供按 case_id 查找的 API。

    后续 Phase 1c/Phase 3 扩展:
    - 实际模拟 WMS HTTP 响应 (返回 dict, normalizer 消费)
    - ECS simulator 同步实现
    - sandbox profile 跨域引用拦截
    """

    def __init__(self, profile: ExternalContractProfile, repo_root: Path | None = None) -> None:
        self._profile = profile
        self._repo_root = repo_root or Path.cwd()
        self._cases: dict[str, FixtureCase] = {}
        self._loaded = False

    def load(self) -> None:
        """从 fixture_set_path 加载所有 *.json FixtureCase。

        fixture_set_path 是相对仓库根的路径 (e.g. tests/fixtures/external_contracts/wms/default)。
        """
        if self._loaded:
            return

        fixture_path = self._repo_root / self._profile.fixture_set_path
        if not fixture_path.is_dir():
            raise FileNotFoundError(f"fixture_set_path 不存在或非目录: {fixture_path}")

        for json_file in sorted(fixture_path.glob("*.json")):
            raw = json.loads(json_file.read_text(encoding="utf-8"))
            case = FixtureCase.model_validate(raw)
            if case.provider_code != self._profile.provider_code:
                raise ValueError(
                    f"fixture {json_file} provider_code={case.provider_code} "
                    f"与 profile {self._profile.provider_code} 不匹配"
                )
            if case.contract_version != self._profile.contract_version:
                raise ValueError(
                    f"fixture {json_file} contract_version={case.contract_version} "
                    f"与 profile {self._profile.contract_version} 不匹配"
                )
            self._cases[case.case_id] = case

        # 验证 required_cases 全部存在
        missing = set(self._profile.fixture_set_required_cases) - set(self._cases)
        if missing:
            raise ValueError(f"fixture_set_required_cases 缺失: {missing} (已有: {set(self._cases)})")

        self._loaded = True

    def get_case(self, case_id: str) -> FixtureCase:
        """按 case_id 查找 FixtureCase。加载未触发时自动 load。"""
        if not self._loaded:
            self.load()
        if case_id not in self._cases:
            raise KeyError(
                f"case_id={case_id} 不在 profile {self._profile.provider_code} fixture_set 中, 已有: {set(self._cases)}"
            )
        return self._cases[case_id]

    def list_cases(self) -> list[str]:
        """返回所有 case_id 列表。"""
        if not self._loaded:
            self.load()
        return sorted(self._cases)

    def has_case(self, case_id: str) -> bool:
        """case_id 是否存在 (不抛异常)。"""
        if not self._loaded:
            self.load()
        return case_id in self._cases

    @property
    def profile(self) -> ExternalContractProfile:
        return self._profile
