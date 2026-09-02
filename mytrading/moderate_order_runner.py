# -*- coding: utf-8 -*-
"""
D-2: moderate(배당) 자동매수 러너 — 게이트 통과 후 submit_order/mark_bought 연결.

⚠️ --live 를 줘야만 실제 발주 시도. 기본(dry-run)은 D-1과 동일하게 "발주 예정" 로그·알림만
   하고 submit_order 를 호출하지 않는다. --live 여도 vps 가 아니면(이중 확인) 발주 안 한다.

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
    KIS_MODE=vps uv run python mytrading/moderate_order_runner.py --live     # 실제 발주(모의계좌만)

계좌 선택: submit_order 자체는 계좌 인자를 안 받는다 — get_brokerage(account_name=...) 가
  반환하는 브로커리지 객체에 계좌가 이미 baked in 되어 있다. 그런데 get_brokerage 는
  user_key 파라미터가 없고 오직 환경변수 KIS_USER 로만 사용자를 고른다(common.py:_resolve_account).
  order_runner.py(free)는 이걸 account_name 없이 딱 한 번만 호출해 모든 타겟에 재사용하는데,
  moderate 는 계좌마다 다른 사용자일 수 있으므로 그 패턴을 그대로 베끼지 않고, 발주 직전에
  os.environ["KIS_USER"] = user_key 를 명시적으로 세팅한 뒤 get_brokerage(account_name=...)
  를 호출한다(아래 _place_order 참고).
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LOG_DIR = Path.home() / "moderate_order_log"


def _moderate_accounts(pf, mode: str) -> list:
    """moderate 배분액(>0)이 있는 (user_key, AccountInfo) 목록 — 현재 모드 기준.
    계좌체계 재설계 2-2b: 새 계좌명 API(Portfolio.accounts_of/AccountInfo) 기반으로
    전환 — "계좌|모드" 합성키 파싱 대신 info.mode/info.moderate 로 직접 필터한다.

    ⚠️ 발주(get_brokerage/_place_order)에는 반드시 info.legacy_name(kis_devlp.yaml
    의 실제 계좌명, 예:"일반증권")을 써야 한다 — info.name(allocations.yaml 의 새
    계좌명, 예:"모의투자증권")을 그대로 넘기면 kis_devlp 쪽에 매칭되는 이름이 없어
    조용히 엉뚱한 계좌로 폴백하는 위험이 있다(2-2 설계에서 확인됨, common.py의
    _resolve_account 가 이름 불일치 시 예외 없이 "주문가능한 첫 계좌"로 폴백)."""
    out = []
    for ukey, accts in pf.accounts_new.items():
        for info in accts.values():
            if info.mode == mode and info.moderate > 0:
                out.append((ukey, info))
    return out


def _place_order(user_key: str, account_name: str, code: str, qty: int) -> dict:
    """moderate 시장가 매수 1건 발주. 성공 시 mark_bought(code) 호출.
    반환: {"success": bool, "order_id": str|None, "error": str|None}.
    ⚠️ 호출 전 vps 재확인은 이 함수의 책임이 아니다 — 호출부(main)가 이미 확인한 뒤 불러야 한다."""
    import os as _os
    from mytrading.common import get_brokerage
    from kis_backtest.providers.base import OrderSide, OrderType

    # get_brokerage() 는 user_key 인자가 없고 KIS_USER 환경변수로만 사용자를 고른다
    # (order_runner.py 처럼 account_name 없이 한 번만 호출해 재사용하면 다른 사용자 계좌로
    #  잘못 나갈 수 있어서, 매 호출 직전 명시적으로 세팅한다).
    _os.environ["KIS_USER"] = user_key
    brk = get_brokerage(account_name=account_name)
    try:
        order = brk.submit_order(symbol=str(code), side=OrderSide.BUY,
                                 quantity=int(qty), order_type=OrderType.MARKET)
        from mytrading.position_state import mark_bought
        mark_bought(str(code))
        return {"success": True, "order_id": order.id, "error": None}
    except Exception as e:
        return {"success": False, "order_id": None, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="게이트 통과 후보 있으면 텔레그램 발송 (없으면 출력만)")
    ap.add_argument("--live", action="store_true",
                     help="실제 발주 (없으면 dry-run — '발주 예정' 로그·알림만, submit_order 호출 안 함)")
    ap.add_argument("--symbol", metavar="CODE",
                     help="지정 시 해당 종목코드 1개만 발주 대상으로 제한 (테스트용, 예: 054050)")
    args = ap.parse_args()

    # ① vps 강제 — common.init() 보다 먼저, 종목 순회 전에 스크립트 전체를 즉시 중단.
    #   KIS_MODE 환경변수를 안 줘도 ~/KIS/config/.telegram_mode 파일이 prod 면 resolve_mode()
    #   가 prod 를 반환할 수 있어서, 여기서 명시적으로 검사해야 안전하다.
    from mytrading.common import resolve_mode
    mode = resolve_mode()
    if mode != "vps":
        print(f"🚫 D-2는 vps(모의) 전용입니다 — 현재 모드: {mode}. 즉시 중단.")
        sys.exit(1)

    from mytrading.common import init
    init(require_confirm=False)

    from mytrading.trade_plan import build_plan
    from mytrading.portfolio import load_portfolio
    from mytrading.market_calendar import is_market_open, is_trading_hours
    from mytrading.position_state import bought_within_cadence
    from mytrading.order_pace import cadence_days_for

    today = date.today()
    pf = load_portfolio()
    accounts = _moderate_accounts(pf, mode)   # [(ukey, AccountInfo), ...]

    try:
        # daily_update.py 와 반대 폴백: D는 실발주라 "모르면 안 산다"(market_calendar.py의
        # 기존 원칙, A-3 cadence 게이트와 동일 관례) — 실패 시 정규장 아님으로 간주해 스킵.
        # is_trading_hours() 는 09:00~15:30 시각까지 추가로 검사 — cron 은 10:10 실행이라
        # 항상 통과, 야간/장외 수동 실행만 스킵되게 하려는 목적(개장일 판정은 기존 그대로).
        # ⚠️ 유저 무관 — 정규장 여부는 모두에게 동일하므로 유저 루프 밖에서 한 번만 계산.
        market_open = is_market_open() and is_trading_hours()
    except Exception as e:
        market_open = False
        print(f"  ⚠️ is_market_open/is_trading_hours 조회 실패 — 보수적으로 정규장 아님 처리: {e}")

    # confirm 2단계-4: 유저별로 build_plan(user_key=) 을 따로 호출해야 유저별
    # confirm(2단계-1)·스냅샷(2단계-3)이 반영된다 — accounts 를 유저 단위로 그룹화한다.
    # Owner 하나뿐이면 아래 루프가 정확히 1회 돌아서, 이전(build_plan 1회 + 전체
    # accounts 기준 첫 활성계좌)과 100% 동일한 경로를 탄다(회귀 0).
    accounts_by_user = {}
    for u, a in accounts:
        accounts_by_user.setdefault(u, []).append(a)

    print("=" * 50)
    print(f"[moderate_order_runner] {today}")
    print(f"  모드: {mode} / 정규장: {'열림' if market_open else '닫힘'}")
    accounts_disp = [(u, info.legacy_name) for u, info in accounts]  # 로그 표시용 — legacy_name 그대로(회귀 0)
    print(f"  moderate 배분 계좌: {accounts_disp if accounts_disp else '(없음)'}")

    results = []
    pending = []  # 게이트 전부 통과한 종목 — "발주 예정"

    if not accounts_by_user:
        # 계좌가 아예 없는 경우 — 예전과 동일하게 build_plan 은 그대로 돌려서 후보
        # 전체를 "계좌 없음"으로 스킵 기록한다(로그 형태를 예전과 그대로 맞춤).
        plans = build_plan("moderate", today)
        candidates = [p for p in plans if p.get("action") == "buy"]
        candidates.sort(key=lambda p: p.get("score") or 0, reverse=True)
        if args.symbol:
            target = str(args.symbol).zfill(6)
            candidates = [c for c in candidates if str(c["symbol"]).zfill(6) == target]
        print(f"\n  --- moderate 배분 계좌 없음 — 후보 {len(candidates)}개 전부 스킵 예정 ---")
        for c in candidates:
            row = {"symbol": c["symbol"], "name": c.get("name", c["symbol"]), "user": None,
                   "qty": c.get("qty", 0), "amount": c.get("amount", 0),
                   "reason": c.get("reason", ""),
                   "gates": {"trading_active": "skip(moderate 배분 계좌 없음)"},
                   "skip_reason": "moderate 배분 계좌 없음", "account": None,
                   "live_result": {"attempted": False, "success": None,
                                   "order_id": None, "error": None,
                                   "mark_bought_called": False}}
            print(f"    ⏭ {row['name']}({row['symbol']}) 스킵 — {row['skip_reason']}")
            results.append(row)
    else:
        for ukey, acc_list in accounts_by_user.items():
            plans = build_plan("moderate", today, user_key=ukey)
            candidates = [p for p in plans if p.get("action") == "buy"]
            # 총점(score_dividend.py 300점) 내림차순 — 없으면 0 취급(맨 뒤). 예산 소진 시
            # 점수 높은 종목이 먼저 시도되게 하려는 목적. ⚠️ used_amt 누적차감은 아직 없음
            # (별도 작업) — 정렬만. ⚠️ 유저가 둘 이상이면 이 정렬은 "그 유저 안"에서만
            # 적용된다(유저 간 우선순위는 미결정 — 2단계-4 문서 참고). Owner 하나면
            # 기존과 동일(전체=그 유저).
            candidates.sort(key=lambda p: p.get("score") or 0, reverse=True)
            if args.symbol:
                target = str(args.symbol).zfill(6)
                candidates = [c for c in candidates if str(c["symbol"]).zfill(6) == target]

            print(f"\n  --- 유저 {ukey} — 매수 후보 {len(candidates)}개 "
                  f"(전체 {len(plans)}종목 중) ---")

            for c in candidates:
                code = c["symbol"]
                name = c.get("name", code)
                style = c.get("style")
                gates = {}
                skip_reason = None
                account_used = None  # 게이트 ②를 통과했을 때만 채워짐 — 실제 발주 시 이 계좌로.

                # ② trading_active — 이 유저의 moderate 계좌 중 하나라도 켜져 있으면 통과
                # 2-2b: info.trading_active(새 API, AccountInfo) 로 직접 조회 — raw YAML
                # 재파싱 없음.
                active_accounts = [info for info in acc_list if info.trading_active]
                if not active_accounts:
                    gates["trading_active"] = "skip"
                    skip_reason = "trading_active 꺼짐"
                else:
                    gates["trading_active"] = "pass"
                    # build_plan 의 qty/budget 계산이 애초에 단일 계좌(그 유저 기본
                    # 스냅샷) 전제라, 한 유저가 moderate 계좌를 여럿 가져도 같은 qty 를
                    # 여러 계좌에 중복 발주하면 안 된다 — 첫 번째 활성 계좌 하나로만
                    # 낸다(유저 "내부" 단일계좌 전제, 추후 재검토 필요).
                    # ⚠️ 발주용이라 legacy_name(kis_devlp 실제 계좌명) — new name 아님.
                    account_used = (ukey, active_accounts[0].legacy_name)

                # ③ 정규장
                if skip_reason is None:
                    if not market_open:
                        gates["market_open"] = "skip"
                        skip_reason = "정규장 아님"
                    else:
                        gates["market_open"] = "pass"

                # ④ 주기(cadence) — 위 docstring 참고: build_plan 단계에서 이미 걸러진
                #    뒤라 이 시점엔 사실상 항상 pass. mark_bought 연결 전까진 실질적
                #    의미 없음.
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

                row = {"symbol": code, "name": name, "user": ukey, "qty": c.get("qty", 0),
                       "amount": c.get("amount", 0), "reason": c.get("reason", ""),
                       "gates": gates, "skip_reason": skip_reason, "account": account_used,
                       "live_result": {"attempted": False, "success": None,
                                       "order_id": None, "error": None,
                                       "mark_bought_called": False}}

                if skip_reason:
                    print(f"    ⏭ {name}({code}) 스킵 — {skip_reason}")
                else:
                    print(f"    ✅ {name}({code}) {c.get('qty', 0)}주 · "
                          f"{c.get('amount', 0):,.0f}원 — 발주 예정 (게이트 전부 통과)")
                    pending.append(row)

                results.append(row)

    live_attempted = False
    if pending and args.live:
        # ⚠️ 이중 방어 — 스크립트 시작부에서 이미 vps 를 확인했지만, 실제 발주 직전에 한 번 더.
        from mytrading.common import resolve_mode as _resolve_mode2
        if _resolve_mode2() != "vps":
            print(f"🚫 발주 직전 재확인 실패 — vps 아님({_resolve_mode2()}). 전체 발주 중단.")
            for r in pending:
                r["live_result"]["attempted"] = False
                r["live_result"]["error"] = "발주 직전 vps 재확인 실패"
        else:
            live_attempted = True
            for r in pending:
                u, a = r["account"]
                res = _place_order(u, a, r["symbol"], r["qty"])
                r["live_result"]["attempted"] = True
                r["live_result"]["success"] = res["success"]
                r["live_result"]["order_id"] = res["order_id"]
                r["live_result"]["error"] = res["error"]
                r["live_result"]["mark_bought_called"] = res["success"]
                try:
                    from mytrading.telegram import notify
                    if res["success"]:
                        print(f"  🟢 [실주문] {r['name']}({r['symbol']}) 접수 성공 "
                              f"— 주문번호 {res['order_id']}")
                        notify.notify_order_submitted(
                            f"{r['name']}({r['symbol']})", "BUY", r["qty"], "시장가")
                    else:
                        print(f"  🔴 [실주문 실패] {r['name']}({r['symbol']}) — {res['error']}")
                        notify.notify_error(f"{r['name']} 매수 실패", res["error"])
                except Exception as e:
                    print(f"    (알림 실패: {e})")

    if pending and args.notify and not live_attempted:
        lines = [f"<b>[moderate 발주 예정 · {today}]</b>"]
        for r in pending:
            lines.append(f"\n🟢 {r['name']}({r['symbol']}) {r['qty']}주 · "
                        f"약 {r['amount'] / 10000:,.0f}만원")
        lines.append("\n⚠️ dry-run — submit_order 미호출, 실제 발주 안 됨 (--live 로 실행 시 발주)")
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
            {"user": u, "account": info.legacy_name, "trading_active": info.trading_active}
            for u, info in accounts
        ],
        "n_candidates": len(results),   # results 는 유저별 candidates 를 전부 합친 것(불변식: 후보 1개당 row 1개)
        "n_pending": len(pending),
        "candidates": results,
        "live_flag": args.live,
        "submit_order_called": any(r["live_result"]["attempted"] for r in results),
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n로그 기록: {log_path}")


if __name__ == "__main__":
    main()
