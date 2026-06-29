"""条码业务判定领域服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from src.app.workline.domain.models import BarcodeDecision, BarcodeDecisionType

if TYPE_CHECKING:
    from src.app.workline.domain.contracts import SixInOne


class BarcodeDecisionService:
    """负责条码提取、合法性校验与业务 NG 判定。

    业务规则：
    - 6个统一语义字段全部有值才算 OK
    - 任一字段缺失视为 INCOMPLETE，需重新扫描
    - 当前统一业务追溯码非法视为 INVALID
    - 命中 NG 规则视为 NG
    """

    MIN_BARCODE_LENGTH = 3
    NG_RULE_KEYWORDS: ClassVar[dict[str, tuple[str, str]]] = {
        "SIZENG": ("SCAN_NG_BY_RULE", "条码命中尺寸 NG 业务规则"),
        "THICKNESSNG": ("SCAN_NG_BY_RULE", "条码命中厚度 NG 业务规则"),
    }

    def evaluate(
        self,
        six_in_one: SixInOne,
    ) -> BarcodeDecision:
        """对六合一码执行领域判定。"""

        if not six_in_one.is_complete:
            missing = ", ".join(six_in_one.missing_fields)
            return BarcodeDecision(
                six_in_one=six_in_one,
                decision=BarcodeDecisionType.INCOMPLETE,
                reason_code="BARCODE_INCOMPLETE",
                reason_message=f"条码不完整，缺失字段: {missing}",
            )

        invalid_field = self._find_field_containing_comma(six_in_one)
        if invalid_field is not None:
            return BarcodeDecision(
                six_in_one=six_in_one,
                decision=BarcodeDecisionType.INVALID,
                reason_code="BARCODE_INVALID",
                reason_message=f"条码格式错误: {invalid_field}",
            )

        pkg_id = six_in_one.PkgID or ""
        if not self._is_valid_pkg_id(pkg_id):
            return BarcodeDecision(
                six_in_one=six_in_one,
                decision=BarcodeDecisionType.INVALID,
                reason_code="BARCODE_INVALID",
                reason_message=f"条码格式错误: {pkg_id}",
            )

        ng_reason = self._match_ng_rule(pkg_id)
        if ng_reason is not None:
            reason_code, reason_message = ng_reason
            return BarcodeDecision(
                six_in_one=six_in_one,
                decision=BarcodeDecisionType.NG,
                reason_code=reason_code,
                reason_message=f"{reason_message}: {pkg_id}",
            )

        return BarcodeDecision(
            six_in_one=six_in_one,
            decision=BarcodeDecisionType.OK,
        )

    def _is_valid_pkg_id(self, pkg_id: str) -> bool:
        """校验 PkgID 是否合法。

        Todo: 需要根据最终业务规则调整校验逻辑。
        """

        if not pkg_id:
            return False
        return len(pkg_id) >= self.MIN_BARCODE_LENGTH

    def _match_ng_rule(self, pkg_id: str) -> tuple[str, str] | None:
        """匹配业务 NG 规则。"""

        normalized_pkg_id = pkg_id.upper()
        for keyword, reason in self.NG_RULE_KEYWORDS.items():
            if keyword in normalized_pkg_id:
                return reason
        return None

    def _find_field_containing_comma(self, six_in_one: SixInOne) -> str | None:
        """返回第一个包含逗号的字段名。"""

        for field_name, field_value in six_in_one.iter_business_fields():
            if isinstance(field_value, str) and "," in field_value:
                return field_name
        return None


barcode_decision_service = BarcodeDecisionService()
