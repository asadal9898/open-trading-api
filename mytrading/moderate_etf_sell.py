# -*- coding: utf-8 -*-
"""
moderate 배당주 매수 재원 마련 — ETF(cash_plan.krw_etfs) 매도.

⚠️ 발주는 --live 일 때만. 기본(dry-run)은 "매도 예정" 로그·출력만 하고 submit_order 를
   호출하지 않는다. `MODERATE_FUNDING_DESIGN.md` §5 참고.

이 스크립트는 moderate_order_runner.py(D, 배당주 매수)보다 **먼저** 돈다 — D가 오늘 뭘 사고
싶어할지 build_plan("moderate")으로 선제 재현해서, 신호가 있고 가용예산이 남아있으면 ETF를
팔아 재원을 미리 마련해둔다. D 자신은 건드리지 않는다(예수금 체크 없이 그대로 둠 — D를
고치면 이미 실사용 중인 매일 --live 코드에 회귀 위험).

매도 트리거 = (가용예산 > 0) AND (오늘 배당주 매수 신호 있음, D와 동일 게이트로 재확인).
매도 목표금액 = min(1,000,000원, ETF 풀 시가 합) — 신호 개수·가용예산 크기와 무관하게 항상
이 고정 단위(§5). 여러 종목이 동시에 신호를 내면 한 번의 매도로 다 못 살 수 있음(리스크로
문서화됨, MODERATE_FUNDING_DESIGN.md §7 참고).

파킹(moderate_etf_parking.py)과 값의 종류가 다르다: 파킹은 "얼마를 배정했나"를 묻는
질문이라 cost_basis(원금)를 쓰지만, 매도는 "지금 팔면 얼마가 들어오나"를 물으므로
market_value(시가)를 쓴다 — MODERATE_FUNDING_DESIGN.md §4-5에서 이미 "서로 다른 질문이라
안 얽힌다"고 정리된 부분.

실행:
    KIS_MODE=vps uv run python mytrading/moderate_etf_sell.py            # dry-run, 출력만
    KIS_MODE=vps uv run python mytrading/moderate_etf_sell.py --notify   # 매도 예정 있으면 텔레그램 알림
    KIS_MODE=vps uv run python mytrading/moderate_etf_sell.py --live     # 실제 매도(모의계좌만)
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

ALLOC_PATH = _ROOT / "mytrading" / "configs" / "allocations.yaml"
LOG_DIR = Path.home() / "moderate_etf_sell_log"
MIN_CASH_FLOOR = 1_000_000
SELL_TARGET_CAP = 1_000_000


def _moderate_accounts(pf, mode: str) -> list:
    """moderate 배분액(>0)이 있는 (user_key, account_name) 목록 — 현재 모드 기준.
    moderate_order_runner.py/moderate_etf_parking.py 의 동명 함수와 동일 패턴
    (독립 구현 — 서로 import 안 함)."""
    out = []
    for ukey, accts in pf.allocations.items():
        for key, al in accts.items():
            if "|" in key:
                acc_name, acc_mode = key.rsplit("|", 1)
            else:
                acc_name, acc_mode = key, "vps"
            if acc_mode == mode and al.moderate > 0:
                out.append((ukey, acc_name))
    return out


def _trading_active(alloc_data: dict, user_key: str, account: str) -> bool:
    """계좌별 보수(배당) 자동매매 활성 여부. moderate_order_runner.py 와 동일 독립 구현."""
    accs = ((alloc_data.get("users") or {}).get(user_key, {}) or {}).get("accounts", {}) or {}
    acc = accs.get(account, {}) or {}
    return bool(acc.get("trading_active", False))


def _load_krw_etfs() -> list:
    """allocations.yaml 최상위 cash_plan.krw_etfs. 없으면 빈 리스트."""
    import yaml
    try:
        data = yaml.safe_load(ALLOC_PATH.read_text(encoding="utf-8")) or {}
        return [dict(e) for e in ((data.get("cash_plan") or {}).get("krw_etfs") or [])]
    except Exception as e:
        print(f"[sell] cash_plan.krw_etfs 로드 실패: {e}")
        return []


def _etf_pool_state(snap, etfs: list) -> dict:
    """krw_etfs 각 종목의 (보유수량, 시가, 평가액) — 계좌 실잔고 기준.
    시가(market_value) 사용 — 파킹의 cost_basis 와 다른 질문(파일 상단 docstring 참고)."""
    from find_dividend_stocks import _current_price
    out = {}
    held_by_code = {str(h.symbol): h for h in (snap.holdings if snap else [])}
    for e in etfs:
        code = str(e.get("code", ""))
        h = held_by_code.get(code)
        qty = h.quantity if h else 0
        price = float(h.current_price) if h else float(_current_price(code) or 0.0)
        market_value = h.market_value if h else 0.0
        out[code] = {"name": e.get("name", ""), "weight": e.get("weight", 0),
                     "qty": qty, "price": price, "market_value": market_value}
    return out


def _place_order(user_key: str, account_name: str, code: str, qty: int) -> dict:
    """ETF 시장가 매도 1건. 성공 시 {"success": True, "order_id": ...}, 실패 시 error 포함.
    moderate_order_runner.py/moderate_etf_parking.py 의 _place_order 와 동일 패턴
    (계좌 인자 문제 재확인: get_brokerage 는 user_key 를 안 받고 KIS_USER 환경변수로만
    사용자를 고른다 — 매 호출 전 명시 세팅)."""
    import os as _os
    from mytrading.common import get_brokerage
    from kis_backtest.providers.base import OrderSide, OrderType

    _os.environ["KIS_USER"] = user_key
    brk = get_brokerage(account_name=account_name)
    try:
        order = brk.submit_order(symbol=str(code), side=OrderSide.SELL,
                                 quantity=int(qty), order_type=OrderType.MARKET)
        return {"success": True, "order_id": order.id, "error": None}
    except Exception as e:
        return {"success": False, "order_id": None, "error": str(e)}


def compute_sell_plan(pool: dict) -> list:
    """§5 매도 계산. pool: {code: {name,weight,qty,price,market_value}}.
    반환: [{code,name,weight,qty,price,market_value_sold}, ...] (qty>0 인 것만 의미 있음)."""
    pool_value = sum(v["market_value"] for v in pool.values())
    plan = []
    if pool_value <= 0:
        return plan

    if pool_value < SELL_TARGET_CAP:
        # 전량매도 — 비율 계산 안 함 (단수 때문에 비율대로 나누면 못 다 팔 수 있음)
        for code, v in pool.items():
            plan.append({"code": code, "name": v["name"], "weight": v["weight"],
                         "qty": v["qty"], "price": v["price"],
                         "reason": "전량매도(ETF 풀 시가 합 < 100만원)"})
    else:
        target = min(SELL_TARGET_CAP, pool_value)
        wsum = sum(v["weight"] for v in pool.values()) or 100
        for code, v in pool.items():
            alloc = target * v["weight"] / wsum
            qty = min(v["qty"], int(alloc // v["price"])) if v["price"] > 0 else 0
            plan.append({"code": code, "name": v["name"], "weight": v["weight"],
                         "qty": qty, "price": v["price"],
                         "reason": f"목표 {target:,.0f}원의 {v['weight']}% 배분"})
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="매도 예정 있으면 텔레그램 발송 (없으면 출력만)")
    ap.add_argument("--live", action="store_true",
                     help="실제 매도 (없으면 dry-run — '매도 예정' 로그·알림만, submit_order 호출 안 함)")
    args = ap.parse_args()

    # ① vps 강제
    from mytrading.common import resolve_mode
    mode = resolve_mode()
    if mode != "vps":
        print(f"🚫 ETF 매도는 vps(모의) 전용입니다 — 현재 모드: {mode}. 즉시 중단.")
        sys.exit(1)

    from mytrading.common import init
    init(require_confirm=False)

    from mytrading.trade_plan import build_plan, holdings_value_for_category
    from mytrading.position_sizing import category_amount
    from mytrading.portfolio import load_portfolio
    from mytrading.market_calendar import is_market_open, is_trading_hours
    from mytrading.common import get_brokerage
    from mytrading.account_snapshot import get_snapshot

    today = date.today()
    pf = load_portfolio()
    accounts = _moderate_accounts(pf, mode)

    import yaml
    alloc_raw = yaml.safe_load(ALLOC_PATH.read_text(encoding="utf-8")) or {}

    snap = get_snapshot(get_brokerage())

    # ③ 오늘 배당주 매수 신호 있나 — D 를 선제 재현 (게이트: trading_active/정규장, D 와 동일)
    plans = build_plan("moderate", today)
    buy_candidates = [p for p in plans if p.get("action") == "buy"]

    active_accounts = [(u, a) for u, a in accounts if _trading_active(alloc_raw, u, a)]
    try:
        # market_calendar.is_trading_hours() 는 정책 없는 순수 판정(예외 전파) —
        # 여기서 fail-closed 로 감싼다: 개장일이어도 09:00~15:30 밖(장외 수동 실행)이면
        # 매도 안 함. cron 미등록 상태라 지금은 수동 실행에만 영향.
        market_open = is_market_open() and is_trading_hours()
    except Exception as e:
        market_open = False
        print(f"  ⚠️ is_market_open/is_trading_hours 조회 실패 — 보수적으로 정규장 아님 처리: {e}")

    signal_exists = bool(buy_candidates) and bool(active_accounts) and market_open

    # ④ 가용예산 = category_amount − used_amt − 최소현금 100만원
    total_budget = category_amount("moderate")
    if total_budget is None:
        print("🚫 category_amount('moderate') 조회 실패 — 중단.")
        sys.exit(1)
    used_amt = holdings_value_for_category("moderate", snap=snap, pf=pf)
    available = total_budget - used_amt - MIN_CASH_FLOOR

    print("=" * 50)
    print(f"[moderate_etf_sell] {today}")
    print(f"  모드: {mode} / 정규장: {'열림' if market_open else '닫힘'}")
    print(f"  moderate 배분 계좌: {accounts if accounts else '(없음)'} "
          f"(trading_active 켜짐: {active_accounts if active_accounts else '없음'})")
    print(f"  오늘 배당주 매수 후보: {len(buy_candidates)}개 "
          f"{[c['symbol'] for c in buy_candidates] if buy_candidates else ''}")
    print(f"  가용예산: {available:,.0f}원")
    print(f"  매도 트리거(가용예산>0 AND 신호 있음): {available > 0 and signal_exists}")

    # ⑤ 트리거 안 맞으면 매도 안 함
    sell_plan = []
    pool = {}
    if not (available > 0 and signal_exists):
        reason = []
        if available <= 0:
            reason.append(f"가용예산 {available:,.0f}원 ≤ 0")
        if not signal_exists:
            reason.append("오늘 매수 신호 없음(또는 trading_active/정규장 게이트 미통과)")
        print(f"\n(매도 안 함 — {', '.join(reason)})")
    else:
        # ⑥ 매도 계산 (§5)
        etfs = _load_krw_etfs()
        pool = _etf_pool_state(snap, etfs)
        pool_value = sum(v["market_value"] for v in pool.values())
        print(f"\n  ETF 풀 시가 합: {pool_value:,.0f}원")
        sell_plan = compute_sell_plan(pool)
        print(f"  매도 계획 ({len(sell_plan)}종):")
        for it in sell_plan:
            if it["qty"] >= 1:
                print(f"    · {it['name']}({it['code']}) {it['weight']}% — {it['qty']}주 × "
                      f"{it['price']:,.0f} = {it['qty'] * it['price']:,.0f}원 ({it['reason']})")
            else:
                print(f"    · {it['name']}({it['code']}) {it['weight']}% — 매도 없음(보유 0 또는 "
                      f"배분액 1주 미만) ({it['reason']})")

    for it in sell_plan:
        it["account"] = accounts[0] if accounts else None
        it["live_result"] = {"attempted": False, "success": None, "order_id": None, "error": None}

    pending = [it for it in sell_plan if it["qty"] >= 1]

    # ⑦ --live 일 때만 실제 매도
    live_attempted = False
    if pending and args.live:
        from mytrading.common import resolve_mode as _resolve_mode2
        if _resolve_mode2() != "vps":
            print(f"🚫 발주 직전 재확인 실패 — vps 아님({_resolve_mode2()}). 전체 매도 중단.")
            for it in pending:
                it["live_result"]["error"] = "발주 직전 vps 재확인 실패"
        elif not accounts:
            print("🚫 moderate 배분 계좌 없음 — 매도 중단.")
        else:
            live_attempted = True
            u, a = accounts[0]
            for it in pending:
                res = _place_order(u, a, it["code"], it["qty"])
                it["live_result"]["attempted"] = True
                it["live_result"]["success"] = res["success"]
                it["live_result"]["order_id"] = res["order_id"]
                it["live_result"]["error"] = res["error"]
                if res["success"]:
                    print(f"  🟢 [실주문] {it['name']}({it['code']}) 매도 접수 성공 "
                          f"— 주문번호 {res['order_id']}")
                else:
                    print(f"  🔴 [실주문 실패] {it['name']}({it['code']}) — {res['error']}")
                try:
                    from mytrading.telegram import notify
                    if res["success"]:
                        notify.notify_order_submitted(it["code"], "매도", it["qty"], "시장가")
                    else:
                        notify.notify_error(f"{it['name']} 매도 실패", res["error"])
                except Exception as e:
                    print(f"    (알림 실패: {e})")

    if pending and args.notify and not live_attempted:
        lines = [f"<b>[moderate ETF 매도 예정 · {today}]</b>",
                f"가용예산 {available:,.0f}원, 배당주 매수 후보 {len(buy_candidates)}개"]
        for it in pending:
            lines.append(f"\n🔴 {it['name']}({it['code']}) {it['qty']}주 · "
                        f"약 {it['qty'] * it['price'] / 10000:,.0f}만원")
        lines.append("\n⚠️ dry-run — submit_order 미호출, 실제 매도 안 됨 (--live 로 실행 시 매도)")
        try:
            from mytrading.telegram import notify
            notify.send_message("\n".join(lines))
        except Exception as e:
            print(f"(알림 실패: {e})")
    elif not pending:
        print("\n(매도 예정 종목 없음 — 알림 생략)")

    total_sell_amount = sum(it["qty"] * it["price"] for it in pending)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.json"
    log_data = {
        "date": str(today),
        "mode": mode,
        "market_open": market_open,
        "moderate_accounts": accounts,
        "n_buy_candidates": len(buy_candidates),
        "buy_candidate_symbols": [c["symbol"] for c in buy_candidates],
        "signal_exists": signal_exists,
        "category_amount": total_budget,
        "used_amt": used_amt,
        "min_cash_floor": MIN_CASH_FLOOR,
        "available": available,
        "etf_pool": pool,
        "sell_plan": sell_plan,
        "total_sell_amount": total_sell_amount,
        "live_flag": args.live,
        "submit_order_called": any(it["live_result"]["attempted"] for it in sell_plan),
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  실제 매도 예정 합계: {total_sell_amount:,.0f}원")
    print(f"로그 기록: {log_path}")


if __name__ == "__main__":
    main()
