# -*- coding: utf-8 -*-
"""
코스피 RSI + 200일 이동평균 조합 검증.

두 가지 가설:
  A. 추세 필터: RSI 과매도 매수를 200일선 '위'(상승장)에서만 하면 더 나은가?
     (RSI 약점 = 하락장에서 과매도가 계속 과매도 → 추세 필터로 거름)
  B. 200일선 근접 반등: 가격이 200일선 근처로 내려오면 반등하나?
     (실전 관찰: 200일선에서 반등 많음)

방법 (오늘 원칙): 룩어헤드 방지 + 기저 대비 엣지.
실행: uv run python mytrading/research_backtest/rsi_ma200_kospi.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
KOSPI = _ROOT / "backtester" / ".lean-workspace" / "data" / "index" / "kospi.csv"


def load_kospi() -> pd.DataFrame:
    df = pd.read_csv(KOSPI, header=None, names=["date", "o", "h", "l", "c", "v"])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values("date").reset_index(drop=True)


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100 - (100 / (1 + ag/al))


def fwd(close, d):
    return close.shift(-d) / close - 1.0


def stat(df, mask, label, HZ=(5, 20, 60)):
    out = {"label": label, "n": int(mask.sum())}
    for h in HZ:
        fr = df[f"fwd{h}"][mask].dropna()
        out[f"{h}d"] = None if len(fr) == 0 else {
            "mean": fr.mean()*100, "win": (fr > 0).mean()*100, "n": len(fr)}
    return out


def show(r, base=None, HZ=(5, 20, 60)):
    print(f"\n[{r['label']}]  신호 {r['n']}회")
    for h in HZ:
        v = r.get(f"{h}d")
        if v is None:
            print(f"  {h:>2}일후: 표본없음"); continue
        line = f"  {h:>2}일후: 평균 {v['mean']:+.2f}%  승률 {v['win']:.1f}%  (n={v['n']})"
        if base and base.get(f"{h}d"):
            b = base[f"{h}d"]
            line += f"   |엣지 수익{v['mean']-b['mean']:+.2f}%p 승률{v['win']-b['win']:+.1f}%p"
        print(line)


def main():
    df = load_kospi()
    df["rsi"] = rsi(df["c"], 14)
    df["ma200"] = df["c"].rolling(200).mean()
    df["dist200"] = (df["c"] / df["ma200"] - 1.0) * 100   # 200일선 대비 이격(%)
    HZ = (5, 20, 60)
    for h in HZ:
        df[f"fwd{h}"] = fwd(df["c"], h)

    valid = df["rsi"].notna() & df["ma200"].notna()
    base = stat(df, valid, "기저(전체)", HZ)
    print(f"코스피 {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    print("\n" + "="*64)
    print("■ 기저")
    show(base, None, HZ)

    # ── 가설 A: RSI 과매도 + 추세 필터 ──
    print("\n" + "="*64)
    print("■ A. RSI<25 과매도 매수 — 추세(200일선) 필터 유무 비교")
    m_all = valid & (df["rsi"] < 25)
    show(stat(df, m_all, "RSI<25 (전체)", HZ), base, HZ)
    m_up = valid & (df["rsi"] < 25) & (df["c"] > df["ma200"])
    show(stat(df, m_up, "RSI<25 & 200일선 위(상승장)", HZ), base, HZ)
    m_dn = valid & (df["rsi"] < 25) & (df["c"] <= df["ma200"])
    show(stat(df, m_dn, "RSI<25 & 200일선 아래(하락장)", HZ), base, HZ)

    # ── 가설 B: 200일선 근접 반등 ──
    print("\n" + "="*64)
    print("■ B. 200일선 근접 반등 — 200일선 부근(±%)에서 반등하나?")
    # 200일선 아래로 내려왔다가 근접 (이격 -3%~0%, 즉 살짝 아래)
    for lo, hi, lbl in [(-3, 0, "200일선 살짝 아래(-3%~0%)"),
                        (-1, 1, "200일선 근접(±1%)"),
                        (0, 3, "200일선 살짝 위(0~+3%)")]:
        m = valid & (df["dist200"] >= lo) & (df["dist200"] < hi)
        show(stat(df, m, lbl, HZ), base, HZ)

    # ── 가설 A+B 결합: 200일선 근처 + 과매도 ──
    print("\n" + "="*64)
    print("■ A+B. 200일선 근처(±5%) & RSI 과매도(<30) 동시")
    m = valid & (df["dist200"].abs() < 5) & (df["rsi"] < 30)
    show(stat(df, m, "200일선±5% & RSI<30", HZ), base, HZ)

    print("\n" + "="*64)
    print("■ 해석")
    print("  · A: '200일선 위' 엣지 > '아래' 엣지 면 → 추세필터 유효")
    print("       (하락장 과매도는 반등 약할 것 = RSI 약점 확인)")
    print("  · B: 200일선 근접 구간이 기저보다 높으면 → 지지선 반등 실재")
    print("  · 표본(n) 적으면 우연 주의. 결합할수록 n 급감")


if __name__ == "__main__":
    main()