"""退箱检验：独立重新投入按有效扫码准入，当前处置不能被绕过。"""

from .scan_types import ScanDecision, ScanFact, normal_bin_code


class Scan3Handler:
    def decide(self, fact: ScanFact) -> ScanDecision:
        if fact.role != "SCAN3":
            raise ValueError("SCAN3 handler received another role")
        code = normal_bin_code(fact.raw_bin_code, "-B")
        passage = fact.passage
        if code is None or (
            passage is not None and (code != passage.bin_code or passage.ng or not passage.normal_authorized)
        ):
            return ScanDecision("MOVE_LEFT", ng_reason="SCAN3_NOT_NORMAL_AUTHORIZED")
        return ScanDecision("MOVE_FORWARD", normal_bin_code=code)
