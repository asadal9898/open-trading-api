# -*- coding: utf-8 -*-
"""
D-1: moderate(배당) 자동매수 러너 — 게이트 + dry-run 뼈대. submit_order 는 아직 연결 안 함.

⚠️ 발주 없음. submit_order/OrderSide/OrderType 등 실제 주문 코드를 이 파일에 추가하지 말 것
   — 그건 D-2에서, --live 로만, 아래 TODO 자리에.

게이트 순서 (① 은 스크립트 전체 중단, ②~④ 는 종목별 스킵):
  ① vps 강제        — resolve_mode() != "vps" 면 즉시 스크립트 전체 중단 (prod 방어)
  ② trading_active  — moderate 배분액이 있는 계좌 중 켜진 계좌가 하나도 없으면 스킵
  ③ 정규장          — market_calendar.is_market_open() 이 False 면 스킵
  ④ 주기(cadence)   — bought_within_cadence() 가 True 면 스킵
     ⚠️ build_plan()/_apply_sizing() 내부에 이미 동일한 cadence 게이트가 있어서(A-3),
        action=="buy" 로 여기 candidates 에 들어온 시점엔 이미 통과된 상태다 — 이 ④ 는
        사실상 항상 pass 로 찍힌다(현재 재현 불가능한 이중 체크). mark_bought() 가 아직
        아무 데서도 호출되지 않아 실질적 의미는 없지만, D-2에서 mark_bought 연결 후
        방어적 이중 확인(로그 가시성) 용도로 남겨둔다.

금액/수량은 build_plan()→plan_buy() 가 이미 계산해서 qty/amount 에 넣어준 값을 그대로 쓴다
(여기서 다시 예산 계산 안 함).

실행:
    KIS_MODE=vps uv run python mytrading/moderate_order_runner.py            # dry-run, 출력만
    KIS_MODE=vps uv run python mytrading/moderate_order_runner.py --notify   # 통과 후보 텔레그램 알림
    KIS_MODE=vps uv run python mytrading/moderate_order_runner.py --live     # (D-1 미구현) 경고만, 발주 안 함
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
LOG_DIR = Path.home() / "moderate_order_log"


def _trading_active(alloc_data: dict, user_key: str, account: str) -> bool:
    """계좌별 보수(배당) 자동매매 활성 여부. 기본 False(중지).
    telegram_bot.py 의 _trading_active() 와 같은 필드(allocations.yaml
    users.{key}.accounts.{acc}.trading_active)를 읽는 독립 구현 —
    텔레그램 봇 모듈을 import 하지 않기 위해(free_loss_alert.py 등과 같은 패턴)."""
    accs = ((alloc_data.get("users") or {}).get(user_key, {}) or {}).get("accounts", {}) or {}
    acc = accs.get(account, {}) or {}
    return bool(acc.get("trading_active", False))


def _moderate_accounts(pf, mode: str) -> list:
    """moderate 배분액(>0)이 있는 (user_key, account_name) 목록 — 현재 모드 기준.
    build_plan() 은 계좌 정보를 안 주므로, "누구 계좌로 낼 주문인가"는 allocations.yaml 의
    moderate 배분액으로 역산한다."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="게이트 통과 후보 있으면 텔레그램 발송 (없으면 출력만)")
    ap.add_argument("--live", action="store_true",
                     help="(D-1 미구현) 실제 발주 — 지금은 경고만 출력하고 발주 안 함")
    args = ap.parse_args()

    # ① vps 강제 — common.init() 보다 먼저, 종목 순회 전에 스크립트 전체를 즉시 중단.
    #   KIS_MODE 환경변수를 안 줘도 ~/KIS/config/.telegram_mode 파일이 prod 면 resolve_mode()
    #   가 prod 를 반환할 수 있어서, 여기서 명시적으로 검사해야 안전하다.
    from mytrading.common import resolve_mode
    mode = resolve_mode()
    if mode != "vps":
        print(f"🚫 D-1은 vps(모의) 전용입니다 — 현재 모드: {mode}. 즉시 중단.")
        sys.exit(1)

    if args.live:
        print("⚠️ --live 는 아직 미구현입니다 (D-1은 게이트+dry-run 뼈대만) — 발주하지 않습니다.")
        print("   submit_order 연결은 D-2에서.")

    from mytrading.common import init
    init(require_confirm=False)

    from mytrading.trade_plan import build_plan
    from mytrading.portfolio import load_portfolio
    from mytrading.market_calendar import is_market_open
    from mytrading.position_state import bought_within_cadence
    from mytrading.order_pace import cadence_days_for

    today = date.today()
    pf = load_portfolio()
    accounts = _moderate_accounts(pf, mode)

    import yaml
    alloc_raw = yaml.safe_load(ALLOC_PATH.read_text(encoding="utf-8")) or {}

    market_open = is_market_open()

    plans = build_plan("moderate", today)
    candidates = [p for p in plans if p.get("action") == "buy"]

    print("=" * 50)
    print(f"[moderate_order_runner] {today} — 매수 후보 {len(candidates)}개 "
          f"(전체 {len(plans)}종목 중)")
    print(f"  모드: {mode} / 정규장: {'열림' if market_open else '닫힘'}")
    print(f"  moderate 배분 계좌: {accounts if accounts else '(없음)'}")

    results = []
    pending = []  # 게이트 전부 통과한 종목 — "발주 예정"

    for c in candidates:
        code = c["symbol"]
        name = c.get("name", code)
        style = c.get("style")
        gates = {}
        skip_reason = None

        # ② trading_active — moderate 계좌 중 하나라도 켜져 있으면 통과
        if not accounts:
            gates["trading_active"] = "skip(moderate 배분 계좌 없음)"
            skip_reason = "moderate 배분 계좌 없음"
        else:
            active_accounts = [(u, a) for u, a in accounts
                               if _trading_active(alloc_raw, u, a)]
            if not active_accounts:
                gates["trading_active"] = "skip"
                skip_reason = "trading_active 꺼짐"
            else:
                gates["trading_active"] = "pass"

        # ③ 정규장
        if skip_reason is None:
            if not market_open:
                gates["market_open"] = "skip"
                skip_reason = "정규장 아님"
            else:
                gates["market_open"] = "pass"

        # ④ 주기(cadence) — 위 docstring 참고: build_plan 단계에서 이미 걸러진 뒤라
        #    이 시점엔 사실상 항상 pass. mark_bought 연결 전까진 실질적 의미 없음.
        if skip_reason is None:
            cadence_days = cadence_days_for(style)
            try:
                already = bought_within_cadence(code, cadence_days)
            except Exception as e:
                already = True  # 모르면 안 산다
                print(f"  ⚠️ {name}({code}) cadence 판정 실패 — 보수적으로 스킵: {e}")
            if already:
                gates["cadence"] = f"skip(cadence {cadence_days}일 이내 매수기록)"
                skip_reason = f"주기 내 이미 매수(cadence {cadence_days}일)"
            else:
                gates["cadence"] = "pass"

        row = {"symbol": code, "name": name, "qty": c.get("qty", 0),
               "amount": c.get("amount", 0), "reason": c.get("reason", ""),
               "gates": gates, "skip_reason": skip_reason}

        if skip_reason:
            print(f"  ⏭ {name}({code}) 스킵 — {skip_reason}")
        else:
            print(f"  ✅ {name}({code}) {c.get('qty', 0)}주 · {c.get('amount', 0):,.0f}원 "
                  f"— 발주 예정 (게이트 전부 통과)")
            pending.append(row)

        results.append(row)

    # TODO D-2: 여기서 pending 을 순회하며 submit_order 연결 (--live 일 때만).
    #   try:
    #       order = brk.submit_order(symbol=code, side=OrderSide.BUY, quantity=qty,
    #                                order_type=OrderType.MARKET)
    #       position_state.mark_bought(code)   # 성공(예외 없음) 시에만
    #   except Exception as e:
    #       notify.notify_error(...)            # 실패 — mark_bought 호출 안 함
    #   지금은 submit_order 호출도, mark_bought 호출도 없다 — 로그·알림만.

    if pending and args.notify:
        lines = [f"<b>[moderate 발주 예정 · {today}]</b>"]
        for r in pending:
            lines.append(f"\n🟢 {r['name']}({r['symbol']}) {r['qty']}주 · "
                        f"약 {r['amount'] / 10000:,.0f}만원")
        lines.append("\n⚠️ dry-run(D-1) — submit_order 미연결, 실제 발주 안 됨")
        try:
            from mytrading.telegram import notify
            notify.send_message("\n".join(lines))
        except Exception as e:
            print(f"(알림 실패: {e})")
    elif not pending:
        print("\n(발주 예정 종목 없음 — 알림 생략)")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.json"
    log_data = {
        "date": str(today),
        "mode": mode,
        "market_open": market_open,
        "moderate_accounts": [
            {"user": u, "account": a, "trading_active": _trading_active(alloc_raw, u, a)}
            for u, a in accounts
        ],
        "n_candidates": len(candidates),
        "n_pending": len(pending),
        "candidates": results,
        "live_flag": args.live,
        "submit_order_called": False,  # D-1: 항상 False. D-2 연결 후에도 실제 호출 여부를 기록.
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n로그 기록: {log_path}")


if __name__ == "__main__":
    main()
