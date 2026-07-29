"""손실 종목의 배당 이력 + 데이터 편입 시점 + 실제 손실 매수 시점 확인.
   가설: '배당 없던 시절에 매수 → 손실' 인가?"""
import sys, json
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())

name2code = {info["name"]: c for c, info in OP.items()}

def load_dates(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily") / f"{code}.csv"
    if not p.exists(): return []
    ds = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 6: continue
        try:
            s = parts[0][:8]
            ds.append(date(int(s[:4]), int(s[4:6]), int(s[6:8])))
        except Exception: continue
    return sorted(ds)

LOSS = ["웹젠","골프존","엠씨넥스","KG파이낸셜","KG스틸"]

for name in LOSS:
    code = name2code.get(name)
    if not code:
        for nm, c in name2code.items():
            if name[:3] in nm: code, name = c, nm; break
    if not code:
        print(f"\n{name}: op_history 에 없음"); continue
    ds = load_dates(code)
    print(f"\n{'='*55}")
    print(f"● {name} ({code})")
    print(f"  일봉 데이터: {ds[0]} ~ {ds[-1]}  ({len(ds)}일)" if ds else "  데이터 없음")
    # 연도별 배당 이력
    divs = DIV.get(code, {}).get("dividends") or []
    by_year = {}
    for x in divs:
        a = x.get("amount") or 0; s = str(x.get("date",""))
        if a <= 0 or len(s) < 4: continue
        by_year.setdefault(s[:4], 0)
        by_year[s[:4]] += a
    if by_year:
        print(f"  연도별 배당금 합:")
        for y in sorted(by_year):
            print(f"    {y}: {by_year[y]:.0f}원")
    else:
        print(f"  배당 이력: 없음 (전 기간 무배당)")
