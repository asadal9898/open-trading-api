# -*- coding: utf-8 -*-
"""
주문 실행 러너 — allocations.yaml 의 buy_plan/sell_plan 을 실제 KIS 주문으로.
★ 실제 돈이 나가는 모듈. dry_run=True 기본, --execute 로만 실제 주문.
"""
import sys, argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
ALLOC_PATH = _ROOT / "mytrading" / "configs" / "allocations.yaml"


def _rt_load(path):
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _rt_dump(data, path):
    import yaml
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _collect_targets(data, only_user=None):
    targets = []
    fh = data.get("free_holdings") or {}
    for user, accts in fh.items():
        if only_user and user != only_user:
            continue
        for acc, lst in (accts or {}).items():
            if not isinstance(lst, list):
                continue
            for it in lst:
                if not isinstance(it, dict):
                    continue
                confirm = it.get("confirm")
                code = str(it.get("code", ""))
                if confirm == "SellRequested":
                    q = int(it.get("sell_qty", 0) or 0)
                    if q > 0:
                        targets.append((user, acc, it, "sell", q))
                    continue
                if confirm == "Approval":
                    plan = it.get("buy_plan") or {}
                    onetime = int(plan.get("onetime", 0) or 0)
                    if onetime > 0:
                        targets.append((user, acc, it, "buy_onetime", onetime))
                    split = plan.get("split") or {}
                    sq = int(split.get("qty", 0) or 0)
                    if sq > 0:
                        targets.append((user, acc, it, "buy_split", sq))
    return targets


def run_orders(dry_run=True, only_user=None):
    from mytrading import common
    common.init()
    from mytrading.common import (assert_can_order, current_account_info,
                                  get_brokerage, resolve_mode)
    mode = resolve_mode()
    is_paper = (mode == "vps")
    mode_txt = "모의투자(vps)" if is_paper else "🚨 실전투자(prod)"
    print("=" * 60)
    print(f"주문 실행 러너 — {mode_txt}")
    print(f"실행 모드: {'DRY-RUN (미리보기, 주문 안 함)' if dry_run else '★ 실제 주문 ★'}")
    print("=" * 60)
    if not assert_can_order():
        print("주문 불가 계좌 — 중단.")
        return
    info = current_account_info()
    print(f"계좌: {info['source']} ({info['account_no']})")
    if not is_paper and not dry_run:
        print("\n🚨🚨🚨 실전 계좌에 실제 주문이 나갑니다 🚨🚨🚨")
        print("  5초 후 진행합니다. 중단하려면 Ctrl+C.")
        import time
        time.sleep(5)
    data = _rt_load(ALLOC_PATH)
    targets = _collect_targets(data, only_user)
    if not targets:
        print("\n주문 대상 없음 (Approval+buy_plan 또는 SellRequested 종목 없음).")
        return
    print(f"\n주문 대상: {len(targets)}건\n" + "-" * 60)
    brk = None if dry_run else get_brokerage()
    if not dry_run:
        from kis_backtest.providers.base import OrderSide, OrderType
    changed = False
    for user, acc, it, action, qty in targets:
        code = str(it.get("code"))
        name = it.get("name", code)
        side_txt = "매도" if action == "sell" else "매수"
        kind_txt = {"buy_onetime": "일시", "buy_split": "분할1회", "sell": ""}[action]
        line = f"  {name}({code}) {side_txt} {kind_txt} {qty}주"
        if dry_run:
            print(f"[예정]{line}")
            continue
        try:
            side = OrderSide.SELL if action == "sell" else OrderSide.BUY
            order = brk.submit_order(symbol=code, side=side, quantity=qty, order_type=OrderType.MARKET)
            print(f"[주문]{line} → 접수 (주문번호 {order.id})")
            try:
                from mytrading import notify
                notify.notify_order_submitted(code, side_txt, qty, "시장가")
            except Exception as e:
                print(f"    (알림 실패: {e})")
            if action == "sell":
                it["confirm"] = "Sold"
                it.pop("sell_qty", None)
            elif action == "buy_onetime":
                it["confirm"] = "Bought"
                bp = it.get("buy_plan") or {}
                bp.pop("onetime", None)
                it["buy_plan"] = bp
            changed = True
        except Exception as e:
            print(f"[실패]{line} → {e}")
            try:
                from mytrading import notify
                notify.notify_error(f"{name} {side_txt} 주문 실패", str(e))
            except Exception:
                pass
    if changed and not dry_run:
        _rt_dump(data, ALLOC_PATH)
        print("\n주문 상태 저장 (중복 방지).")
    print("\n" + "=" * 60)
    if dry_run:
        print("DRY-RUN 완료. 실제 주문하려면 --execute 를 붙이세요.")
        print("  (실전 전 반드시 모의계좌 vps 에서 먼저 테스트)")
    else:
        print("주문 실행 완료. 체결 결과는 텔레그램/계좌에서 확인하세요.")
    print("=" * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="실제 주문 실행 (없으면 dry-run)")
    ap.add_argument("--user", default=None, help="특정 사용자만 (예: Owner)")
    args = ap.parse_args()
    run_orders(dry_run=not args.execute, only_user=args.user)
