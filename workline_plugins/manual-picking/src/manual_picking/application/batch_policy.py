"""人工拣料 CTU 批次的纯业务优先级。"""

from wes_plugin_sdk import BinInboundBatchIntent, BinReturnBatchIntent, BinReturnCandidate, wms_operations


def choose_next_batch(
    *,
    operation_id: str,
    workline_code: str,
    task_id: str,
    rack_id: str,
    rack_face: str,
    return_bins: tuple[str, ...],
    return_location: str,
    return_retry_due: bool,
    allow_inbound: bool,
) -> BinReturnBatchIntent | BinInboundBatchIntent | None:
    if return_bins and return_retry_due:
        return wms_operations.outbound_bin_return_batch(
            operation_id=operation_id,
            workline_code=workline_code,
            rack_id=rack_id,
            rack_face=rack_face,
            return_candidates=tuple(
                BinReturnCandidate(sequence_no, bin_code, return_location)
                for sequence_no, bin_code in enumerate(return_bins[:4], 1)
            ),
        )
    if allow_inbound:
        return wms_operations.outbound_bin_inbound_batch(
            operation_id=operation_id,
            task_id=task_id,
            rack_id=rack_id,
            rack_face=rack_face,
            max_bin_count=4,
        )
    return None


__all__ = ["choose_next_batch"]
