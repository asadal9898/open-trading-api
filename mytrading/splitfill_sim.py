"""
분할 매수 시뮬레이션 (백테스트) — 일시매수(lump) vs 분할매수(DCA) 비교.

⚠️ 이것은 과거 데이터 기반 시뮬레이션이다 (실제 주문 아님).
목적: "목표 금액을 한 번에 살까, 나눠 살까?"를 과거 가격으로 검증해
      1-b(실제 분할 주문)에서 쓸 분할 방식을 결정한다.

기존 백테스터(kis_backtest)는 '신호' 중심이라 분할 '집행'을 못 다룬다.
→ 이 모듈이 일봉 CSV 를 직접 읽어 집행 방식별 결과를 계산한다.

데이터: backtester/.lean-workspace/data/equity/krx/daily/{symbol}.csv
        형식: YYYYMMDD,open,high,low,close,volume  (종가=5번째)

사용:
    from mytrading.splitfill_sim import load_prices, simulate, compare
    prices = load_prices("005930", "20200101", "20201231")
    res = simulate(prices, budget=1_000_000, method="dca", n=3, interval=2)
    print(res.avg_price, res.final_pnl_pct)

CLI:
    uv run python mytrading/splitfill_sim.py 005930 20200101 20201231
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DAILY_DIR = _REPO_ROOT / "backtester" / ".lean-workspace" / "data" / "equity" / "krx" / "daily"

# 거래비용 (분할은 거래횟수↑ → 비용↑. 매수 시 수수료만 가정, 모의 기준 근사)
_FEE_RATE = 0.00015  # 0.015% (증권사 수수료 근사. 실제는 계좌별 상이)


@dataclass
class PricePoint:
    date: str       # YYYYMMDD
    close: float


def load_prices(symbol: str, start: str = None, end: str = None) -> List[PricePoint]:
    """
    일봉 CSV 에서 (날짜, 종가) 시계열을 읽는다. start~end (YYYYMMDD) 범위 필터.
    """
    path = _DAILY_DIR / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{symbol}.csv 없음 — 먼저 prefetch 하세요: "
            f"uv run python mytrading/runners/prefetch_data.py {symbol}")
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 5:
                continue
            d = row[0].strip()
            if start and d < start:
                continue
            if end and d > end:
                continue
            try:
                close = float(row[4])
            except ValueError:
                continue
            out.append(PricePoint(date=d, close=close))
    out.sort(key=lambda p: p.date)
    return out


@dataclass
class Fill:
    """한 회차 체결."""
    date: str
    price: float
    quantity: int
    cost: float        # 체결금액 (수수료 포함)


@dataclass
class SimResult:
    """시뮬레이션 결과 한 건."""
    method: str               # "lump" / "dca"
    label: str                # 사람이 읽는 방식 이름
    fills: List[Fill] = field(default_factory=list)
    budget: float = 0.0
    total_quantity: int = 0
    total_cost: float = 0.0   # 실제 매수에 쓴 금액 (수수료 포함)
    avg_price: float = 0.0    # 평균 진입가 (총비용/수량, 수수료 제외 단가)
    final_price: float = 0.0  # 기간말 종가
    final_value: float = 0.0  # 기간말 평가액 (수량 × 기간말종가)
    final_pnl: float = 0.0    # 평가손익 (평가액 − 총비용)
    final_pnl_pct: float = 0.0


def _buy_at(price: float, budget_part: float) -> Tuple[int, float]:
    """주어진 예산으로 몇 주 살 수 있나 (정수주). 반환 (수량, 실제비용)."""
    if price <= 0:
        return 0, 0.0
    # 수수료 고려해 살 수 있는 최대 정수주
    qty = int(budget_part // (price * (1 + _FEE_RATE)))
    if qty <= 0:
        return 0, 0.0
    cost = qty * price * (1 + _FEE_RATE)
    return qty, cost


def simulate(prices: List[PricePoint], budget: float,
             method: str = "lump", n: int = 1, interval: int = 1) -> SimResult:
    """
    집행 방식대로 매수 시뮬레이션.
    method="lump": 첫날 전액 매수
    method="dca" : n회로 나눠 interval(거래일) 간격 매수
    """
    if not prices:
        raise ValueError("가격 데이터가 비었습니다.")

    if method == "lump":
        n, interval = 1, 0
        label = "일시매수"
    else:
        label = f"분할 {n}회×{interval}일"

    res = SimResult(method=method, label=label, budget=budget)

    per_round = budget / n  # 회차당 예산
    idx = 0
    for r in range(n):
        if idx >= len(prices):
            break  # 데이터 끝
        pt = prices[idx]
        qty, cost = _buy_at(pt.close, per_round)
        if qty > 0:
            res.fills.append(Fill(date=pt.date, price=pt.close,
                                  quantity=qty, cost=cost))
            res.total_quantity += qty
            res.total_cost += cost
        idx += interval if method == "dca" else 1

    # 평균 진입가 (수수료 제외 순단가)
    if res.total_quantity > 0:
        gross = sum(f.price * f.quantity for f in res.fills)
        res.avg_price = gross / res.total_quantity

    # 기간말 평가
    res.final_price = prices[-1].close
    res.final_value = res.total_quantity * res.final_price
    res.final_pnl = res.final_value - res.total_cost
    res.final_pnl_pct = (res.final_pnl / res.total_cost * 100
                         if res.total_cost > 0 else 0.0)
    return res


def compare(symbol: str, start: str, end: str, budget: float = 1_000_000,
            methods: Optional[List[dict]] = None) -> List[SimResult]:
    """
    여러 집행 방식을 같은 종목/기간/예산으로 비교.
    methods: [{"method":"lump"}, {"method":"dca","n":3,"interval":2}, ...]
             None 이면 기본 세트.
    """
    prices = load_prices(symbol, start, end)
    if methods is None:
        methods = [
            {"method": "lump"},
            {"method": "dca", "n": 3, "interval": 1},
            {"method": "dca", "n": 3, "interval": 3},
            {"method": "dca", "n": 5, "interval": 1},
            {"method": "dca", "n": 5, "interval": 5},
            {"method": "dca", "n": 10, "interval": 1},
        ]
    results = []
    for m in methods:
        results.append(simulate(prices, budget,
                                method=m.get("method", "lump"),
                                n=m.get("n", 1),
                                interval=m.get("interval", 1)))
    return results


def print_comparison(symbol: str, start: str, end: str,
                     results: List[SimResult]) -> None:
    print(f"=== 분할 시뮬레이션: {symbol} ({start}~{end}) ===")
    print(f"  예산: {results[0].budget:,.0f}원\n" if results else "")
    print(f"  {'방식':<16} {'평균단가':>10} {'수량':>6} {'평가손익':>12} {'수익률':>8}")
    print("  " + "-" * 56)
    # 수익률 순 정렬해서 보기 (단, 수량 0=못 산 경우는 맨 아래로)
    def _sort_key(x):
        # 못 산 경우(수량 0)는 비교에서 맨 뒤로
        return (x.total_quantity > 0, x.final_pnl_pct)
    for r in sorted(results, key=_sort_key, reverse=True):
        if r.total_quantity == 0:
            print(f"  {r.label:<16} {'(예산부족 - 1주도 못 삼)':>40}")
        else:
            print(f"  {r.label:<16} {r.avg_price:>10,.0f} {r.total_quantity:>6} "
                  f"{r.final_pnl:>12,.0f} {r.final_pnl_pct:>7.2f}%")
    print(f"\n  기간말 종가: {results[0].final_price:,.0f}원" if results else "")
    print("  ※ 과거 데이터 시뮬레이션 — 미래를 보장하지 않음 (참고용)")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    symbol = args[0] if len(args) > 0 else "005930"
    start = args[1] if len(args) > 1 else "20200101"
    end = args[2] if len(args) > 2 else "20241231"
    budget = float(args[3]) if len(args) > 3 else 1_000_000

    results = compare(symbol, start, end, budget=budget)
    print()
    print_comparison(symbol, start, end, results)