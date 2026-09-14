"""入口：本点 -B 有效向前，否则 NG 向右。"""

from .scan_types import ScanDecision, ScanFact, normal_bin_code, scanned_bin_identity


class Scan1Handler:
    def decide(self, fact: ScanFact) -> ScanDecision:
        if fact.role != "SCAN1":
            raise ValueError("SCAN1 handler received another role")
        code = normal_bin_code(fact.raw_bin_code, "-B")
        if code is None:
            return ScanDecision(
                "MOVE_RIGHT",
                normal_bin_code=scanned_bin_identity(fact.raw_bin_code),
                ng_reason="SCAN1_INVALID_BIN_CODE",
            )
        return ScanDecision("MOVE_FORWARD", normal_bin_code=code)
