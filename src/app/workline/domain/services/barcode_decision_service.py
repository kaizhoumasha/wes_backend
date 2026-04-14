"""条码业务判定领域服务。"""

from __future__ import annotations

from typing import ClassVar

from src.app.workline.domain.models import BarcodeDecision, BarcodeDecisionType


class BarcodeDecisionService:
    """负责条码提取、合法性校验与业务 NG 判定。"""

    MIN_BARCODE_LENGTH = 3
    NG_RULE_KEYWORDS: ClassVar[dict[str, tuple[str, str]]] = {
        "SIZENG": ("SCAN_NG_BY_RULE", "条码命中尺寸 NG 业务规则"),
        "THICKNESSNG": ("SCAN_NG_BY_RULE", "条码命中厚度 NG 业务规则"),
    }

    def evaluate_scan(
        self,
        *,
        barcode: str | None = None,
        lot_code: str | None = None,
        date_code: str | None = None,
        po_number: str | None = None,
        mfr_pn: str | None = None,
        product_no: str | None = None,
        qty: str | None = None,
    ) -> BarcodeDecision:
        """对扫码事件执行领域判定。"""

        barcodes = self._collect_barcodes(
            barcode=barcode,
            lot_code=lot_code,
            date_code=date_code,
            po_number=po_number,
            mfr_pn=mfr_pn,
            product_no=product_no,
            qty=qty,
        )
        primary_barcode = barcodes[0] if barcodes else ""

        if not self._is_valid_barcode(primary_barcode):
            return BarcodeDecision(
                barcode=primary_barcode,
                barcodes=barcodes,
                decision=BarcodeDecisionType.INVALID,
                reason_code="BARCODE_INVALID",
                reason_message=f"条码格式错误: {primary_barcode}",
            )

        ng_reason = self._match_ng_rule(primary_barcode)
        if ng_reason is not None:
            reason_code, reason_message = ng_reason
            return BarcodeDecision(
                barcode=primary_barcode,
                barcodes=barcodes,
                decision=BarcodeDecisionType.NG,
                reason_code=reason_code,
                reason_message=f"{reason_message}: {primary_barcode}",
            )

        return BarcodeDecision(
            barcode=primary_barcode,
            barcodes=barcodes,
            decision=BarcodeDecisionType.OK,
        )

    def _collect_barcodes(
        self,
        *,
        barcode: str | None,
        lot_code: str | None,
        date_code: str | None,
        po_number: str | None,
        mfr_pn: str | None,
        product_no: str | None,
        qty: str | None,
    ) -> list[str]:
        """按业务优先级收集条码字段。"""

        candidates = [
            barcode,
            lot_code,
            date_code,
            po_number,
            mfr_pn,
            product_no,
            qty,
        ]
        return [candidate for candidate in candidates if candidate]

    def _is_valid_barcode(self, barcode: str) -> bool:
        """校验主条码是否合法。"""

        if not barcode:
            return False
        if len(barcode) < self.MIN_BARCODE_LENGTH:
            return False
        return barcode.isalnum()

    def _match_ng_rule(self, barcode: str) -> tuple[str, str] | None:
        """匹配业务 NG 规则。"""

        normalized_barcode = barcode.upper()
        for keyword, reason in self.NG_RULE_KEYWORDS.items():
            if keyword in normalized_barcode:
                return reason
        return None


barcode_decision_service = BarcodeDecisionService()
