# -*- coding: utf-8 -*-
"""전체 KRX 종목의 2020~2021 배당 수집 → 2021 시점 배당주 검색용.
   ksdinfo_dividend(예탁원 배당) 재활용. 조회만, 주문 없음.
실행: KIS_MODE=prod uv run python mytrading/collect_div_all_2021.py
"""
import json, sys, time
from pathlib import Path
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

OUT = _ROOT / "mytrading" / "research_backtest" / "data" / "div_multiyear.json"
CODES_FILE = Path("/tmp/all_krx_codes.json")
Y0, Y1 = 2020, 2024
SLEEP = 0.5

def _to_float(v, d=0.0):
    try: return float(str(v).replace(",", "").strip())
    except: return d

def fetch(fn, code, y0, y1):
    rows = {}
    for yr in range(y0, y1 + 1):
        try:
            df = fn(cts="", gb1="0", f_dt=f"{yr}0101", t_dt=f"{yr}1231",
                    sht_cd=code, high_gb="")
        except Exception:
            df = None
        if df is not None and not getattr(df, "empty", True):
            for _, r in df.iterrows():
                rec = str(r.get("record_date", "")).strip()
                amt = _to_float(r.get("per_sto_divi_amt"), 0.0)
                if len(rec) >= 8 and amt > 0:
                    rows[rec] = amt
        time.sleep(SLEEP)
    return sorted(rows.items())

def main():
    from mytrading import common
    common.init(require_confirm=False)
    import mytrading.finance_data  # sys.path 등록 부수효과
    from ksdinfo_dividend import ksdinfo_dividend

    codes = json.loads(CODES_FILE.read_text())
    # 이어하기: 기존 결과 로드
    out = {}
    if OUT.exists():
        out = json.loads(OUT.read_text())
    done = set(out.keys())
    todo = [(c, n) for c, n in codes if c not in done]
    print(f"전체 {len(codes)}종목, 완료 {len(done)}, 남음 {len(todo)}")

    for i, (code, name) in enumerate(todo, 1):
        divs = fetch(ksdinfo_dividend, code, Y0, Y1)
        if divs:
            out[code] = {"name": name,
                         "dividends": [{"date": d, "amount": a} for d, a in divs]}
        if i % 20 == 0:
            OUT.write_text(json.dumps(out, ensure_ascii=False))
            print(f"  {i}/{len(todo)} — 배당있는 종목 누적 {len(out)}")
    OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"완료: {len(out)}종목 배당 수집 → {OUT.name}")

if __name__ == "__main__":
    main()
