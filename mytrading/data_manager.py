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


def get_52w_range(symbol: str, asof: Optional[date] = None, window: int = 252) -> Optional[dict]:
    """
    52주(기본 252거래일) 최저/최고가 + 현재가 위치.
    캐시된 일봉 CSV(_cache_path)에서 계산. 데이터 부족/없으면 None.

    asof: 기준일(없으면 최신). 이 날짜 이하의 마지막 window개로 계산.
    반환: {low, high, last, low_date, high_date, pct_from_low, pct_from_high, n}
    - value_range(저점매수): pct_from_low <= buy_zone% 이면 저점 근처 → 매수
    - momentum(신고가):     pct_from_high >= -X% (0 근접) 이면 고점권 → 매수
    """
    path = _cache_path(symbol)
    if not path.exists():
        return None
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 5:
                continue
            d = parts[0]
            try:
                hi = float(parts[2]); lo = float(parts[3]); cl = float(parts[4])
            except ValueError:
                continue  # 헤더 등 스킵
            rows.append((d, hi, lo, cl))
    if not rows:
        return None

    if asof is not None:
        cut = asof.strftime("%Y%m%d")
        rows = [r for r in rows if r[0] <= cut]
    if not rows:
        return None

    win = rows[-window:]
    last_d, _, _, last_close = win[-1]
    high = max(r[1] for r in win)
    low = min(r[2] for r in win)
    high_date = max(win, key=lambda r: r[1])[0]
    low_date = min(win, key=lambda r: r[2])[0]

    pct_from_low = (last_close - low) / low * 100 if low > 0 else 0.0
    pct_from_high = (last_close - high) / high * 100 if high > 0 else 0.0

    return {
        "symbol": symbol,
        "low": low, "high": high, "last": last_close,
        "low_date": low_date, "high_date": high_date,
        "pct_from_low": round(pct_from_low, 2),
        "pct_from_high": round(pct_from_high, 2),
        "n": len(win),
    }

def get_trend(symbol: str, asof: Optional[date] = None,
              mas=(5, 20, 60)) -> Optional[dict]:
    """
    이동평균선 + 추세 상태. momentum(주도주) 추세 판단용.
    캐시된 일봉 CSV(_cache_path)에서 계산. 데이터 부족/없으면 None.

    mas: 계산할 이동평균 기간들 (기본 5/20/60일선)
    asof: 기준일(없으면 최신).
    반환: {last, ma5, ma20, ma60, above_ma5, above_ma20, ma5_slope, n}
    ※ momentum 매도 규칙(5일선 이탈 등)은 백테스트로 확정 예정.
      이 함수는 판단에 필요한 '재료'(이동평균·추세)만 제공.
    """
    path = _cache_path(symbol)
    if not path.exists():
        return None
    closes = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                cl = float(parts[4])
            except ValueError:
                continue
            closes.append((parts[0], cl))
    if not closes:
        return None

    if asof is not None:
        cut = asof.strftime("%Y%m%d")
        closes = [c for c in closes if c[0] <= cut]
    if not closes:
        return None

    vals = [c[1] for c in closes]
    last = vals[-1]

    def _ma(period, offset=0):
        end = len(vals) - offset
        if end < period:
            return None
        seg = vals[end - period:end]
        return sum(seg) / period

    out = {"symbol": symbol, "last": last, "n": len(vals)}
    for p in mas:
        m = _ma(p)
        out[f"ma{p}"] = round(m, 2) if m is not None else None

    ma5 = out.get("ma5")
    ma20 = out.get("ma20")
    out["above_ma5"] = (last >= ma5) if ma5 is not None else None
    out["above_ma20"] = (last >= ma20) if ma20 is not None else None

    ma5_prev = _ma(5, offset=1)
    if ma5 is not None and ma5_prev is not None:
        out["ma5_slope"] = round(ma5 - ma5_prev, 2)
    else:
        out["ma5_slope"] = None

    return out
