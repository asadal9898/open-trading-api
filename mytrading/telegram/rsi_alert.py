# -*- coding: utf-8 -*-
"""
코스피 RSI 과매도 신호 알림 — RSI(14) < 임계값이면 텔레그램 알림.

백테스트(rsi_kospi_backtest): RSI<25 → 5일 반등 승률 74.6% (+21%p 엣지).
단, 이상적 조건(당일 종가 진입, 비용 무시)이며 하락 초입엔 손실 위험(칼날).
→ 자동매매 아님. 관찰·참고용 신호만.

실행: uv run --with pandas python mytrading/rsi_alert.py
      uv run --with pandas python mytrading/rsi_alert.py --threshold 30 --notify
"""
import sys
import argparse
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

KOSPI_CSV = _ROOT / "backtester" / ".lean-workspace" / "data" / "index" / "kospi.csv"


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (기존 백테스트와 동일)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def load_kospi():
    """kospi.csv → DataFrame (YYYYMMDD,O,H,L,C,V, 헤더 없음)."""
    df = pd.read_csv(KOSPI_CSV, header=None,
                     names=["date", "o", "h", "l", "c", "v"])
    df["date"] = df["date"].astype(str)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=25.0,
                    help="RSI 임계값 (기본 25)")
    ap.add_argument("--notify", action="store_true",
                    help="신호 시 텔레그램 발송 (없으면 출력만)")
    args = ap.parse_args()

    df = load_kospi()
    df["rsi"] = rsi(df["c"], 14)
    last = df.iloc[-1]
    cur_rsi = last["rsi"]
    date = last["date"]
    close = last["c"]

    print(f"코스피 {date} 종가 {close:,.2f} — RSI(14) {cur_rsi:.1f}")

    if pd.isna(cur_rsi):
        print("RSI 계산 불가 (데이터 부족).")
        return

    if cur_rsi < args.threshold:
        msg = (f"\u26a0\ufe0f <b>코스피 RSI 과매도 신호</b>\n"
               f"{date} 종가 {close:,.0f}\n"
               f"RSI(14) = {cur_rsi:.1f} (&lt; {args.threshold:.0f})\n\n"
               f"\U0001f4ca 백테스트: RSI&lt;25 → 5일 반등 승률 74.6%\n"
               f"\u26a0\ufe0f 단, 하락 초입엔 손실 위험(칼날). 참고용 신호이며 "
               f"자동매매 아님. 최종 판단은 직접.")
        print("=" * 50)
        print("★ 과매도 신호 발생!")
        print(msg.replace("<b>", "").replace("</b>", "").replace("&lt;", "<"))
        if args.notify:
            try:
                from mytrading.telegram import notify
                notify.send_message(msg)
                print("(텔레그램 발송)")
            except Exception as e:
                print(f"(발송 실패: {e})")
    else:
        print(f"신호 없음 (RSI {cur_rsi:.1f} >= {args.threshold:.0f}). 알림 안 함.")


if __name__ == "__main__":
    main()
