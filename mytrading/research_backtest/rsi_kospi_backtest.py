# -*- coding: utf-8 -*-
"""
코스피 RSI 확률 검증 — 보조지표가 확률적 엣지를 주는지.

핵심 질문: "RSI 과매도(30↓)에 사면, 그냥 아무 때나 사는 것보다 나은가?"

방법 (오늘의 원칙):
  1. RSI(14) 계산 — 신호 시점의 값만 사용 (룩어헤드 없음)
  2. 진입 조건별 N일 후 수익률 (5·20·60일)
  3. ★기저(전체 무작위 진입)와 비교 — 이게 핵심
  4. 승률·평균수익·표본수. 엣지 = 조건부 - 기저

실행: uv run python mytrading/research_backtest/rsi_kospi_backtest.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
KOSPI = _ROOT / "backtester" / ".lean-workspace" / "data" / "index" / "kospi.csv"


def load_kospi() -> pd.DataFrame:
    df = pd.read_csv(KOSPI, header=None,
                     names=["date", "o", "h", "l", "c", "v"])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values("date").reset_index(drop=True)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder 평활 (EMA alpha=1/period)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def fwd_return(close: pd.Series, days: int) -> pd.Series:
    """days일 후 수익률 (룩어헤드 방지: 미래값, 진입판단엔 안 씀)."""
    return close.shift(-days) / close - 1.0


def analyze(df: pd.DataFrame, mask: pd.Series, label: str,
            horizons=(5, 20, 60)) -> dict:
    """조건(mask) 진입의 N일 후 수익률 통계."""
    out = {"label": label, "n": int(mask.sum())}
    for h in horizons:
        fr = df[f"fwd{h}"][mask].dropna()
        if len(fr) == 0:
            out[f"{h}d"] = None
            continue
        out[f"{h}d"] = {
            "mean": fr.mean() * 100,
            "win": (fr > 0).mean() * 100,
            "n": len(fr),
        }
    return out


def print_row(r: dict, base: dict = None, horizons=(5, 20, 60)):
    print(f"\n[{r['label']}]  신호 {r['n']}회")
    for h in horizons:
        v = r.get(f"{h}d")
        if v is None:
            print(f"  {h:>2}일후: 표본없음")
            continue
        line = f"  {h:>2}일후: 평균 {v['mean']:+.2f}%  승률 {v['win']:.1f}%  (n={v['n']})"
        if base:
            bv = base.get(f"{h}d")
            if bv:
                edge_m = v['mean'] - bv['mean']
                edge_w = v['win'] - bv['win']
                line += f"   |엣지 수익{edge_m:+.2f}%p 승률{edge_w:+.1f}%p"
        print(line)


def main():
    df = load_kospi()
    print(f"코스피 {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()} "
          f"({len(df)}일)")

    # 지표 계산
    df["rsi"] = rsi(df["c"], 14)
    HZ = (5, 20, 60)
    for h in HZ:
        df[f"fwd{h}"] = fwd_return(df["c"], h)

    valid = df["rsi"].notna()

    # ── 기저: 전체(무작위 진입) ──
    base = analyze(df, valid, "기저(전체)", HZ)
    print("\n" + "=" * 60)
    print("■ 기저 — 아무 날이나 진입했을 때")
    print_row(base, None, HZ)

    # ── 과매도 매수 (역추세) ──
    print("\n" + "=" * 60)
    print("■ 과매도 매수 — RSI 낮을 때 사면 반등하나?")
    for thr in (30, 25, 20):
        m = valid & (df["rsi"] < thr)
        r = analyze(df, m, f"RSI < {thr}", HZ)
        print_row(r, base, HZ)

    # ── 과매수 (매도/하락) ──
    print("\n" + "=" * 60)
    print("■ 과매수 — RSI 높을 때 이후 하락하나? (매도신호 검증)")
    for thr in (70, 75, 80):
        m = valid & (df["rsi"] > thr)
        r = analyze(df, m, f"RSI > {thr}", HZ)
        print_row(r, base, HZ)

    # ── 판정 가이드 ──
    print("\n" + "=" * 60)
    print("■ 해석")
    print("  · 과매도 매수가 유효하려면: 기저보다 승률·수익 엣지(+)가 뚜렷해야")
    print("  · 과매수 매도가 유효하려면: 이후 수익률이 기저보다 낮아야(하락)")
    print("  · 표본(n)이 적으면(수십 이하) 우연 가능 — 신뢰 낮음")
    print("  · 엣지가 0 근처면: RSI는 코스피에서 확률적 엣지 없음 (기각)")


if __name__ == "__main__":
    main()