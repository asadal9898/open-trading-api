"""
주문 속도(order_pace) 계산 — 국면별 분할주문(1-b)의 핵심 부품.

종목 style × 시장 국면 × 사용자 가중치 → "오늘 이 종목을 얼마나 사고/팔지" 산출.

구성:
  is_trade_day(cadence, asof)         : 오늘이 이 종목의 거래일인가? (cadence 판정)
  compute_slice(style, regime, direction, ...) : 실제 매매 비율(%) = slice 덧셈공식
  value_range_signal(symbol, asof)    : 소외주 52주 가격조건 (저점매수/고점매도 판단)

설정은 mytrading_config.yaml 의 order_pace 섹션에서 읽음.
  스타일: accumulate / momentum / value_range  (각 cadence, slice)
  regime_base: 국면별 buy/sell 배수 (bull/bear/toppish/sideways)
  user_weight: 스타일별 사용자 가중치 (0~1.0)
  max_slice: 최종 slice 상한

slice 공식 (덧셈):
  실제 slice = 스타일 slice × (regime_base[국면][방향] + user_weight[스타일])
  최종 = min(실제 slice, max_slice)

사용:
  from mytrading.order_pace import is_trade_day, compute_slice, value_range_signal
"""
from datetime import date
from typing import Optional

import sys
from pathlib import Path
# 직접 실행(python mytrading/order_pace.py) 시 repo 루트를 경로에 추가
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mytrading.common import CONFIG, get_regime

try:
    from mytrading.data_manager import get_52w_range
except Exception:
    get_52w_range = None  # data_manager 미가용 시 value_range 비활성


def _op() -> dict:
    """config 의 order_pace 섹션."""
    return CONFIG.get("order_pace", {}) or {}


# ──────────────────────────────────────────────────────────
# 5번: cadence 판정 — 오늘이 이 종목 거래일인가?
# ──────────────────────────────────────────────────────────
def is_trade_day(cadence: str, asof: Optional[date] = None) -> bool:
    """
    cadence 에 따라 오늘(asof) 이 거래일인지 판정.
      daily         : 매 영업일 (월~금 항상 True)
      weekly        : 주 1회 — 월요일
      weekly_2x     : 주 2회 — 월·목
      biweekly_even : 짝수 ISO 주차의 월요일
      biweekly_odd  : 홀수 ISO 주차의 월요일
    ※ 주말(토·일)은 항상 False (장이 안 열림).
    ※ 실제 휴장일 스킵은 상위(daily_update/market_calendar)에서 처리.
    """
    d = asof or date.today()
    wd = d.weekday()          # 월=0 ... 일=6
    if wd >= 5:               # 토/일
        return False
    iso_week = d.isocalendar()[1]
    cad = (cadence or "weekly").lower()

    if cad == "daily":
        return True
    if cad == "weekly":
        return wd == 0        # 월요일
    if cad == "weekly_2x":
        return wd in (0, 3)   # 월·목
    if cad == "biweekly_even":
        return wd == 0 and iso_week % 2 == 0
    if cad == "biweekly_odd":
        return wd == 0 and iso_week % 2 == 1
    # 알 수 없는 cadence → weekly 취급
    return wd == 0


def cadence_for(style: str) -> str:
    """스타일의 cadence (config). 없으면 weekly."""
    s = _op().get(style, {}) or {}
    return s.get("cadence", "weekly")


# A-3: cadence 문자열 → 일수. position_state.bought_within_cadence() 의 cadence_days 인자용.
_CADENCE_DAYS = {
    "daily": 1,
    "weekly": 7,
    "weekly_2x": 4,
    "biweekly_even": 14,
    "biweekly_odd": 14,
    "monthly": 30,  # 30일 근사 — 정확한 월 경계 필요시 재검토(현재 value_range=weekly라 미사용)
}


def cadence_days_for(style: str) -> int:
    """스타일의 cadence 를 '일수'로 변환 (A-3 주기 판정용). 모르는 cadence 는 7(주간) 취급.
    value_range 는 config 에 cadence 키가 없어 cadence_for() 기본값 weekly → 7일이 된다."""
    cad = cadence_for(style)
    return _CADENCE_DAYS.get(cad, 7)


# ──────────────────────────────────────────────────────────
# 6번: slice 계산 — 실제 매매 비율(%)
# ──────────────────────────────────────────────────────────
def compute_slice(style: str, regime: str, direction: str) -> float:
    """
    실제 매매 비율(%) = 스타일 slice × (regime_base[국면][방향] + user_weight[스타일][방향])
    상한 max_slice 적용. 결과 0 이면 '거래 안 함'.

    style:     accumulate / momentum / value_range
    regime:    bull / bear / toppish / sideways
    direction: "buy" / "sell"

    user_weight 는 방향별 분리: { buy: 0.5, sell: 0.0 } 형식.
      (구형식 스칼라 0.5 도 호환 — buy/sell 양쪽에 동일 적용)
    """
    op = _op()
    style_cfg = op.get(style, {}) or {}
    base_slice = float(style_cfg.get("slice", 0) or 0)

    regime_base = (op.get("regime_base", {}) or {}).get(regime, {}) or {}
    base_mult = float(regime_base.get(direction, 0) or 0)

    # user_weight: 방향별 dict 또는 구형식 스칼라
    uw_raw = (op.get("user_weight", {}) or {}).get(style, 0)
    if isinstance(uw_raw, dict):
        user_w = float(uw_raw.get(direction, 0) or 0)
    else:
        user_w = float(uw_raw or 0)   # 구형식: 양방향 동일

    # 덧셈 공식
    mult = base_mult + user_w
    result = base_slice * mult

    max_slice = float(op.get("max_slice", 100) or 100)
    return round(min(result, max_slice), 3)


# ──────────────────────────────────────────────────────────
# value_range: 소외주 52주 가격조건 (저점매수 / 고점매도)
# ──────────────────────────────────────────────────────────
# 평단 기준 보유 판단 (백테스트 검증: 익절 +15% / 물타기 -30% 1회 / 손절 -50%)
VR_TAKE_PROFIT = 15.0
VR_AVERAGE_DOWN = -30.0
VR_STOP_LOSS = -50.0


def position_action(pnl_percent: float, already_averaged: bool = False) -> dict:
    """보유 종목의 평단 대비 손익률로 매매 판단.

    검증(5년 백테스트, 승률 92%):
      +15% 이상  → 익절 (전량)
      -50% 이하  → 손절 (전량)
      -30% 이하  → 물타기 1회 (already_averaged 면 보류)
      그 외      → 보유

    ※ 손절(-50%) 이 물타기(-30%) 보다 우선. 순서 주의.
    반환: {"action": "sell"/"buy"/"hold", "reason": str, "kind": str}
    """
    if pnl_percent is None:
        return {"action": "hold", "reason": "손익률 없음", "kind": "none"}
    if pnl_percent >= VR_TAKE_PROFIT:
        return {"action": "sell",
                "reason": f"평단 대비 {pnl_percent:+.1f}% (익절 기준 +{VR_TAKE_PROFIT:.0f}%)",
                "kind": "take_profit"}
    if pnl_percent <= VR_STOP_LOSS:
        return {"action": "sell",
                "reason": f"평단 대비 {pnl_percent:+.1f}% (손절 기준 {VR_STOP_LOSS:.0f}%)",
                "kind": "stop_loss"}
    if pnl_percent <= VR_AVERAGE_DOWN:
        if already_averaged:
            return {"action": "hold",
                    "reason": f"평단 대비 {pnl_percent:+.1f}% (물타기 1회 소진)",
                    "kind": "averaged_done"}
        return {"action": "buy",
                "reason": f"평단 대비 {pnl_percent:+.1f}% (물타기 기준 {VR_AVERAGE_DOWN:.0f}%, 1회)",
                "kind": "average_down"}
    return {"action": "hold",
            "reason": f"평단 대비 {pnl_percent:+.1f}% (보유 유지)",
            "kind": "hold"}


def value_range_signal(symbol: str, asof: Optional[date] = None) -> dict:
    """
    소외주(value_range) 의 52주 가격조건 판단.
    반환: {
      "action": "buy" / "sell" / "hold",
      "reason": 설명,
      "range": get_52w_range 결과(있으면),
    }
    - 52주 최저가 +buy_zone% 이내 → buy (저점 근처)
    - 52주 최고가 -sell_zone% 이내 → sell (고점 근처, 알림+분할매도)
    - 그 사이 → hold (매매 안 함)
    """
    op = _op()
    vr = op.get("value_range", {}) or {}
    buy_zone = float(vr.get("buy_zone", 5) or 5)
    sell_zone = float(vr.get("sell_zone", 5) or 5)

    if get_52w_range is None:
        return {"action": "hold", "reason": "data_manager 미가용", "range": None}

    r = get_52w_range(symbol, asof=asof)
    if not r:
        return {"action": "hold", "reason": "데이터 없음", "range": None}

    # 저점 근처? (현재가가 52주 최저가 +buy_zone% 이내)
    if r["pct_from_low"] <= buy_zone:
        return {"action": "buy",
                "reason": f"52주 최저가 +{r['pct_from_low']:.1f}% (저점 근처, ≤{buy_zone:.0f}%)",
                "range": r}
    # ※ 52주 고점 기준 매도는 제거됨 (2026-07 백테스트 검증).
    #   검증된 매도 규칙은 '내 평단 대비 +15% 익절' 이며, 52주 고점은 매수가와 무관해
    #   손실 구간에서도 매도 신호가 날 수 있어 백테스트와 어긋남.
    #   보유 종목의 매도 판단은 position_action() 사용.
    return {"action": "hold",
            "reason": f"중간 구간 (저점+{r['pct_from_low']:.1f}%, 고점{r['pct_from_high']:.1f}%)",
            "range": r}


# ──────────────────────────────────────────────────────────
# 확인용 CLI
# ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from datetime import timedelta
    print("=== cadence 판정 (다음 7일) ===")
    base = date.today()
    for cad in ("daily", "weekly", "weekly_2x", "biweekly_even", "biweekly_odd"):
        days = []
        for i in range(14):
            d = base + timedelta(days=i)
            if is_trade_day(cad, d):
                days.append(d.strftime("%m/%d(%a)"))
        print(f"  {cad:14}: {', '.join(days[:5])}")

    print("\n=== slice 계산 예시 ===")
    for style in ("accumulate", "momentum", "value_range"):
        for regime in ("bull", "bear", "toppish"):
            buy = compute_slice(style, regime, "buy")
            sell = compute_slice(style, regime, "sell")
            print(f"  {style:12} {regime:8}: buy {buy:.2f}% / sell {sell:.2f}%")