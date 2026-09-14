"""退箱入队：身份或前序正常放行不确定时停箱。"""

from .scan_types import ScanDecision, ScanFact, normal_bin_code


class Scan4Handler:
    def decide(self, fact: ScanFact) -> ScanDecision:
        if fact.role != "SCAN4":
            raise ValueError("SCAN4 handler received another role")
        code = normal_bin_code(fact.raw_bin_code, "-B")
        passage = fact.passage
        if (
            code is None
            or passage is None
            or code != passage.bin_code
            or passage.ng
            or not passage.normal_authorized
            or not passage.scan3_forward
        ):
            return ScanDecision("HOLD")
        return ScanDecision("MOVE_FORWARD", normal_bin_code=code)
