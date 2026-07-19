"""
유니버스 value_range 종목의 배당 이력 수집 → ~/div_history_5y.json

원천: ksdinfo_dividend (한국예탁결제원 배당정보, KIS API)
  - record_date        : 배당기준일
  - per_sto_divi_amt   : 주당배당금 (현금)

백테스트가 쓰는 구조 그대로 저장한다:
  { 종목코드: { "name": ..., "dividends": [ {"date": "YYYYMMDD", "amount": float}, ... ] } }

⚠️ 조회만 한다. 주문·유니버스 수정 없음.

사용:
    KIS_MODE=prod uv run python mytrading/collect_div.py                 # 없는 것만
    KIS_MODE=prod uv run python mytrading/collect_div.py --refresh       # 전부 다시
    KIS_MODE=prod uv run python mytrading/collect_div.py --limit 5       # 시험
    KIS_MODE=prod uv run python mytrading/collect_div.py --years 8       # 8년치

주의:
  - API 조회 기간이 길면 응답이 잘릴 수 있어 1년 단위로 나눠 호출한다.
  - 종목당 (연수)회 호출 → 76종목 × 8년이면 600여 회. --sleep 으로 간격 조절.
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import yaml

OUT = Path.home() / "div_history_5y.json"
BACKUP = Path.home() / "div_history_5y.json.bak"


def load_universe():
    """universe.yaml 의 value_range 종목 (코드, 이름)."""
    uni = yaml.safe_load(
        (_ROOT / "mytrading" / "universe.yaml").read_text(encoding="utf-8")) or {}
    out, seen = [], set()
    for cat, items in uni.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or it.get("style") != "value_range":
                continue
            code = str(it.get("code", "")).strip()
            if code and code not in seen:
                seen.add(code)
                out.append((code, it.get("name", "")))
    return out


def _to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return default


def fetch_dividends(fn, code, y0, y1, sleep_sec):
    """연도별로 나눠 배당 이력 조회 → [(기준일YYYYMMDD, 주당배당금)]"""
    rows = {}
    for yr in range(y0, y1 + 1):
        f_dt = f"{yr}0101"
        t_dt = f"{yr}1231"
        try:
            df = fn(cts="", gb1="0", f_dt=f_dt, t_dt=t_dt,
                    sht_cd=code, high_gb="")
        except Exception:
            df = None
        if df is not None and not getattr(df, "empty", True):
            for _, r in df.iterrows():
                rec = str(r.get("record_date", "")).strip()
                amt = _to_float(r.get("per_sto_divi_amt"), 0.0)
                if len(rec) >= 8 and amt > 0:
                    rows[rec] = amt          # 같은 기준일 중복 방지
        time.sleep(sleep_sec)
    return sorted(rows.items())


def main():
    args = sys.argv[1:]
    refresh = "--refresh" in args
    limit = None
    sleep_sec = 0.3
    years = 8

    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            limit = int(args[i + 1])
    if "--sleep" in args:
        i = args.index("--sleep")
        if i + 1 < len(args):
            sleep_sec = float(args[i + 1])
    if "--years" in args:
        i = args.index("--years")
        if i + 1 < len(args):
            years = int(args[i + 1])

    from mytrading.common import init
    init(require_confirm=False)

    # finance_data 임포트가 예제 모듈 경로를 sys.path 에 등록한다 (부수효과 이용)
    import mytrading.finance_data  # noqa: F401

    # finance_data 임포트가 예제 모듈 경로를 sys.path 에 등록한다 (부수효과 이용)
    import mytrading.finance_data  # noqa: F401

    # finance_data 임포트가 예제 모듈 경로를 sys.path 에 등록한다 (부수효과 이용)
    import mytrading.finance_data  # noqa: F401

    try:
        from ksdinfo_dividend import ksdinfo_dividend
    except Exception as e:
        print(f"ksdinfo_dividend 임포트 실패: {e}")
        return

    today = date.today()
    y1 = today.year
    y0 = y1 - years + 1

    data = {}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
            OUT.replace(BACKUP)
            print(f"기존 {len(data)}종목 로드 (백업: {BACKUP.name})")
        except Exception as e:
            print(f"기존 파일 읽기 실패, 새로 시작: {e}")
            data = {}

    stocks = load_universe()
    if limit:
        stocks = stocks[:limit]

    print(f"수집 대상 {len(stocks)}종목 · {y0}~{y1}년 · sleep {sleep_sec}s")
    print("-" * 66)

    filled = skipped = 0
    for idx, (code, name) in enumerate(stocks, 1):
        entry = data.get(code) or {}
        if not refresh and entry.get("dividends"):
            skipped += 1
            continue

        pairs = fetch_dividends(ksdinfo_dividend, code, y0, y1, sleep_sec)
        data[code] = {
            "name": name or entry.get("name", ""),
            "dividends": [{"date": d, "amount": a} for d, a in pairs],
        }
        filled += 1
        last = pairs[-1] if pairs else None
        tail = f"최근 {last[0]} {last[1]:,.0f}원" if last else "배당 없음"
        print(f"  [{idx:>3}/{len(stocks)}] {name[:14]:14} {code}  "
              f"{len(pairs):>2}건  {tail}")

        if idx % 5 == 0:
            OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    n_ok = sum(1 for c, _ in stocks if (data.get(c) or {}).get("dividends"))
    print("-" * 66)
    print(f"수집 완료 — 전체 {len(data)}종목 저장")
    print(f"  이번에 수집 {filled} · 기존 보유로 건너뜀 {skipped}")
    print(f"  대상 {len(stocks)}종목 중 배당 있는 종목: {n_ok}")
    print(f"  저장: {OUT}")


if __name__ == "__main__":
    main()