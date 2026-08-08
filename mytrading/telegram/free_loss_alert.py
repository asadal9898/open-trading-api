# -*- coding: utf-8 -*-
"""
자유종목(free_holdings) 손실 알림 — 보유 중인 자유종목이 임계 손실을 넘으면 텔레그램.

value_range 는 -50% 손절 규칙이 있으나, free(개인 재량) 종목엔 손절 규칙이 없음.
→ 손실이 커지면 알림만 (자동매도 아님, 판단은 사람).

실행: uv run python mytrading/free_loss_alert.py               # 확인만
      uv run python mytrading/free_loss_alert.py --threshold -10 --notify
"""
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _free_codes():
    """allocations.yaml free_holdings 의 종목 코드 집합 {code: name}."""
    import yaml
    path = _ROOT / "mytrading" / "configs" / "allocations.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    fh = data.get("free_holdings") or {}
    for _u, accts in fh.items():
        for _a, lst in (accts or {}).items():
            if isinstance(lst, list):
                for it in lst:
                    if isinstance(it, dict) and it.get("code"):
                        out[str(it["code"]).zfill(6)] = it.get("name", it["code"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=-10.0,
                    help="이 손실률(%%) 이하면 알림 (기본 -10)")
    ap.add_argument("--notify", action="store_true",
                    help="신호 시 텔레그램 발송 (없으면 출력만)")
    args = ap.parse_args()

    from mytrading import common
    common.init(require_confirm=False)
    from mytrading.common import get_brokerage
    from mytrading.account_snapshot import get_snapshot

    snap = get_snapshot(get_brokerage())
    free_codes = _free_codes()

    hits = []
    for h in snap.holdings:
        code = str(h.symbol).zfill(6)
        if code not in free_codes:
            continue  # 자유종목만
        if h.pnl_percent is not None and h.pnl_percent <= args.threshold:
            hits.append(h)

    if not hits:
        print(f"자유종목 손실 알림 없음 (임계 {args.threshold:.0f}%). "
              f"보유 자유종목 {sum(1 for h in snap.holdings if str(h.symbol).zfill(6) in free_codes)}개.")
        return

    # 알림 메시지
    lines = [f"\u26a0\ufe0f <b>자유종목 손실 알림</b> (임계 {args.threshold:.0f}%)"]
    for h in hits:
        lines.append(
            f"\n\U0001f4c9 {h.name}({h.symbol}) {h.pnl_percent:+.1f}%\n"
            f"  평단 {h.average_price:,.0f} → 현재 {h.current_price:,.0f} "
            f"({h.quantity}주, {h.market_value:,.0f}원)")
    lines.append("\n\u203b 참고용 신호입니다. 손절 여부는 직접 판단하세요.")
    msg = "\n".join(lines)

    print("=" * 50)
    print(msg.replace("<b>", "").replace("</b>", ""))
    if args.notify:
        try:
            from mytrading import notify
            notify.send_message(msg)
            print("\n(텔레그램 발송)")
        except Exception as e:
            print(f"\n(발송 실패: {e})")


if __name__ == "__main__":
    main()
