"""
매일 데이터 갱신 — 종목 일봉 + 거시 지수를 증분 갱신한다.

cron 으로 평일 아침 호출하면, 전일까지의 데이터를 최신으로 유지한다.
모두 증분 갱신이라 매일 돌려도 가볍다 (새 거래일 1~2건만 받음).

갱신 대상:
  1) 종목 일봉  — universe 종목 전부 (배분/백테스트에 사용)
  2) 거시 지수  — 코스피/S&P500/나스닥 (국면 판단 참고)

휴장일 처리:
  한국·미국 둘 다 휴장이면 받을 것이 없으므로 건너뛴다.
  (주말·공휴일에 cron 이 돌아도 조용히 종료)

실행:
    uv run python mytrading/daily_update.py
    uv run python mytrading/daily_update.py --force   # 휴장일이어도 강제 실행

cron (평일 아침 7시 예시):
    0 7 * * 1-5 cd ~/workspace/open-trading-api && \
      /home/yscha/.local/bin/uv run python mytrading/daily_update.py \
      >> ~/KIS/cache/daily_update.log 2>&1
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _market_open_any() -> bool:
    """한국 또는 미국 중 하나라도 오늘 개장이면 True."""
    from mytrading.market_calendar import is_market_open
    # 국내 개장 여부 (KIS 휴장일 기반)
    try:
        kr_open = is_market_open()
    except Exception:
        kr_open = True  # 확인 실패 시 보수적으로 실행
    # 미국 개장 여부 (pandas-market-calendars NYSE)
    us_open = _us_open_today()
    return bool(kr_open or us_open)


def _us_open_today() -> bool:
    """오늘 미국(NYSE) 개장 여부. 실패 시 True(보수적)."""
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("NYSE")
        today = datetime.now().strftime("%Y-%m-%d")
        sched = nyse.schedule(start_date=today, end_date=today)
        return not sched.empty
    except Exception:
        return True


def update_symbols() -> None:
    """universe 종목 일봉 증분 갱신."""
    from mytrading.common import CONFIG
    from mytrading.portfolio import get_watch_symbols
    from mytrading.data_manager import ensure_data_multi

    symbols = get_watch_symbols()
    if not symbols:
        # universe 비었으면 config 폴백
        symbols = CONFIG.get("trading", {}).get("symbols", ["005930"])
    start = CONFIG.get("data", {}).get("prefetch_start", "2020-01-01")

    print(f"\n[1/2] 종목 일봉 갱신: {len(symbols)}개 ({', '.join(symbols)})")
    ensure_data_multi(symbols, start=start)  # end 생략 → 오늘까지, 증분


def update_indices() -> None:
    """거시 지수 증분 갱신."""
    from mytrading.index_data import ensure_all_indices
    print(f"\n[2/2] 거시 지수 갱신")
    ensure_all_indices()  # 증분 (기존 마지막 날짜 다음부터)


def main() -> None:
    force = "--force" in sys.argv[1:]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== 일일 데이터 갱신 {now} ===")

    from mytrading.common import init
    init(require_confirm=False)  # 모의 인증

    if not force and not _market_open_any():
        print("오늘은 한국·미국 모두 휴장 — 갱신 건너뜀 (--force 로 강제 가능)")
        return

    update_symbols()
    update_indices()
    print(f"\n=== 갱신 완료 ===")


if __name__ == "__main__":
    main()