"""
모멘텀 매도 백테스트 — 20일선 이탈 매도 vs Buy & Hold.

목적: momentum 종목(삼성전자·SK하이닉스 등)에 "종가가 20일선 아래로 내려가면 매도,
     위로 올라가면 매수" 규칙이 그냥 들고 있는 것(Buy&Hold)보다 나은지 검증.

배경(차영석 가설): 한국 시장은 작고 수급 급변이 잦아 차트 매매가 어려운 편.
     20일선 이탈 매도가 Buy&Hold 를 못 이길 가능성 — 이를 데이터로 확인.

데이터: backtester/.lean-workspace/data/equity/krx/daily/{code}.csv
     형식: 날짜(YYYYMMDD),시가,고가,저가,종가,거래량 (헤더 없음)

사용:
  uv run python mytrading/backtest_ma_exit.py                    # 기본(삼성·SK)
  uv run python mytrading/backtest_ma_exit.py 005930 000660 ...  # 종목 지정
  uv run python mytrading/backtest_ma_exit.py --ma 5             # 5일선
"""
import sys
from pathlib import Path
from datetime import datetime

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DAILY_DIR = _REPO_ROOT / "backtester" / ".lean-workspace" / "data" / "equity" / "krx" / "daily"

# 매매 비용 (한국 주식, 매도 시)
_TAX = 0.0018          # 증권거래세 0.18% (매도)
_FEE = 0.00015         # 위탁수수료 0.015% (매수·매도 각)

_DEFAULT_SYMBOLS = ["005930", "000660"]   # 삼성전자, SK하이닉스
_NAMES = {"005930": "삼성전자", "000660": "SK하이닉스",
          "035250": "강원랜드", "009680": "모토닉"}


def _read_daily(code: str, start: str = None, end: str = None):
    """일봉 CSV → [(date_str, close), ...] 오름차순. 거래량 0(미완성)은 제외."""
    path = _DAILY_DIR / f"{code.lower()}.csv"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(",")
        if len(parts) < 6:
            continue
        ds = parts[0].strip()
        if len(ds) != 8 or not ds.isdigit():
            continue
        if start and ds < start:
            continue
        if end and ds > end:
            continue
        try:
            close = float(parts[4])
            vol = float(parts[5])
        except (ValueError, IndexError):
            continue
        if vol <= 0:   # 거래량 0 = 미완성/휴장 → 제외
            continue
        rows.append((ds, close))
    rows.sort(key=lambda x: x[0])
    return rows


def _moving_avg(closes, period):
    """단순이동평균. i 번째는 [i-period+1 .. i] 평균. 부족하면 None."""
    out = []
    for i in range(len(closes)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(closes[i - period + 1:i + 1]) / period)
    return out


def _max_drawdown(equity):
    """자산곡선 최대 낙폭(MDD, %). equity: 일별 자산가치 리스트."""
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > mdd:
            mdd = dd
    return mdd


def backtest_ma_exit(code: str, ma_period: int = 20, start: str = None, end: str = None):
    """20일선(기본) 이탈 매도 전략 vs Buy&Hold.
    반환: dict(전략 수익률·매매횟수·MDD, B&H 수익률·MDD).
    """
    rows = _read_daily(code, start, end)
    if len(rows) < ma_period + 10:
        return None
    dates = [d for d, _ in rows]
    closes = [c for _, c in rows]
    ma = _moving_avg(closes, ma_period)

    # ── 전략: 종가>ma 매수, 종가<ma 매도 ──
    cash = 1.0          # 초기 자본 1.0 (비율)
    shares = 0.0        # 보유 수량(비율)
    holding = False
    trades = 0
    equity = []         # 일별 자산가치

    for i in range(len(closes)):
        price = closes[i]
        m = ma[i]
        # 자산가치 기록 (현금 + 보유평가)
        equity.append(cash + shares * price)
        if m is None:
            continue
        if not holding and price > m:
            # 매수 (현금 전부)
            shares = cash * (1 - _FEE) / price
            cash = 0.0
            holding = True
            trades += 1
        elif holding and price < m:
            # 매도 (보유 전부)
            cash = shares * price * (1 - _FEE - _TAX)
            shares = 0.0
            holding = False
            trades += 1

    # 마지막 날 보유 중이면 청산가치로 (매도비용 적용)
    final_strat = cash + (shares * closes[-1] * (1 - _FEE - _TAX) if holding else 0.0)
    strat_ret = (final_strat - 1.0) * 100
    strat_mdd = _max_drawdown(equity)

    # ── Buy & Hold: 첫날 매수, 끝까지 ──
    bh_shares = 1.0 * (1 - _FEE) / closes[0]
    bh_equity = [bh_shares * c for c in closes]
    bh_final = bh_shares * closes[-1] * (1 - _FEE - _TAX)
    bh_ret = (bh_final - 1.0) * 100
    bh_mdd = _max_drawdown(bh_equity)

    return {
        "code": code, "name": _NAMES.get(code, code),
        "period": f"{dates[0]}~{dates[-1]}", "days": len(closes),
        "ma": ma_period,
        "strat_ret": strat_ret, "trades": trades, "strat_mdd": strat_mdd,
        "bh_ret": bh_ret, "bh_mdd": bh_mdd,
        "diff": strat_ret - bh_ret,
    }


def main():
    args = sys.argv[1:]
    ma_period = 20
    start = end = None
    if "--ma" in args:
        idx = args.index("--ma")
        if idx + 1 < len(args):
            ma_period = int(args[idx + 1])
            args = args[:idx] + args[idx + 2:]
    if "--start" in args:
        idx = args.index("--start")
        if idx + 1 < len(args):
            start = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
    if "--end" in args:
        idx = args.index("--end")
        if idx + 1 < len(args):
            end = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
    symbols = [a for a in args if a.isdigit()] or _DEFAULT_SYMBOLS

    print(f"=== {ma_period}일선 이탈 매도 vs Buy&Hold ===")
    print(f"비용: 거래세 {_TAX*100:.2f}% + 수수료 {_FEE*100:.3f}%(편도)\n")
    print(f"{'종목':<12} {'전략수익':>9} {'매매':>5} {'전략MDD':>8} "
          f"{'B&H수익':>9} {'B&H_MDD':>8} {'차이':>9}")
    print("-" * 70)
    for code in symbols:
        r = backtest_ma_exit(code, ma_period, start, end)
        if r is None:
            print(f"{_NAMES.get(code, code):<12} 데이터 부족")
            continue
        win = "✅" if r["diff"] > 0 else "❌"
        print(f"{r['name']:<12} {r['strat_ret']:>8.1f}% {r['trades']:>5} "
              f"{r['strat_mdd']:>7.1f}% {r['bh_ret']:>8.1f}% {r['bh_mdd']:>7.1f}% "
              f"{r['diff']:>+8.1f}% {win}")
        print(f"{'':12} 기간: {r['period']} ({r['days']}일)")
    print("\n해석: 차이>0(✅) = 20일선 매도가 Buy&Hold 보다 나음")
    print("      차이<0(❌) = 그냥 들고 있는 게 나음 (한국 차트매매 어려움 방증)")


if __name__ == "__main__":
    main()