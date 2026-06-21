"""
첫 백테스트 러너 — kis_backtest 프리셋 전략 실행
원본 kis_backtest 라이브러리를 import해서 사용 (1번 방식)

전제: Docker 실행 중 (QuantConnect Lean 엔진). 첫 실행 시 Lean 이미지 자동 다운로드(수 분 소요).

실행:
    uv run python mytrading/runners/run_backtest.py
    uv run python mytrading/runners/run_backtest.py momentum   # 전략 지정

사용 가능한 프리셋 전략:
    sma_crossover, momentum, week52_high, consecutive_moves, ma_divergence,
    false_breakout, strong_close, volatility_breakout, short_term_reversal,
    trend_filter_signal
"""
import os
import sys
from pathlib import Path

# 프로젝트 루트를 path 에 추가
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kis_backtest import LeanClient, STRATEGY_REGISTRY
import kis_backtest.strategies.preset  # 프리셋 10종 자동 등록
from mytrading.common import CONFIG, get_data_provider, resolve_mode, get_backtest_period

# Lean 데이터(.lean-workspace)는 backtester/ 안에 있고,
# client.py가 cwd 기준 상대경로(.lean-workspace)로 데이터를 찾으므로
# 백테스트 실행 전 작업 디렉터리를 backtester/ 로 맞춰야 함.
BACKTESTER_DIR = ROOT / "backtester"


def main():
    # 1. 전략 선택 (인자로 받거나 기본 sma_crossover)
    strategy_id = sys.argv[1] if len(sys.argv) > 1 else "sma_crossover"

    available = list(STRATEGY_REGISTRY.list().keys())
    if strategy_id not in available:
        print(f"[FAIL] 알 수 없는 전략: '{strategy_id}'")
        print(f"가능한 전략: {', '.join(available)}")
        sys.exit(1)

    # 2. 설정값 읽기 (mytrading_config.yaml)
    bt_cfg = CONFIG.get("backtest", {})
    symbols = CONFIG.get("trading", {}).get("symbols", ["005930"])
    start_date, end_date = get_backtest_period()  # end_date "today" 자동 처리

    print("=" * 55)
    print(f"  백테스트 실행")
    print(f"  전략     : {strategy_id}")
    print(f"  종목     : {symbols}")
    print(f"  기간     : {start_date} ~ {end_date}")
    print(f"  데이터   : {'모의(vps)' if resolve_mode() == 'vps' else '실전(prod)'} 계좌 키 사용")
    print("=" * 55)
    print("  Docker Lean 엔진 실행 중... (첫 실행은 이미지 다운로드로 수 분 소요)")

    # 3. 백테스트 실행 (data_provider 로 KIS 과거 데이터 연결)
    #    Lean 데이터가 backtester/.lean-workspace 에 있으므로 cwd 를 backtester 로 변경
    original_cwd = os.getcwd()
    os.chdir(BACKTESTER_DIR)
    try:
        client = LeanClient(data_provider=get_data_provider())
        result = client.backtest_strategy(
            strategy_id=strategy_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        os.chdir(original_cwd)  # 작업 디렉터리 원복

    # 4. 결과 출력 (BacktestResult 의 실제 속성은 환경에 따라 다를 수 있어 안전하게 출력)
    print("\n" + "=" * 55)
    print("  백테스트 완료")
    print("=" * 55)
    _print_result(result)


def _print_result(result):
    """BacktestResult 를 안전하게 출력 (속성명이 버전마다 다를 수 있어 방어적으로)."""
    # 자주 쓰이는 지표 후보들
    candidates = [
        ("총수익률", ["total_return", "total_return_pct", "net_profit"]),
        ("연환산수익률(CAGR)", ["cagr", "annual_return"]),
        ("샤프지수", ["sharpe", "sharpe_ratio"]),
        ("최대낙폭(MDD)", ["max_drawdown", "mdd"]),
        ("승률", ["win_rate"]),
        ("총거래수", ["total_trades", "num_trades"]),
    ]
    printed = False
    for label, attrs in candidates:
        for a in attrs:
            if hasattr(result, a):
                print(f"  {label}: {getattr(result, a)}")
                printed = True
                break

    if not printed:
        # 속성을 못 찾으면 전체 구조를 보여줌 (다음에 정확히 매핑하기 위해)
        print("  [지표 자동 매핑 실패 — result 구조를 출력합니다]")
        attrs = [x for x in dir(result) if not x.startswith("_")]
        print(f"  사용 가능한 속성: {attrs}")


if __name__ == "__main__":
    main()