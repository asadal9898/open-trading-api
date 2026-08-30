# -*- coding: utf-8 -*-
"""
moderate 배당주 -30% 물타기 후보 알림 — 1단계(알림만, 승인·실행은 다음 단계).

⚠️ 발주 없음. submit_order 등 주문 코드를 이 파일에 절대 추가하지 말 것 —
   이 스크립트는 build_plan() 이 낸 kind="average_down" 후보를 사람이 보도록
   알리기만 한다. 승인 버튼·콜백·자동 매도/매수 실행은 다음 단계(반자동 cash
   물타기 2단계)에서 별도로 붙인다. §1 "장부 없음" 원칙 — cash 잔액을 이 스크립트가
   추적·차감하지 않는다. 매 실행 시점의 alloc.cash(te) 실시간값만 참고 표시하고,
   물타기 여부·재원 판단은 사람이 한다(MODERATE_FUNDING_DESIGN.md 참고).

실행:
    KIS_MODE=vps uv run python mytrading/moderate_avgdown_alert.py            # dry-run, 출력만
    KIS_MODE=vps uv run python mytrading/moderate_avgdown_alert.py --notify   # 후보 있으면 텔레그램 발송
"""
import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LOG_DIR = Path.home() / "moderate_avgdown_alert_log"
A4_CAP = 2_000_000


def _moderate_accounts(pf, mode: str) -> list:
    """moderate 배분액(>0)이 있는 (user_key, account_name) 목록 — 현재 모드 기준.
    moderate_order_runner.py/moderate_etf_parking.py/moderate_etf_sell.py 의 동명 함수와
    동일 패턴(독립 구현 — 서로 import 안 함)."""
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


def _fmt_manwon(amount) -> str:
    return f"약 {float(amount) / 10000:,.0f}만원"


def _build_message(candidates: list, cash_avail: float, today: date) -> str:
    lines = [f"<b>[배당 물타기 후보 · {today}]</b>",
             f"cash(물타기 재원) 여유분: {cash_avail:,.0f}원 — 참고용, 재원 판단은 직접"]
    for c in candidates:
        name = html.escape(str(c["name"]))
        reason = html.escape(str(c["reason"]))
        lines.append(
            f"\n🔵 {name}({c['symbol']}) 평단 대비 {c['pnl_percent']:+.1f}%\n"
            f"   보유원가(cost_basis): {c['current_cost']:,.0f}원\n"
            f"   물타기 예정: {c['qty']}주 · {_fmt_manwon(c['amount'])}\n"
            f"   A-4 여유(200만 상한 대비): {c['a4_remaining']:,.0f}원\n"
            f"   사유: {reason}"
        )
    lines.append("\n⚠️ 알림만 — 승인·매도·매수 없음. submit_order 미호출.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="물타기 후보 있으면 텔레그램 발송 (없으면 출력만)")
    args = ap.parse_args()

    # ① vps 강제
    from mytrading.common import resolve_mode
    mode = resolve_mode()
    if mode != "vps":
        print(f"🚫 물타기 알림은 vps(모의) 전용입니다 — 현재 모드: {mode}. 즉시 중단.")
        sys.exit(1)

    from mytrading.common import init
    init(require_confirm=False)

    from mytrading.trade_plan import build_plan
    from mytrading.portfolio import load_portfolio
    from mytrading.common import get_brokerage
    from mytrading.account_snapshot import get_snapshot

    today = date.today()
    pf = load_portfolio()
    accounts = _moderate_accounts(pf, mode)
    snap = get_snapshot(get_brokerage())

    # ② build_plan("moderate") → kind=="average_down" AND action=="buy" 만
    #    (kind 는 A-4 로 action 이 hold 로 바뀐 뒤에도 남아있을 수 있어 action 도 같이 검사 —
    #    실제로 지금 매수 가능한 후보만 알림 대상으로 삼는다)
    plans = build_plan("moderate", today)
    candidates_raw = [p for p in plans if p.get("kind") == "average_down" and p.get("action") == "buy"]

    # ③ cash 여유분 — alloc.cash(total_equity). accounts[0] 만 본다(D/파킹/매도와 동일 단일계좌 전제)
    cash_avail = 0.0
    if accounts:
        u0, a0 = accounts[0]
        al = pf.allocation_for(u0, a0, mode)
        if al is not None:
            cash_avail = al.cash(float(snap.total_equity))

    candidates = []
    for p in candidates_raw:
        h = snap.holding_of(p["symbol"])
        current_cost = h.cost_basis if h else 0.0
        candidates.append({
            "symbol": p["symbol"], "name": p.get("name", p["symbol"]),
            "pnl_percent": p.get("pnl_percent") or 0.0,
            "current_cost": current_cost,
            "qty": p.get("qty", 0), "amount": p.get("amount", 0.0),
            "a4_remaining": A4_CAP - current_cost,
            "reason": p.get("reason", ""),
        })

    print("=" * 50)
    print(f"[moderate_avgdown_alert] {today}")
    print(f"  모드: {mode}")
    print(f"  moderate 배분 계좌: {accounts if accounts else '(없음)'}")
    print(f"  cash(물타기 재원) 여유분: {cash_avail:,.0f}원")
    print(f"  물타기 후보: {len(candidates)}개 (전체 {len(plans)}종목 중)")
    for c in candidates:
        print(f"  🔵 {c['name']}({c['symbol']}) {c['pnl_percent']:+.1f}% · "
              f"보유원가 {c['current_cost']:,.0f}원 · 물타기예정 {c['qty']}주/"
              f"{_fmt_manwon(c['amount'])} · A-4여유 {c['a4_remaining']:,.0f}원 — {c['reason']}")

    notified = False
    if candidates:
        msg = _build_message(candidates, cash_avail, today)
        print("\n--- 알림 문구 ---")
        print(msg.replace("<b>", "").replace("</b>", ""))
        if args.notify:
            try:
                from mytrading.telegram import notify
                notified = notify.send_message(msg)
                print(f"\n(텔레그램 발송: {'성공' if notified else '실패'})")
            except Exception as e:
                print(f"\n(발송 실패: {e})")
        else:
            print("\n(--notify 없음 — 발송 생략)")
    else:
        print("\n(물타기 대상 없음 — 알림 생략)")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.json"
    log_data = {
        "date": str(today),
        "mode": mode,
        "moderate_accounts": accounts,
        "cash_avail": cash_avail,
        "count": len(candidates),
        "notified": notified,
        "candidates": candidates,
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n로그 기록: {log_path}")


if __name__ == "__main__":
    main()
