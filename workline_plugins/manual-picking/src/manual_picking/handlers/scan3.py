"""退箱检验：只有本次经过的确定正常授权才向前。"""

from .scan_types import ScanDecision, ScanFact, normal_bin_code


class Scan3Handler:
    def decide(self, fact: ScanFact) -> ScanDecision:
        if fact.role != "SCAN3":
            raise ValueError("SCAN3 handler received another role")
        code = normal_bin_code(fact.raw_bin_code, "-B")
        passage = fact.passage
        if code is None or passage is None or code != passage.bin_code or passage.ng or not passage.normal_authorized:
            return ScanDecision("MOVE_LEFT", ng_reason="SCAN3_NOT_NORMAL_AUTHORIZED")
        return ScanDecision("MOVE_FORWARD", normal_bin_code=code)
