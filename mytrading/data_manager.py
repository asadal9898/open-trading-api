"""
mytrading 데이터 관리자
종목 일봉 데이터를 backtester 캐시(.lean-workspace/data/equity/krx/daily/{symbol}.csv)에
받아두고, 증분 업데이트한다. 원본 backtester 코드는 수정하지 않는다.

핵심 기능:
  - ensure_data(symbol, start): 캐시가 없으면 전체 받기, 있으면 마지막 날짜 다음날~오늘만 증분 추가
  - ensure_data_multi([...]): 여러 종목 한 번에

데이터 위치(한 곳 관리):
  <repo>/backtester/.lean-workspace/data/equity/krx/daily/{symbol}.csv
  → 백테스트(run_backtest)와 동일 캐시를 공유하므로, 미리 받아두면 백테스트는 API 호출 0회.

csv 형식: YYYYMMDD,open,high,low,close,volume  (시간순: 과거→최신)

사용:
    uv run python mytrading/runners/prefetch_data.py
또는 코드에서:
    from mytrading.data_manager import ensure_data
    ensure_data("005930", start="2020-01-01")
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from mytrading.common import get_data_provider, resolve_mode

# 캐시 경로 (backtester 와 공유) — common 의 ROOT 기준
_THIS_DIR = Path(__file__).resolve().parent          # .../mytrading
_REPO_ROOT = _THIS_DIR.parent                          # .../open-trading-api
CACHE_DIR = _REPO_ROOT / "backtester" / ".lean-workspace" / "data" / "equity" / "krx" / "daily"


def _today() -> date:
    return datetime.now().date()


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.lower()}.csv"


def _last_date_in_csv(path: Path) -> Optional[date]:
    """csv 마지막 줄의 날짜(YYYYMMDD) 반환. 없으면 None."""
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            lines = [ln for ln in f if ln.strip()]
        if not lines:
            return None
        last = lines[-1].split(",")[0]
        return datetime.strptime(last, "%Y%m%d").date()
    except Exception:
        return None


def _bar_to_csv_line(bar) -> str:
    """Bar(time, open, high, low, close, volume) → 'YYYYMMDD,o,h,l,c,v'"""
    d = bar.time
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.strftime('%Y%m%d')},{int(bar.open)},{int(bar.high)}," \
           f"{int(bar.low)},{int(bar.close)},{int(bar.volume)}"


def ensure_data(symbol: str, start: str = "2020-01-01", end: Optional[str] = None) -> None:
    """
    종목 캐시를 보장한다.
      - 캐시 없음     → start ~ end(기본 오늘) 전체 받기
      - 캐시 있음     → (마지막 날짜+1) ~ end 만 받아서 append (증분)
      - 이미 최신     → 아무것도 안 함
    """
    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt = _today() if end is None else datetime.strptime(end, "%Y-%m-%d").date()

    path = _cache_path(symbol)
    last = _last_date_in_csv(path)

    if last is None:
        fetch_start, mode = start_dt, "full"
    elif last >= end_dt:
        print(f"  - {symbol}: 이미 최신 (마지막 {last}) → 스킵")
        return
    else:
        fetch_start, mode = last + timedelta(days=1), "incremental"

    print(f"  - {symbol}: {mode} 받기 ({fetch_start} ~ {end_dt})")

    provider = get_data_provider()
    bars = provider.get_history(symbol, fetch_start, end_dt)  # List[Bar], 시간순

    if not bars:
        print(f"    (받은 데이터 없음 — 휴장 구간이거나 신규 데이터 없음)")
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [_bar_to_csv_line(b) for b in bars]

    if mode == "full":
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"    전체 {len(lines)}건 저장 → {path.name}")
    else:  # incremental: 기존 파일 끝에 append (중복 날짜 방지)
        existing_last = last
        new_lines = [ln for ln in lines
                     if datetime.strptime(ln.split(",")[0], "%Y%m%d").date() > existing_last]
        if not new_lines:
            print("    추가할 신규 일자 없음")
            return
        with open(path, "a") as f:
            f.write("\n".join(new_lines) + "\n")
        print(f"    증분 {len(new_lines)}건 추가 → {path.name}")


def ensure_data_multi(symbols: List[str], start: str = "2020-01-01",
                      end: Optional[str] = None) -> None:
    """여러 종목 캐시 보장."""
    print(f"데이터 관리: {len(symbols)}개 종목 (mode={resolve_mode()})")
    for sym in symbols:
        ensure_data(sym, start=start, end=end)
    print("데이터 관리 완료")