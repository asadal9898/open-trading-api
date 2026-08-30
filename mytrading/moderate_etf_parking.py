# -*- coding: utf-8 -*-
"""
moderate 여유분 + cash(물타기 재원) 여유분 → 단기채 ETF 파킹 (`cash_plan.krw_etfs`).

⚠️ 발주는 --live 일 때만. 기본(dry-run)은 "파킹 예정" 로그·출력만 하고 submit_order 를
   호출하지 않는다. `MODERATE_FUNDING_DESIGN.md` 참고 — moderate 배분액 중 아직 배당주를
   못 산 여유분을 현금으로 놀리지 않고 단기채 ETF로 잠시 담아두는 용도. ETF 보유 자체가
   목적이 아니라 배당주 매수 전 임시 주차다.

moderate 가용예산 = category_amount("moderate") − holdings_value_for_category("moderate")
  − 최소현금 1,000,000원
  (MODERATE_FUNDING_DESIGN.md §2 — 100만원은 이 공식 안에서 한 번만 차감, 파킹 금액은 이
  가용예산 그 자체다.)
cash 여유분 = alloc.cash(total_equity) — moderate/free 배정 후 남는 계좌 잔여현금
  (2026-08-30 "자금 구조 확정" — moderate 여유분 + cash 여유분을 하나의 ETF 풀에 합쳐서
  파킹한다. §1 "장부 없음" 원칙대로 어느 게 누구 몫인지 구분하지 않고, 파킹 목표만 합산.
  ⚠️ cash 는 지금 자체 하한 없이 전액 파킹 대상이다 — moderate 의 100만원 같은 "자투리
  방지 최소단위"를 cash 에도 둘지는 미정(사용자 확인 필요, 일단 전액으로 구현).
  합산 목표(moderate 가용예산 + cash 여유분)가 0 이하면 파킹하지 않는다.

이 스크립트는 moderate_order_runner.py(D, 배당주 매수)와 분리 실행한다 — D가 먼저 돌아
그날 산 만큼 현금이 빠진 뒤의 상태를 반영해야 가용예산이 정확하다(§3). 같은 프로세스 안에서
D 뒤에 동기 호출하지 않고, cron에서 D보다 몇 분 뒤 별도 스케줄로 돌린다.

실행:
    KIS_MODE=vps uv run python mytrading/moderate_etf_parking.py            # dry-run, 출력만
    KIS_MODE=vps uv run python mytrading/moderate_etf_parking.py --notify   # 파킹 예정 있으면 텔레그램 알림
    KIS_MODE=vps uv run python mytrading/moderate_etf_parking.py --live     # 실제 매수(모의계좌만)
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
LOG_DIR = Path.home() / "moderate_etf_parking_log"
MIN_CASH_FLOOR = 1_000_000


def _moderate_accounts(pf, mode: str) -> list:
    """moderate 배분액(>0)이 있는 (user_key, account_name) 목록 — 현재 모드 기준.
    moderate_order_runner.py 의 동명 함수와 동일 패턴(독립 구현 — 서로 import 안 함)."""
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


def _load_krw_etfs() -> list:
    """allocations.yaml 최상위 cash_plan.krw_etfs. 없으면 빈 리스트."""
    import yaml
    try:
        data = yaml.safe_load(ALLOC_PATH.read_text(encoding="utf-8")) or {}
        return [dict(e) for e in ((data.get("cash_plan") or {}).get("krw_etfs") or [])]
    except Exception as e:
        print(f"[parking] cash_plan.krw_etfs 로드 실패: {e}")
        return []


def _parked_etf_cost_basis(snap, etf_codes: set) -> float:
    """이미 파킹된 ETF(krw_etfs 코드들)의 매입원가(cost_basis) 합.
    market_value(시가) 아닌 cost_basis 사용 — A-4(종목당 매입원가 상한)와 동일 이유
    (시세 노이즈 없이 "얼마를 이 용도에 배정했나"를 정확히 반영). 별도 장부 없이
    계좌 실잔고의 평단가 필드를 그대로 씀 — MODERATE_FUNDING_DESIGN.md §4-5 해결안."""
    if not snap:
        return 0.0
    return sum(h.cost_basis for h in snap.holdings if str(h.symbol) in etf_codes)


def _place_order(user_key: str, account_name: str, code: str, qty: int) -> dict:
    """ETF 시장가 매수 1건. 성공 시 {"success": True, "order_id": ...}, 실패 시 error 포함.
    moderate_order_runner.py 의 _place_order 와 동일 패턴(계좌 인자 문제 재확인: get_brokerage
    는 user_key 를 안 받고 KIS_USER 환경변수로만 사용자를 고른다 — 매 호출 전 명시 세팅)."""
    import os as _os
    from mytrading.common import get_brokerage
    from kis_backtest.providers.base import OrderSide, OrderType

    _os.environ["KIS_USER"] = user_key
    brk = get_brokerage(account_name=account_name)
    try:
        order = brk.submit_order(symbol=str(code), side=OrderSide.BUY,
                                 quantity=int(qty), order_type=OrderType.MARKET)
        return {"success": True, "order_id": order.id, "error": None}
    except Exception as e:
        return {"success": False, "order_id": None, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="파킹 예정 있으면 텔레그램 발송 (없으면 출력만)")
    ap.add_argument("--live", action="store_true",
                     help="실제 매수 (없으면 dry-run — '파킹 예정' 로그·알림만, submit_order 호출 안 함)")
    args = ap.parse_args()

    # ① vps 강제 — common.init() 보다 먼저. KIS_MODE 를 안 줘도 .telegram_mode 파일이
    #   prod 면 resolve_mode() 가 prod 를 반환할 수 있어서, 여기서 명시적으로 검사해야 안전하다.
    from mytrading.common import resolve_mode
    mode = resolve_mode()
    if mode != "vps":
        print(f"🚫 ETF 파킹은 vps(모의) 전용입니다 — 현재 모드: {mode}. 즉시 중단.")
        sys.exit(1)

    from mytrading.common import init
    init(require_confirm=False)

    from mytrading.trade_plan import holdings_value_for_category
    from mytrading.position_sizing import category_amount
    from mytrading.portfolio import load_portfolio
    from mytrading.common import get_brokerage
    from mytrading.account_snapshot import get_snapshot
    from find_dividend_stocks import _current_price

    today = date.today()
    pf = load_portfolio()
    accounts = _moderate_accounts(pf, mode)

    # ③ 가용예산 = category_amount − used_amt − 최소현금 100만원 (이 공식 안에서 1회만 차감)
    snap = get_snapshot(get_brokerage())   # 한 번만 조회 — used_amt·파킹액 계산 양쪽에 재사용
    total_budget = category_amount("moderate")
    used_amt = holdings_value_for_category("moderate", snap=snap)
    if total_budget is None:
        print("🚫 category_amount('moderate') 조회 실패 — 중단.")
        sys.exit(1)
    available = total_budget - used_amt - MIN_CASH_FLOOR

    # ③-2 cash 여유분 — alloc.cash(total_equity). category_amount() 는 cash 를 못 다룸
    #   (Allocation 에 cash 저장 필드가 없고 cash(total_equity) 메서드만 있음, 이전 확인됨).
    #   moderate 계좌가 여럿이어도 첫 번째 계좌만 본다(accounts[0], D 와 동일한 단일계좌
    #   전제 — 지금 실제로도 moderate 배분 계좌가 하나뿐이라 문제없음).
    cash_avail = 0.0
    if accounts:
        _u0, _a0 = accounts[0]
        _al = pf.allocation_for(_u0, _a0, mode)
        if _al is not None:
            cash_avail = _al.cash(float(snap.total_equity))
    combined_target = available + cash_avail

    print("=" * 50)
    print(f"[moderate_etf_parking] {today}")
    print(f"  모드: {mode}")
    print(f"  moderate 배분액: {total_budget:,.0f}원")
    print(f"  보유 배당주 평가액(used_amt): {used_amt:,.0f}원")
    print(f"  최소현금: {MIN_CASH_FLOOR:,.0f}원")
    print(f"  moderate 가용예산: {available:,.0f}원")
    print(f"  cash 여유분(alloc.cash, 하한 없음): {cash_avail:,.0f}원")
    print(f"  합산 파킹 목표: {combined_target:,.0f}원")
    print(f"  moderate 배분 계좌: {accounts if accounts else '(없음)'}")

    # ④ 합산 목표 ≤ 0 → 파킹 안 함. 0 초과면 "이미 파킹된 만큼"을 빼서 중복 파킹 방지
    #    (§4-5 해결안 — 가용예산 정의 자체는 불변, 이 스크립트의 목표치 계산만 보정)
    items = []
    parked_cost = 0.0
    to_park = 0.0
    if combined_target <= 0:
        print(f"\n(합산 목표 {combined_target:,.0f}원 ≤ 0 — 파킹 안 함)")
    else:
        etfs = _load_krw_etfs()
        etf_codes = {str(e.get("code", "")) for e in etfs}
        parked_cost = _parked_etf_cost_basis(snap, etf_codes)
        to_park = combined_target - parked_cost
        print(f"  이미 파킹된 ETF 매입원가: {parked_cost:,.0f}원")
        print(f"  추가로 파킹할 금액: {to_park:,.0f}원")

        if to_park <= 0:
            print("\n(이미 가용예산만큼 파킹돼 있음 — 추가 파킹 없음)")
        else:
            # ⑤ krw_etfs 비율대로 배분액·수량 계산 (to_park 기준 — available 아님)
            wsum = sum(e.get("weight", 0) for e in etfs) or 100
            print(f"\n  파킹 대상 ETF ({len(etfs)}종):")
            for e in etfs:
                code = str(e.get("code", ""))
                name = e.get("name", "")
                w = e.get("weight", 0)
                amt = to_park * w / wsum
                price = float(_current_price(code) or 0.0)
                qty = int(amt // price) if price > 0 else 0
                items.append({"code": code, "name": name, "weight": w,
                              "amount": amt, "price": price, "qty": qty,
                              "account": accounts[0] if accounts else None,
                              "live_result": {"attempted": False, "success": None,
                                              "order_id": None, "error": None}})
                if qty >= 1:
                    print(f"    · {name}({code}) {w}% — {qty}주 × {price:,.0f} = "
                          f"{qty * price:,.0f}원 (배분액 {amt:,.0f}원)")
                else:
                    print(f"    · {name}({code}) {w}% — 금액 부족(1주 미만, 배분액 {amt:,.0f}원)")

    # ⑥ --live 일 때만 실제 매수
    live_attempted = False
    pending = [it for it in items if it["qty"] >= 1]
    if pending and args.live:
        from mytrading.common import resolve_mode as _resolve_mode2
        if _resolve_mode2() != "vps":
            print(f"🚫 발주 직전 재확인 실패 — vps 아님({_resolve_mode2()}). 전체 발주 중단.")
            for it in pending:
                it["live_result"]["error"] = "발주 직전 vps 재확인 실패"
        elif not accounts:
            print("🚫 moderate 배분 계좌 없음 — 발주 중단.")
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
                    print(f"  🟢 [실주문] {it['name']}({it['code']}) 접수 성공 "
                          f"— 주문번호 {res['order_id']}")
                else:
                    print(f"  🔴 [실주문 실패] {it['name']}({it['code']}) — {res['error']}")
                try:
                    from mytrading.telegram import notify
                    if res["success"]:
                        notify.notify_order_submitted(it["code"], "매수", it["qty"], "시장가")
                    else:
                        notify.notify_error(f"{it['name']} 파킹 매수 실패", res["error"])
                except Exception as e:
                    print(f"    (알림 실패: {e})")

    if pending and args.notify and not live_attempted:
        lines = [f"<b>[moderate ETF 파킹 예정 · {today}]</b>",
                f"합산 파킹 목표 {combined_target:,.0f}원 (moderate {available:,.0f} + cash {cash_avail:,.0f})"]
        for it in pending:
            lines.append(f"\n🟢 {it['name']}({it['code']}) {it['qty']}주 · "
                        f"약 {it['qty'] * it['price'] / 10000:,.0f}만원")
        lines.append("\n⚠️ dry-run — submit_order 미호출, 실제 발주 안 됨 (--live 로 실행 시 발주)")
        try:
            from mytrading.telegram import notify
            notify.send_message("\n".join(lines))
        except Exception as e:
            print(f"(알림 실패: {e})")
    elif not pending:
        print("\n(파킹 예정 종목 없음 — 알림 생략)")

    total_park_amount = sum(it["qty"] * it["price"] for it in pending)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.json"
    log_data = {
        "date": str(today),
        "mode": mode,
        "category_amount": total_budget,
        "used_amt": used_amt,
        "min_cash_floor": MIN_CASH_FLOOR,
        "available": available,
        "cash_avail": cash_avail,
        "combined_target": combined_target,
        "parked_etf_cost_basis": parked_cost,
        "to_park": to_park,
        "moderate_accounts": accounts,
        "items": items,
        "total_park_amount": total_park_amount,
        "live_flag": args.live,
        "submit_order_called": any(it["live_result"]["attempted"] for it in items),
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  실제 파킹 예정 합계: {total_park_amount:,.0f}원")
    print(f"로그 기록: {log_path}")


if __name__ == "__main__":
    main()
