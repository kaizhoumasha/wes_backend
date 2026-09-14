"""工作位：本点 -A 与当前经过一致才请求 WMS。"""

from .scan_types import ScanDecision, ScanFact, normal_bin_code


class Scan2Handler:
    def decide(self, fact: ScanFact) -> ScanDecision:
        if fact.role != "SCAN2":
            raise ValueError("SCAN2 handler received another role")
        code = normal_bin_code(fact.raw_bin_code, "-A")
        if code is None or fact.passage is None or code != fact.passage.bin_code or fact.passage.ng:
            return ScanDecision("MOVE_FORWARD", ng_reason="SCAN2_INVALID_OR_MISMATCHED_BIN_CODE")
        return ScanDecision("REQUEST_WMS", normal_bin_code=code)
