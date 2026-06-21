"""
데이터 미리 받기(prefetch) 러너
config 의 종목들을 긴 기간으로 캐시에 받아둔다. 이후 백테스트는 캐시만 사용(API 0회).
증분 지원: 이미 받아둔 종목은 마지막 날짜 이후만 추가.

실행:
    uv run python mytrading/runners/prefetch_data.py
    uv run python mytrading/runners/prefetch_data.py 005930 000660   # 종목 지정
    KIS_MODE=vps uv run python mytrading/runners/prefetch_data.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mytrading.common import CONFIG
from mytrading.data_manager import ensure_data_multi


def main():
    # 종목: 인자 우선, 없으면 config
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        symbols = CONFIG.get("trading", {}).get("symbols", ["005930"])

    # 받을 시작 기간: config 의 prefetch_start 우선, 없으면 2020-01-01
    data_cfg = CONFIG.get("data", {})
    start = data_cfg.get("prefetch_start", "2020-01-01")

    print(f"prefetch 시작: {symbols} (start={start} ~ 오늘)")
    ensure_data_multi(symbols, start=start)  # end 생략 → 오늘까지


if __name__ == "__main__":
    main()