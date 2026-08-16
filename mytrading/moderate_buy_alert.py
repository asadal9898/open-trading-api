# -*- coding: utf-8 -*-
"""
배당(moderate) 카테고리 매수 후보 알림 — build_plan() 결과를 읽어 알림·로그만 남긴다.

⚠️ 발주 없음. submit_order 등 주문 코드를 이 파일에 절대 추가하지 말 것 —
   실제 매수는 /매수, /분할매수 로 사람이 직접 실행.

실행:
    KIS_MODE=prod uv run python mytrading/moderate_buy_alert.py            # 확인만
    KIS_MODE=prod uv run python mytrading/moderate_buy_alert.py --notify   # 후보 있으면 텔레그램 발송
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

LOG_DIR = Path.home() / "moderate_buy_alert_log"


def _fmt_manwon(amount) -> str:
    """종목당 100만원 기준이라 금액 위주로 — 만원 단위로 반올림 표시."""
    return f"약 {float(amount) / 10000:,.0f}만원"


def _build_message(candidates: list, today: date) -> str:
    lines = [f"<b>[배당 매수 후보 · {today}]</b>"]
    for c in candidates:
        name = html.escape(str(c.get("name", c["symbol"])))
        reason = html.escape(str(c.get("reason", "")))
        lines.append(
            f"\n🟢 {name}({c['symbol']}) {c.get('qty', 0)}주 · {_fmt_manwon(c.get('amount', 0))}\n"
            f"   사유: {reason}"
        )
    lines.append("\n⚠️ dry-run — 실제 발주 안 됨, /매수 로 직접 실행")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="후보 있으면 텔레그램 발송 (없으면 출력만)")
    args = ap.parse_args()

    from mytrading import common
    common.init(require_confirm=False)
    from mytrading.trade_plan import build_plan

    today = date.today()
    plans = build_plan("moderate", today)
    candidates = [p for p in plans if p.get("action") == "buy"]

    print("=" * 50)
    print(f"[moderate_buy_alert] {today} — 매수 후보 {len(candidates)}개 "
          f"(전체 {len(plans)}종목 중)")
    for c in candidates:
        print(f"  🟢 {c.get('name', c['symbol'])}({c['symbol']}) {c.get('qty', 0)}주 · "
              f"{_fmt_manwon(c.get('amount', 0))} — {c.get('reason', '')}")

    notified = False
    if candidates:
        msg = _build_message(candidates, today)
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
        print("(후보 없음 — 알림 생략)")

    # 로그는 후보 유무·--notify 여부와 무관하게 항상 기록 ("돌았음" 자체를 남김)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.json"
    log_data = {
        "date": str(today),
        "count": len(candidates),
        "notified": notified,
        "candidates": [
            {"symbol": c["symbol"], "name": c.get("name", c["symbol"]),
             "qty": c.get("qty", 0), "amount": c.get("amount", 0),
             "reason": c.get("reason", "")}
            for c in candidates
        ],
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n로그 기록: {log_path}")


if __name__ == "__main__":
    main()
