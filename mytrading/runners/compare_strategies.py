"""
전략 비교 러너 — 여러 전략을 백테스트하고 표로 비교 + CSV 저장
데이터는 캐시 공유(prefetch 해두면 API 0회).

실행:
    uv run python mytrading/runners/compare_strategies.py            # 10종 전부
    uv run python mytrading/runners/compare_strategies.py sma_crossover momentum  # 일부만

결과:
    - 터미널에 수익률 순 정렬 표
    - mytrading/results/comparison_YYYYMMDD_HHMMSS.csv 저장
"""
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kis_backtest import LeanClient, STRATEGY_REGISTRY
import kis_backtest.strategies.preset  # 프리셋 10종 자동 등록
from mytrading.common import CONFIG, get_data_provider, get_backtest_period, resolve_mode
from mytrading.data_manager import ensure_data_multi

BACKTESTER_DIR = ROOT / "backtester"
RESULTS_DIR = ROOT / "mytrading" / "results"

# CSV/표에 출력할 지표 (속성명, 헤더, 포맷함수)
# 모든 비율 지표(수익률/CAGR/MDD/승률)는 0~ 형태의 비율이므로 ×100 해서 퍼센트로 표시.
#   예) total_return_pct=3.877 → 387.7%,  cagr=0.5797 → 58.0%
def _pct(v):     # 비율 → 퍼센트
    return f"{v * 100:.1f}%"

def _num(v):
    return f"{v:.2f}"

def _int(v):
    return f"{int(v)}"

def _str(v):
    return f"{v}"

METRICS = [
    ("strategy_id",      "전략",      _str),
    ("total_return_pct", "수익률",    _pct),
    ("cagr",             "CAGR",      _pct),
    ("sharpe_ratio",     "샤프",      _num),
    ("sortino_ratio",    "소르티노",  _num),
    ("max_drawdown",     "MDD",       _pct),
    ("win_rate",         "승률",      _pct),
    ("profit_factor",    "손익비",    _num),
    ("total_trades",     "거래수",    _int),
]


def _get(result, attr):
    """결과에서 속성 안전하게 추출 (없으면 None)."""
    return getattr(result, attr, None)


def run_one(client, strategy_id, symbols, start, end):
    """전략 하나 백테스트 → dict 반환 (실패 시 success=False)."""
    try:
        r = client.backtest_strategy(
            strategy_id=strategy_id, symbols=symbols,
            start_date=start, end_date=end,
        )
        row = {attr: _get(r, attr) for attr, _, _ in METRICS}
        row["success"] = True
        return row
    except Exception as e:
        print(f"    [실패] {strategy_id}: {e}")
        return {"strategy_id": strategy_id, "success": False}


def main():
    # 1. 비교할 전략 목록
    all_ids = list(STRATEGY_REGISTRY.list().keys())
    if len(sys.argv) > 1:
        target = [s for s in sys.argv[1:] if s in all_ids]
        invalid = [s for s in sys.argv[1:] if s not in all_ids]
        if invalid:
            print(f"[무시] 알 수 없는 전략: {invalid}")
    else:
        target = all_ids

    symbols = CONFIG.get("trading", {}).get("symbols", ["005930"])
    start, end = get_backtest_period()

    print("=" * 60)
    print(f"  전략 비교  ({len(target)}종)")
    print(f"  종목 : {symbols}")
    print(f"  기간 : {start} ~ {end}")
    print(f"  모드 : {resolve_mode()}")
    print("=" * 60)

    # 2. 데이터 보장 (캐시 없으면 받고, 있으면 증분)
    data_start = CONFIG.get("data", {}).get("prefetch_start", "2020-01-01")
    ensure_data_multi(symbols, start=data_start, end=end)

    # 3. 전략 순회 (cwd 를 backtester 로 — Lean 데이터 경로)
    original_cwd = os.getcwd()
    os.chdir(BACKTESTER_DIR)
    rows = []
    try:
        client = LeanClient(data_provider=get_data_provider())
        for i, sid in enumerate(target, 1):
            print(f"\n[{i}/{len(target)}] {sid} 백테스트 중...")
            rows.append(run_one(client, sid, symbols, start, end))
    finally:
        os.chdir(original_cwd)

    # 4. 성공한 것만 수익률 순 정렬
    ok_rows = [r for r in rows if r.get("success")]
    ok_rows.sort(key=lambda r: (r.get("total_return_pct") or -1e9), reverse=True)

    # 5. 표 출력
    _print_table(ok_rows)

    # 6. CSV 저장
    _save_csv(ok_rows, symbols, start, end)


def _print_table(rows):
    print("\n" + "=" * 60)
    print("  비교 결과 (수익률 순)")
    print("=" * 60)
    if not rows:
        print("  성공한 백테스트가 없습니다.")
        return
    # 헤더
    headers = [h for _, h, _ in METRICS]
    print("  " + "  ".join(f"{h:>10}" for h in headers))
    # 행
    for r in rows:
        cells = []
        for attr, _, fmt in METRICS:
            v = r.get(attr)
            cells.append(fmt(v) if v is not None else "-")
        print("  " + "  ".join(f"{c:>10}" for c in cells))


def _save_csv(rows, symbols, start, end):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"comparison_{ts}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        # 메타 정보
        w.writerow([f"# 종목={symbols} 기간={start}~{end}"])
        # 헤더
        w.writerow([attr for attr, _, _ in METRICS])
        # 데이터
        for r in rows:
            w.writerow([r.get(attr, "") for attr, _, _ in METRICS])
    print(f"\n  CSV 저장: {path}")


if __name__ == "__main__":
    main()