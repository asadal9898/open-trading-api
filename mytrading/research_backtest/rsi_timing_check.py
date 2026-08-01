# -*- coding: utf-8 -*-
"""
RSI<25 시기 편중 검증 — 진짜 엣지인가, 몇 번의 폭락 우연인가?

RSI<25 신호(코스피, 36년 121회)가:
  - 연도별로 어떻게 분포하나 (골고루 vs 특정 폭락기 집중)
  - 대폭락기(2008·2020·2026) 제외해도 승률이 유지되나
  - 각 신호의 실제 날짜·5일후 수익률 (눈으로 편중 확인)

실행: uv run python mytrading/research_backtest/rsi_timing_check.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
KOSPI = _ROOT / "backtester" / ".lean-workspace" / "data" / "index" / "kospi.csv"


def load_kospi():
    df = pd.read_csv(KOSPI, header=None, names=["date", "o", "h", "l", "c", "v"])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values("date").reset_index(drop=True)


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100 - (100 / (1 + ag/al))


def summarize(fr: pd.Series, label: str):
    fr = fr.dropna()
    if len(fr) == 0:
        print(f"  {label}: 표본없음"); return
    print(f"  {label}: n={len(fr)}  평균 {fr.mean()*100:+.2f}%  "
          f"승률 {(fr>0).mean()*100:.1f}%  중앙값 {fr.median()*100:+.2f}%")


def main():
    df = load_kospi()
    df["rsi"] = rsi(df["c"], 14)
    df["fwd5"] = df["c"].shift(-5) / df["c"] - 1.0
    df["fwd20"] = df["c"].shift(-20) / df["c"] - 1.0
    df["year"] = df["date"].dt.year

    sig = df[(df["rsi"] < 25) & df["fwd5"].notna()].copy()
    print(f"코스피 RSI<25 신호: 총 {len(sig)}회 "
          f"({df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()})")

    # ── 1. 연도별 분포 ──
    print("\n" + "="*60)
    print("■ 1. 연도별 신호 분포")
    yc = sig["year"].value_counts().sort_index()
    total = len(sig)
    for yr, cnt in yc.items():
        bar = "█" * cnt
        frac = cnt / total * 100
        print(f"  {yr}: {cnt:3d}회 ({frac:4.1f}%) {bar}")
    # 상위 집중도
    top = yc.sort_values(ascending=False)
    print(f"\n  상위 3개 연도: {list(top.head(3).index)} "
          f"= {top.head(3).sum()}회 ({top.head(3).sum()/total*100:.0f}%)")
    print(f"  신호가 발생한 연도 수: {len(yc)}개 / 36년")

    # ── 2. 전체 승률 (기준) ──
    print("\n" + "="*60)
    print("■ 2. 전체 RSI<25 성과 (기준선)")
    summarize(sig["fwd5"], "5일후 ")
    summarize(sig["fwd20"], "20일후")

    # ── 3. 대폭락기 제외 ──
    print("\n" + "="*60)
    print("■ 3. 대폭락기 제외 시 — 승률 유지되나?")
    crisis_years = {
        "2008 금융위기": [2008],
        "2020 코로나": [2020],
        "2026 현재폭락": [2026],
        "셋 다 제외": [2008, 2020, 2026],
    }
    for name, years in crisis_years.items():
        sub = sig[~sig["year"].isin(years)]
        excluded = len(sig) - len(sub)
        print(f"\n  [{name} 제외] (신호 {excluded}개 빠짐 → {len(sub)}개 남음)")
        summarize(sub["fwd5"], "5일후 ")
        summarize(sub["fwd20"], "20일후")

    # ── 4. 개별 신호 나열 (날짜·수익률) ──
    print("\n" + "="*60)
    print("■ 4. 개별 신호 (날짜·RSI·5일후 수익률) — 편중 눈으로 확인")
    print("  날짜         RSI   5일후    20일후")
    for _, r in sig.iterrows():
        f5 = f"{r['fwd5']*100:+.1f}%" if pd.notna(r['fwd5']) else "  -"
        f20 = f"{r['fwd20']*100:+.1f}%" if pd.notna(r['fwd20']) else "  -"
        print(f"  {r['date'].date()}  {r['rsi']:4.1f}  {f5:>7}  {f20:>7}")

    # ── 판정 ──
    print("\n" + "="*60)
    print("■ 판정 가이드")
    print("  · 상위 3연도가 50%+ 차지 → 폭락 편중 (평상시 신호 아님)")
    print("  · 폭락기 제외해도 승률 유지 → 진짜 엣지")
    print("  · 제외하니 승률 급락·표본 급감 → '폭락 전용 반등 신호'로 한정")


if __name__ == "__main__":
    main()