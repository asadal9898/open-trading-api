# -*- coding: utf-8 -*-
"""
드로다운 컨트롤 백테스트 — "고점 대비 X% 빠지면 이탈, Y%까지 회복하면 재진입"이
buy&hold 대비 MDD 를 얼마나 줄이고 CAGR 을 얼마나 희생하는가.

대상: 코스피 / S&P500 / 나스닥100
규칙:
  - 드로다운 = 종가 / 역대최고가 - 1
  - 보유 중 dd < -X% → 다음날 이탈(현금)
  - 현금 중 dd > -Y% → 다음날 재진입 (Y < X 로 갭을 둬 왔다갔다 방지)
  - 신호 다음날 반영 (룩어헤드 없음), 거래비용 왕복 0.2% 가정

실행: uv run --with pandas python mytrading/research_backtest/dd_control_backtest.py
"""
import sys
from pathlib import Path
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
DATA = _ROOT / "backtester" / ".lean-workspace" / "data" / "index"

COST = 0.002  # 왕복 0.2% (이탈+재진입 세트당)

INDICES = {
    "코스피": "kospi.csv",
    "S&P500": "sp500.csv",
    "나스닥100": "nasdaq100.csv",
}

# (이탈 임계, 재진입 임계) — 재진입은 이탈보다 얕게 (갭)
RULES = [
    (5, 3), (10, 5), (15, 8), (20, 10),
]


def load(fname):
    df = pd.read_csv(DATA / fname, header=None,
                     names=["date", "o", "h", "l", "c", "v"])
    df["date"] = df["date"].astype(str)
    df = df[df["c"] > 0].reset_index(drop=True)
    return df


def cagr_mdd(equity: pd.Series, days_per_year=252):
    """수익곡선 → (CAGR%, MDD%)."""
    n = len(equity)
    if n < 2 or equity.iloc[0] <= 0:
        return 0.0, 0.0
    total = equity.iloc[-1] / equity.iloc[0]
    years = n / days_per_year
    cagr = (total ** (1 / years) - 1) * 100
    peak = equity.cummax()
    mdd = ((equity / peak) - 1).min() * 100
    return cagr, mdd


def run(df, exit_th, reenter_th):
    """드로다운 컨트롤 시뮬레이션. 반환: equity 시리즈, 거래횟수."""
    c = df["c"].reset_index(drop=True)
    peak = c.cummax()
    dd = (c / peak - 1) * 100  # 음수

    equity = [1.0]
    invested = True
    trades = 0
    for i in range(1, len(c)):
        ret = c[i] / c[i - 1] - 1
        eq = equity[-1] * (1 + ret) if invested else equity[-1]
        # 신호는 오늘 dd 기준, 반영은 내일부터 (i 시점 상태 갱신)
        if invested and dd[i] < -exit_th:
            invested = False
            trades += 1
            eq *= (1 - COST / 2)   # 이탈 비용
        elif (not invested) and dd[i] > -reenter_th:
            invested = True
            trades += 1
            eq *= (1 - COST / 2)   # 재진입 비용
        equity.append(eq)
    return pd.Series(equity), trades


def main():
    for name, fname in INDICES.items():
        df = load(fname)
        start, end = df["date"].iloc[0], df["date"].iloc[-1]
        years = len(df) / 252

        # 기준: buy & hold
        bh_equity = df["c"] / df["c"].iloc[0]
        bh_cagr, bh_mdd = cagr_mdd(bh_equity.reset_index(drop=True))

        print(f"\n{'='*66}")
        print(f"■ {name} ({start}~{end}, {years:.0f}년)")
        print(f"{'='*66}")
        print(f"  {'규칙':<16}{'CAGR':>8}{'MDD':>9}{'거래':>6}   비고")
        print(f"  {'-'*58}")
        print(f"  {'buy&hold':<16}{bh_cagr:>+7.1f}%{bh_mdd:>+8.1f}%{'-':>6}   기준")
        for exit_th, reenter_th in RULES:
            eq, trades = run(df, exit_th, reenter_th)
            cagr, mdd = cagr_mdd(eq)
            d_cagr = cagr - bh_cagr
            d_mdd = mdd - bh_mdd
            note = f"CAGR {d_cagr:+.1f}%p, MDD {d_mdd:+.1f}%p"
            print(f"  {'-%d%%/-%d%% 재진입' % (exit_th, reenter_th):<15}"
                  f"{cagr:>+7.1f}%{mdd:>+8.1f}%{trades:>6}   {note}")
    print(f"\n※ 거래비용 왕복 {COST*100:.1f}% 반영, 신호 다음날 반영(룩어헤드 없음)")
    print("※ MDD 가 크게 줄면서 CAGR 희생이 작아야 채택 가치 있음")


if __name__ == "__main__":
    main()
