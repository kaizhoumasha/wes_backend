"""人工拣料业务重试的唯一指数退避规则。"""


def rack_retry_delay_ms(prior_attempt: int) -> int:
    if prior_attempt < 0:
        raise ValueError("rack retry attempt must be nonnegative")
    return min(1000 * 2 ** min(prior_attempt, 6), 60_000)
