"""
환율 알림 — 3년 평균보다 낮은 환율 감지 (주말 리포트 포함용).

USD/JPY/CNY 각 환율에 대해:
  - 3년 평균(최근 ~750 거래일 종가 평균) 계산
  - 최근 1주(월~금) 중 3년 평균보다 낮은 날(요일+가격) 추출
  - 낮은 날이 하나라도 있으면 리포트에 포함 (환전·해외투자 매수 타이밍 신호)

데이터: index_data 가 받아둔 CSV (backtester/.lean-workspace/data/index/fx_*.csv)
  형식: 날짜(YYYYMMDD),시가,고가,저가,종가,거래량  (헤더 없음)

사용:
  uv run python mytrading/fx_alert.py            # 콘솔 출력
  from mytrading.telegram.fx_alert import build_fx_alert  # 리포트에서 호출
"""
import sys
from pathlib import Path
from datetime import datetime, date, timedelta

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_INDEX_DIR = _REPO_ROOT / "backtester" / ".lean-workspace" / "data" / "index"

# 환율 종류 (key: CSV 파일명, 표시명, 단위)
_FX = [
    ("fx_usd", "달러/원",   "원", None),
    ("fx_jpy", "100엔/원", "원", "div100"),
    ("fx_cny", "위안/원",   "원", "div"),
]

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
_YEARS_AVG = 3
_TRADING_DAYS_PER_YEAR = 250   # 3년 ≈ 750 거래일


def _read_fx_csv(key: str):
    """환율 CSV 읽기 → [(date_obj, close), ...] 날짜 오름차순. 없으면 []."""
    path = _INDEX_DIR / f"{key}.csv"
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split(",")
            if len(parts) < 5:
                continue
            ds = parts[0].strip()
            if len(ds) != 8 or not ds.isdigit():
                continue   # 헤더·이상행 스킵
            try:
                d = datetime.strptime(ds, "%Y%m%d").date()
                close = float(parts[4])
            except (ValueError, IndexError):
                continue
            rows.append((d, close))
    except Exception:
        return []
    rows.sort(key=lambda x: x[0])
    return rows


def _read_fx_krw(key, conv):
    """환율을 원화 기준으로. conv=None 그대로, 'div'=USD÷값, 'div100'=USD÷값×100."""
    rows = _read_fx_csv(key)
    if conv is None:
        return rows
    usd = {d: c for d, c in _read_fx_csv("fx_usd")}
    out = []
    for d, c in rows:
        u = usd.get(d)
        if u is None or c == 0:
            continue
        if conv == "div":
            out.append((d, u / c))
        elif conv == "div100":
            out.append((d, u / c * 100))
    return out


def _fx_status(key: str, name: str, unit: str, conv: str = None, asof: date = None) -> dict:
    """한 환율의 3년평균 대비 주간 저가 분석.
    반환: {key, name, avg3y, latest, low_days:[(요일,가격,날짜)], has_low}
    """
    asof = asof or date.today()
    rows = _read_fx_krw(key, conv)
    if len(rows) < 30:
        return {"key": key, "name": name, "avg3y": None,
                "latest": None, "low_days": [], "has_low": False}

    # 3년 평균 (최근 ~750 거래일 종가)
    n3y = _YEARS_AVG * _TRADING_DAYS_PER_YEAR
    recent3y = rows[-n3y:] if len(rows) >= n3y else rows
    avg3y = sum(c for _, c in recent3y) / len(recent3y)

    latest = rows[-1][1]

    # 최근 1주(asof 포함 직전 7일 내 거래일)
    week_start = asof - timedelta(days=7)
    week_rows = [(d, c) for d, c in rows if week_start < d <= asof]

    # 3년 평균보다 낮은 요일만
    low_days = []
    for d, c in week_rows:
        if c < avg3y:
            low_days.append((_WEEKDAY_KR[d.weekday()], c, d))

    return {
        "key": key, "name": name, "unit": unit,
        "avg3y": avg3y, "latest": latest,
        "low_days": low_days, "has_low": bool(low_days),
    }


def build_fx_alert(asof: date = None) -> str:
    """주말 리포트용 환율 알림 텍스트. 낮은 환율 없으면 빈 문자열."""
    asof = asof or date.today()
    blocks = []
    for key, name, unit, conv in _FX:
        st = _fx_status(key, name, unit, conv, asof)
        if not st["has_low"]:
            continue
        # 낮은 요일들: "월 1400, 수 1350"
        days_str = ", ".join(
            f"{wd} {c:,.0f}{unit}" for wd, c, _ in st["low_days"]
        )
        blocks.append(
            f"• {name} (3년평균 {st['avg3y']:,.0f}{unit} 아래 날):\n  {days_str}"
        )

    if not blocks:
        return ""   # 모두 평균 이상 → 알림 없음

    header = "💱 환율 — 3년 평균보다 낮은 날 (환전·해외투자 참고)"
    return header + "\n" + "\n".join(blocks)


def main():
    asof = date.today()
    txt = build_fx_alert(asof)
    if txt:
        print(txt)
    else:
        print("환율 알림 없음 — 모든 환율이 3년 평균 이상입니다.")
        # 참고용 현재 상태도 출력
        print("\n[현재 상태]")
        for key, name, unit, conv in _FX:
            st = _fx_status(key, name, unit, conv, asof)
            if st["avg3y"]:
                gap = st["latest"] - st["avg3y"]
                sign = "+" if gap >= 0 else ""
                print(f"  {name}: 현재 {st['latest']:,.0f} / 3년평균 "
                      f"{st['avg3y']:,.0f} ({sign}{gap:,.0f})")


if __name__ == "__main__":
    main()