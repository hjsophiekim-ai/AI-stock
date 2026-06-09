"""한국 주식 호가단위 유틸.

KRX 기준 가격대별 호가가격단위 (국내주식 기준):
  2,000원 미만           : 1원
  2,000원~5,000원 미만   : 5원
  5,000원~20,000원 미만  : 10원
  20,000원~50,000원 미만 : 50원
  50,000원~200,000원 미만: 100원
  200,000원~500,000원 미만: 500원
  500,000원 이상         : 1,000원

참고: ETF/ETN/ELW는 별도 단위이나, 이 시스템은 해당 종목을 hard exclusion으로 제외한다.
"""
import math


_TICK_TABLE = [
    (2_000,    1),
    (5_000,    5),
    (20_000,   10),
    (50_000,   50),
    (200_000,  100),
    (500_000,  500),
    (math.inf, 1_000),
]


def get_tick_size(price) -> int:
    """가격에 해당하는 호가단위를 반환한다."""
    p = float(price)
    for threshold, tick in _TICK_TABLE:
        if p < threshold:
            return tick
    return 1_000


def adjust_price_to_tick(price, side: str = "buy", method: str = "floor") -> int:
    """가격을 호가단위에 맞게 보정한다.

    Args:
        price: 원래 가격
        side: "buy" 또는 "sell" (로그 목적, 보정 로직에는 미사용)
        method:
            "floor"   — 아래 유효 호가로 내림 (보수적 매수 기본값)
            "ceil"    — 위 유효 호가로 올림 (보수적 매도 기본값)
            "nearest" — 가장 가까운 유효 호가

    Returns:
        보정된 정수 가격 (1원 이상 보장)
    """
    p = int(price)
    if p <= 0:
        return p
    tick = get_tick_size(p)
    if method == "floor":
        adjusted = (p // tick) * tick
    elif method == "ceil":
        adjusted = math.ceil(p / tick) * tick
    else:  # nearest
        lower = (p // tick) * tick
        upper = lower + tick
        adjusted = lower if (p - lower) <= (upper - p) else upper
    return max(adjusted, tick)


def is_valid_tick_price(price) -> bool:
    """가격이 호가단위에 맞는지 확인한다."""
    p = int(price)
    if p <= 0:
        return False
    tick = get_tick_size(p)
    return p % tick == 0


if __name__ == "__main__":
    # 자가 테스트
    cases = [
        (900,    1,   "2,000 미만"),
        (2500,   5,   "2,000~5,000"),
        (7500,   10,  "5,000~20,000"),
        (30000,  50,  "20,000~50,000"),
        (80000,  100, "50,000~200,000"),
        (151651, 100, "50,000~200,000 (KB금융 예시)"),
        (250000, 500, "200,000~500,000"),
        (600000, 1000,"500,000 이상"),
    ]
    print(f"{'가격':>10} {'호가단위':>8} {'floor':>10} {'ceil':>10} {'valid':>6}")
    print("-" * 55)
    for p, expected_tick, label in cases:
        tick = get_tick_size(p)
        floored = adjust_price_to_tick(p, method="floor")
        ceiled = adjust_price_to_tick(p, method="ceil")
        valid = is_valid_tick_price(p)
        status = "OK" if tick == expected_tick else f"FAIL(expected {expected_tick})"
        print(f"{p:>10,} {tick:>8} {floored:>10,} {ceiled:>10,} {str(valid):>6}  [{label}] {status}")
