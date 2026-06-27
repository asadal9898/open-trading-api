"""
분할 매수 종합 비교 러너 — 여러 종목 × 여러 국면(기간) × 여러 분할방식을 한 번에 비교.

목적: "어떤 분할 방식이 어떤 국면에서 평균적으로 나은가"를 표로 종합해
      1-b(실제 분할 주문)에서 쓸 방식을 결정한다.

⚠️ 과거 데이터 시뮬레이션 (실제 주문 아님).

국면 구간(코스피 기준 일반 구분 — 결과 보며 조정 가능):
  - 상승장 : 2020-04 ~ 2021-06 (코로나 회복 랠리)
  - 하락장 : 2021-07 ~ 2022-12 (금리 인상기 하락)
  - 횡보   : 2023-01 ~ 2024-12 (반등·등락)

실행:
    uv run python mytrading/runners/compare_splitfill.py
    uv run python mytrading/runners/compare_splitfill.py 005930 000660  # 종목 지정
결과:
    - 국면별 표 (각 종목에서 방식별 수익률)
    - 방식별 종합 순위 (국면별 평균 순위)
    - mytrading/results/splitfill_YYYYMMDD_HHMMSS.csv
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mytrading.splitfill_sim import load_prices, simulate
from mytrading.portfolio import get_watch_symbols, get_symbol_names

RESULTS_DIR = ROOT / "mytrading" / "results"

# 국면 구간 (라벨, 시작, 끝) — 결과 보며 조정
PHASES = [
    ("상승장(20.04~21.06)", "20200401", "20210630"),
    ("하락장(21.07~22.12)", "20210701", "20221231"),
    ("횡보장(23.01~24.12)", "20230101", "20241231"),
]

# 비교할 분할 방식
METHODS = [
    {"key": "lump",        "method": "lump"},
    {"key": "dca3x1",      "method": "dca", "n": 3,  "interval": 1},
    {"key": "dca3x3",      "method": "dca", "n": 3,  "interval": 3},
    {"key": "dca5x1",      "method": "dca", "n": 5,  "interval": 1},
    {"key": "dca5x5",      "method": "dca", "n": 5,  "interval": 5},
    {"key": "dca10x1",     "method": "dca", "n": 10, "interval": 1},
]
_METHOD_LABEL = {m["key"]: None for m in METHODS}  # simulate가 label 채움

BUDGET = 1_000_000


def run():
    args = sys.argv[1:]
    if args:
        symbols = args
    else:
        symbols = get_watch_symbols()
    names = get_symbol_names()

    print(f"종합 비교: {len(symbols)}종목 × {len(PHASES)}국면 × {len(METHODS)}방식")
    print(f"예산 {BUDGET:,.0f}원 / 종목: {', '.join(symbols)}\n")

    # 결과 저장: rows[(phase, symbol, method_key)] = SimResult
    all_rows = []
    # 방식별 순위 누적 (국면 평균 순위 계산용)
    rank_sum = defaultdict(float)
    rank_cnt = defaultdict(int)

    for phase_label, start, end in PHASES:
        print(f"\n{'='*64}")
        print(f"  [{phase_label}]")
        print(f"{'='*64}")

        for sym in symbols:
            nm = names.get(sym, sym)
            try:
                prices = load_prices(sym, start, end)
            except FileNotFoundError:
                print(f"  {nm}({sym}): 데이터 없음 (prefetch 필요) — 건너뜀")
                continue
            if not prices:
                print(f"  {nm}({sym}): 해당 기간 데이터 없음 — 건너뜀")
                continue

            # 방식별 시뮬
            sym_results = []
            for m in METHODS:
                r = simulate(prices, BUDGET, method=m["method"],
                             n=m.get("n", 1), interval=m.get("interval", 1))
                sym_results.append((m["key"], r))
                all_rows.append({
                    "phase": phase_label, "symbol": sym, "name": nm,
                    "method": m["key"], "label": r.label,
                    "avg_price": r.avg_price, "qty": r.total_quantity,
                    "pnl": r.final_pnl, "pnl_pct": r.final_pnl_pct,
                    "bought": r.total_quantity > 0,
                })

            # 종목 내 순위 (산 것만, 수익률 높은 순)
            bought = [(k, r) for k, r in sym_results if r.total_quantity > 0]
            bought.sort(key=lambda x: x[1].final_pnl_pct, reverse=True)

            print(f"\n  {nm}({sym}):")
            for rank, (k, r) in enumerate(bought, 1):
                rank_sum[k] += rank
                rank_cnt[k] += 1
                print(f"    {rank}. {r.label:<14} {r.final_pnl_pct:>7.2f}% "
                      f"(평단 {r.avg_price:,.0f})")
            # 못 산 방식
            for k, r in sym_results:
                if r.total_quantity == 0:
                    print(f"    -  {r.label:<14} (예산부족)")

    # ---- 방식별 종합 순위 (낮을수록 평균적으로 상위) ----
    print(f"\n{'='*64}")
    print("  [종합] 방식별 평균 순위 (낮을수록 평균적으로 우수)")
    print(f"{'='*64}")
    avg_ranks = []
    for m in METHODS:
        k = m["key"]
        if rank_cnt[k] > 0:
            avg = rank_sum[k] / rank_cnt[k]
            avg_ranks.append((k, avg, rank_cnt[k]))
    avg_ranks.sort(key=lambda x: x[1])
    for k, avg, cnt in avg_ranks:
        # label 찾기
        label = next((r["label"] for r in all_rows if r["method"] == k), k)
        print(f"    {label:<14} 평균순위 {avg:.2f}  ({cnt}개 케이스)")

    # ---- CSV 저장 ----
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"splitfill_{ts}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "phase", "symbol", "name", "method", "label",
            "avg_price", "qty", "pnl", "pnl_pct", "bought"])
        w.writeheader()
        for row in all_rows:
            w.writerow(row)
    print(f"\n  CSV 저장: {csv_path}")
    print("\n  ※ 과거 데이터 시뮬레이션 — 미래 보장 안 함. 1-b 분할방식 결정의 참고자료.")


if __name__ == "__main__":
    run()