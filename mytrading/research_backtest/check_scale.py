"""배당률 계산 오류 원인 진단: CSV 주가 스케일 vs universe.yaml 기록값 대조."""
import sys, json, re
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path.cwd()))
DIV = json.loads((Path.home()/"div_history_5y.json").read_text())

# universe.yaml 의 AI 기록 배당률 파싱
uni = Path("mytrading/universe.yaml").read_text(encoding="utf-8")
recorded = {}
for m in re.finditer(r'code:\s*"(\d+)".*?name:\s*"([^"]+)".*?배당\s*([\d.]+)%', uni):
    recorded[m.group(1)] = (m.group(2), float(m.group(3)))

def last_close(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return None
    lines = p.read_text().splitlines()
    for line in reversed(lines):
        pt = line.split(",")
        if len(pt) >= 6:
            try: return pt[0][:8], float(pt[4])
            except: pass
    return None

print(f"{'종목':14}{'CSV최종종가':>12}{'최근1년배당':>11}{'계산배당률':>10}{'기록배당률':>10}{'배율':>7}")
print("="*68)
for code,(name,rec_dy) in sorted(recorded.items(), key=lambda x:-x[1][1])[:15]:
    lc = last_close(code)
    if not lc: continue
    d_str, price = lc
    # 최근 1년 배당 합
    divs = DIV.get(code,{}).get("dividends") or []
    tot = 0.0
    for x in divs:
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a>0 and len(s)>=4 and s[:4] >= "2025": tot += a
    calc = (tot/price*100) if price else 0
    ratio = (calc/rec_dy) if rec_dy else 0
    print(f"{name[:14]:14}{price:>12,.0f}{tot:>11,.0f}{calc:>9.1f}%{rec_dy:>9.1f}%{ratio:>6.1f}x")
