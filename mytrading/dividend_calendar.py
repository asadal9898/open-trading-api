"""
배당락일 캘린더 — 연도별 관리 (universe.yaml 종목과 분리).

종목 자체는 안 변하지만 배당락일은 매년 변하므로 별도 파일로 둠.
배당락일 = 배당기준일(record_date)의 1거래일 전 → finance_data.get_ex_dividend_dates 가 계산.

파일: mytrading/dividend_calendar.yaml
구조:
  연도(YYYY) → 종목코드 → [{ex_date, record_date, amount}, ...]

  2025:
    "049720":
      - { ex_date: "20251230", record_date: "20251231", amount: 260.0 }
      - { ex_date: "20250627", record_date: "20250630", amount: 240.0 }

활용(나중): 배당락 다음날 과대낙폭 매수, 배당락 전 매도 판단. 백테스트로 검증 후 매매 반영.

사용:
  KIS_MODE=prod uv run python mytrading/dividend_calendar.py --update 049720 009680
  KIS_MODE=prod uv run python mytrading/dividend_calendar.py --update-universe   # universe 배당주 전체
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

_CAL_PATH = _REPO_ROOT / "mytrading" / "configs" / "dividend_calendar.yaml"


def _load() -> dict:
    """배당락일 캘린더 로드. 없으면 빈 dict."""
    if not _CAL_PATH.exists():
        return {}
    try:
        with open(_CAL_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save(data: dict):
    """배당락일 캘린더 저장 (연도 내림차순 정렬)."""
    # 연도 키를 정수 내림차순으로 정렬해 보기 좋게
    ordered = {}
    for yr in sorted(data.keys(), reverse=True):
        ordered[yr] = data[yr]
    with open(_CAL_PATH, "w", encoding="utf-8") as f:
        f.write("# 배당락일 캘린더 — 연도별 (종목과 분리). 자동 생성.\n")
        f.write("# 배당락일 = 배당기준일 1거래일 전. amount=주당배당금.\n")
        yaml.safe_dump(ordered, f, allow_unicode=True, default_flow_style=False,
                       sort_keys=False)


def get_ex_dates(symbol: str, year=None) -> list:
    """종목의 배당락일 조회.
    year 지정 시 그 연도만, 없으면 전체 연도 합쳐서 반환.
    반환: [{ex_date, record_date, amount, year}, ...]
    """
    data = _load()
    out = []
    years = [str(year)] if year else sorted(data.keys(), reverse=True)
    for yr in years:
        ydata = data.get(yr) or data.get(int(yr)) or {}
        for rec in ydata.get(symbol, []):
            out.append({**rec, "year": yr})
    return out


def update(symbols: list) -> int:
    """종목들의 배당락일을 계산해 캘린더에 저장(갱신).
    finance_data.get_ex_dividend_dates 로 계산. 반환: 갱신된 종목 수.
    """
    from mytrading.common import init
    from mytrading.finance_data import get_ex_dividend_dates
    init(require_confirm=False)

    data = _load()
    updated = 0
    for sym in symbols:
        r = get_ex_dividend_dates(sym)
        if not r or not r.get("ex_dates"):
            print(f"  {sym}: 배당락일 없음(배당 데이터 없음)")
            continue
        for e in r["ex_dates"]:
            yr = e["year"]
            data.setdefault(yr, {})
            data[yr].setdefault(sym, [])
            # 중복(같은 record_date) 방지
            existing = {x["record_date"] for x in data[yr][sym]}
            if e["record_date"] not in existing:
                data[yr][sym].append({
                    "ex_date": e["ex_date"],
                    "record_date": e["record_date"],
                    "amount": e["amount"],
                })
        # ex_date 내림차순 정렬 (각 연도 내)
        for yr in data:
            if sym in data[yr]:
                data[yr][sym].sort(key=lambda x: x["ex_date"], reverse=True)
        updated += 1
        n = sum(len(data[yr].get(sym, [])) for yr in data)
        print(f"  {sym}: 배당락일 {n}개 저장")

    _save(data)
    return updated


def _universe_dividend_symbols() -> list:
    """universe 에서 배당주(value_range 스타일 또는 dividend 필드 있는) 종목."""
    from mytrading.portfolio import load_portfolio
    pf = load_portfolio()
    out = []
    for cat in ("aggressive", "moderate", "safe"):
        for s in pf.names(cat):
            if s.get("style") == "value_range" or s.get("dividend"):
                out.append(s["code"])
    return out


def main():
    args = sys.argv[1:]
    if "--update-universe" in args:
        symbols = _universe_dividend_symbols()
        print(f"universe 배당주 {len(symbols)}개: {symbols}")
        update(symbols)
    elif "--update" in args:
        idx = args.index("--update")
        symbols = [a for a in args[idx + 1:] if not a.startswith("--")]
        if not symbols:
            print("사용: dividend_calendar.py --update 종목코드들")
            return
        update(symbols)
    else:
        # 인자 없으면 현재 캘린더 출력
        data = _load()
        if not data:
            print("배당락일 캘린더 비어있음. --update 로 채우세요.")
            return
        for yr in sorted(data.keys(), reverse=True):
            print(f"[{yr}]")
            for sym, recs in data[yr].items():
                dates = ", ".join(r["ex_date"] for r in recs)
                print(f"  {sym}: {dates}")


if __name__ == "__main__":
    main()