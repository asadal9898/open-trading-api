"""
주문 계획 빌더 (1-b 7번) — 오늘 무엇을 얼마나 사고팔지 '계획'만 계산·출력.

⚠️ 실제 주문은 하지 않음. 계산·출력만. (백테스트·검증 후 별도로 주문 연결)

흐름:
  1. tradable_symbols(category) → confirm=Approval 종목만 (매매 대상)
  2. 각 종목 style 별 분기:
     - value_range : value_range_signal(52주 가격) → buy/sell/hold
                     buy/sell 이면 compute_slice 로 비율 산출
     - momentum    : is_trade_day(cadence) → 매매일이면 compute_slice(buy)
     - accumulate  : is_trade_day(cadence) → 매매일이면 compute_slice(buy) 정기매수
  3. 종목별 계획 출력: {symbol, name, style, action, slice_pct, reason}

cadence: universe 에 cadence 필드 있으면 사용, 없으면 style 기본값
  accumulate→weekly, momentum→daily, value_range→(신호 기반, cadence 무관)

사용:
  KIS_MODE=prod uv run python mytrading/trade_plan.py            # 전체 카테고리
  KIS_MODE=prod uv run python mytrading/trade_plan.py --category aggressive
"""
import sys
from pathlib import Path
from datetime import date

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mytrading.common import init, regime_for_symbol
from mytrading.portfolio import load_portfolio
from mytrading.order_pace import (is_trade_day, compute_slice,
                                 value_range_signal, position_action)

# style 별 기본 cadence (universe 에 cadence 필드 없을 때)
_DEFAULT_CADENCE = {
    "accumulate": "weekly",
    "momentum": "daily",
    "value_range": "daily",   # 실제론 신호로 판단, cadence 는 형식상
}


# 잔고 스냅샷 — 실행당 1회만 조회 (종목마다 부르면 API 낭비)
_SNAP = None
_SNAP_TRIED = False


def _snapshot():
    global _SNAP, _SNAP_TRIED
    if _SNAP_TRIED:
        return _SNAP
    _SNAP_TRIED = True
    try:
        from mytrading.common import get_brokerage
        from mytrading.account_snapshot import get_snapshot
        _SNAP = get_snapshot(get_brokerage())
    except Exception as e:
        print(f"[trade_plan] 잔고 조회 실패 — 보유 판단 생략: {e}")
        _SNAP = None
    return _SNAP


def _phase_ok(code) -> bool:
    """영업이익 국면이 매수 가능한가 (침체·판정불가면 False).
    실패 시 보수적으로 False — '모르면 안 산다'."""
    try:
        from mytrading.finance_data import _judge_phase, is_buyable_phase
        return is_buyable_phase(_judge_phase(code).get("phase"))
    except Exception as e:
        print(f"[trade_plan] 국면 판정 실패({code}) — 매수 보류: {e}")
        return False


def _apply_sizing(plan, category, price, snap, used_amt=0.0):
    """매수 계획에 수량·금액을 채운다. 산정 실패 시 매수를 보류한다.

    used_amt: 이 카테고리에서 이미 보유 중인 종목들의 평가액 합(build_plan 에서 카테고리당
      1회 계산해 넘어옴, confirm/auto_confirm 무관 — 순위 밀려 Paused/Rejected 된 보유분도 포함).
      예산 = category_amount(category) − used_amt (remaining) 을 plan_buy 에 직접 주입한다.
      held_count(개수) 기반 슬롯차감은 이미 same 정보(보유분)를 금액으로 반영한 뒤라
      또 쓰면 이중차감이 되므로, plan_buy 호출 시 held_count=0 으로 고정한다.
    """
    if not price or not snap:
        plan["reason"] += " (수량 산정 불가 — 가격·잔고 없음)"
        return

    # A-3: 주기 중복매수 방지 — is_trade_day(요일 고정) 대신 last_bought 기준으로 판정.
    # ⚠️ TODO(D 실발주 붙일 때): position_state.mark_bought() 호출부가 아직 없다.
    #   지금은 last_bought 가 항상 비어있어 이 게이트는 실질적으로 늘 통과(스킵 안 함)한다.
    try:
        from mytrading.position_state import bought_within_cadence
        from mytrading.order_pace import cadence_days_for
        code = plan.get("symbol")
        cadence_days = cadence_days_for(plan.get("style"))
        skip = bool(code) and bought_within_cadence(code, cadence_days)
    except Exception as e:
        print(f"[trade_plan] cadence 판정 실패({plan.get('symbol')}) — 보수적으로 매수 보류: {e}")
        skip = True   # 모르면 안 산다 — 이 저장소의 기존 관례(_phase_ok 등)와 동일
    if skip:
        plan["action"] = "hold"
        plan["slice_pct"] = 0.0
        plan["reason"] = f"{plan['reason']} → 매수 보류: 이번 주기 이미 매수 (cadence {cadence_days}일)"
        return

    try:
        from mytrading.position_sizing import plan_buy, category_amount
        cat = category or "moderate"
        total_budget = category_amount(cat)
        if total_budget is None:
            plan["reason"] += f" (수량 산정 실패: {cat} 배분금액 없음)"
            return
        remaining = total_budget - used_amt
        if remaining <= 0:
            plan["action"] = "hold"
            plan["slice_pct"] = 0.0
            plan["reason"] = (f"{plan['reason']} → 매수 보류: {cat} 배분액 소진"
                              f"(보유평가액 {used_amt:,.0f}원이 배분액 {total_budget:,.0f}원 도달)")
            return
        sz = plan_buy(cat, price=float(price), total_equity=float(snap.total_equity),
                      held_count=0, budget=remaining)
    except Exception as e:
        plan["reason"] += f" (수량 산정 실패: {str(e)[:40]})"
        return
    plan["qty"] = sz.get("qty", 0)
    plan["amount"] = sz.get("amount", 0.0)
    plan["per_symbol"] = sz.get("per_symbol")
    plan["slots"] = sz.get("slots")
    if not sz.get("ok"):
        plan["action"] = "hold"
        plan["slice_pct"] = 0.0
        plan["reason"] = f"{plan['reason']} → 매수 보류: {sz.get('reason','')}"


def _plan_for_symbol(s: dict, asof: date = None, category=None, used_amt: float = 0.0) -> dict:
    """종목 1개의 오늘 매매 계획. s 는 universe 종목 dict."""
    asof = asof or date.today()
    code = s["code"]
    name = s.get("name", code)
    style = s.get("style", "accumulate")
    cadence = s.get("cadence") or _DEFAULT_CADENCE.get(style, "weekly")

    regime = regime_for_symbol(code)

    plan = {
        "symbol": code, "name": name, "style": style,
        "regime": regime, "action": "hold", "slice_pct": 0.0, "reason": "",
    }

    if style == "value_range":
        # 보유 여부로 갈림 (백테스트 검증 전략)
        #   미보유 → 52주 저점+buy_zone% AND 국면OK → 매수
        #   보유   → 평단 대비 +15% 익절 / -30% 물타기 1회 / -50% 손절
        snap = _snapshot()
        h = snap.holding_of(code) if snap else None
        if h is not None:
            from mytrading.position_state import was_averaged_down
            pa = position_action(h.pnl_percent, was_averaged_down(code))
            plan["action"] = pa["action"]
            plan["reason"] = pa["reason"]
            plan["kind"] = pa.get("kind")
            plan["pnl_percent"] = h.pnl_percent
            if pa["action"] == "sell":
                plan["slice_pct"] = 100.0                       # 전량 청산
            elif pa["action"] == "buy":
                plan["slice_pct"] = compute_slice(style, regime, "buy")
                _apply_sizing(plan, category, h.current_price, snap, used_amt)
        else:
            sig = value_range_signal(code, asof)
            action = sig.get("action", "hold")
            reason = sig.get("reason", "")
            if action == "buy" and not _phase_ok(code):
                action, reason = "hold", "국면 침체/판정불가 — 매수 보류"
            plan["action"] = action
            plan["reason"] = reason
            if action == "buy":
                plan["slice_pct"] = compute_slice(style, regime, action)
                _apply_sizing(plan, category, (sig.get("range") or {}).get("last"),
                              snap, used_amt)
    else:
        # momentum / accumulate : cadence 로 매매일 판정 → 매수
        if is_trade_day(cadence, asof):
            sl = compute_slice(style, regime, "buy")
            if sl > 0:
                plan["action"] = "buy"
                plan["slice_pct"] = sl
                plan["reason"] = f"{cadence} 매매일 ({regime})"
            else:
                plan["reason"] = f"slice=0 (국면 {regime})"
        else:
            plan["reason"] = f"{cadence} 비매매일"

    return plan


def build_plan(category: str = None, asof: date = None) -> list:
    """카테고리(없으면 전체)의 Approval 종목 매매 계획 리스트."""
    asof = asof or date.today()
    pf = load_portfolio()
    cats = [category] if category else ["aggressive", "moderate", "safe"]

    plans = []
    for cat in cats:
        approval_codes = set(pf.tradable_symbols(cat))   # Approval 만 — 매수 "후보" 순회용
        _snap = _snapshot()
        # confirm/auto_confirm 무관 — 카테고리 전체 코드(순위 밀려 Paused/Rejected 된 보유분도
        # 포함해야 "이미 배분액에서 얼마를 썼는지"가 정확해진다).
        _cat_codes = {x["code"] for x in pf.names(cat)}
        used_amt = sum(hh.market_value for hh in (_snap.holdings if _snap else [])
                       if hh.symbol in _cat_codes)   # 카테고리당 1회 계산, 후보 전체가 공유
        for s in pf.names(cat):
            if s["code"] in approval_codes:
                plans.append(_plan_for_symbol(s, asof, cat, used_amt))
    return plans


def main():
    args = sys.argv[1:]
    category = None
    if "--category" in args:
        i = args.index("--category")
        if i + 1 < len(args):
            category = args[i + 1]

    init(require_confirm=False)
    asof = date.today()
    plans = build_plan(category, asof)

    print("=" * 60)
    print(f"  주문 계획 ({asof}) — {category or '전체'} · ⚠️ 계산만, 주문 안 함")
    print("=" * 60)
    if not plans:
        print("  Approval 종목 없음.")
        return

    act_count = {"buy": 0, "sell": 0, "hold": 0}
    for p in plans:
        act_count[p["action"]] = act_count.get(p["action"], 0) + 1
        mark = {"buy": "🟢 매수", "sell": "🔴 매도", "hold": "⚪ 대기"}.get(p["action"], p["action"])
        sl = f" {p['slice_pct']:.1f}%" if p["slice_pct"] > 0 else ""
        print(f"  {mark}{sl}  {p['symbol']} {p['name']} [{p['style']}/{p['regime']}]")
        print(f"        → {p['reason']}")
    print("-" * 60)
    print(f"  매수 {act_count['buy']} · 매도 {act_count['sell']} · 대기 {act_count['hold']}")
    print("  ※ 실제 주문은 별도 — 이 계획은 검증·확인용입니다.")


if __name__ == "__main__":
    main()