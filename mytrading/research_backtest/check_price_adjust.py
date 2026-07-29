"""주가 데이터가 배당수정주가인지 판별.
   고배당 이벤트 전후 실제 가격을 눈으로 확인."""
import sys, json
from pathlib import Path
from datetime import date
import bisect
sys.path.insert(0, str(Path.cwd()))
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())

def load(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return None
    ds=[];px={}
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; d=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
            ds.append(d); px[d]=(float(pt[1]),float(pt[4]))
        except: continue
    return sorted(ds),px

# 배당수익률 가장 큰 이벤트 5개
evs=[]
for code,info in DIV.items():
    r=load(code)
    if not r: continue
    ds,px=r
    for x in (info.get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: rec=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        ex=bisect.bisect_left(ds,rec)-1
        if ex<3 or ex>=len(ds)-3: continue
        c=px[ds[ex-1]][1]
        if c>0: evs.append((a/c*100, code, info.get("name",""), rec, ex, ds, px, a))
evs.sort(reverse=True)

print("배당수익률 상위 5개 이벤트 — 배당락 전후 실제 가격")
print("="*60)
for dy,code,name,rec,ex,ds,px,amt in evs[:5]:
    print(f"\n● {name}({code})  배당금 {amt:,.0f}원  이론 낙폭 {dy:.1f}%")
    print(f"  기준일(record_date) {rec} / 배당락일 추정 {ds[ex]}")
    for k in range(ex-3, ex+3):
        o,c = px[ds[k]]
        mark = "  ← 배당락일" if k==ex else ("  (권리부 최종)" if k==ex-1 else "")
        print(f"    {ds[k]}  시가{o:>9,.0f}  종가{c:>9,.0f}{mark}")
