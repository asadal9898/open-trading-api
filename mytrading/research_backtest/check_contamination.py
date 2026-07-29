"""배당률 오염 범위 측정: 271개 이벤트 중 비현실적 배당률이 몇 건인가."""
import sys, json, statistics
from pathlib import Path
from datetime import date
import bisect
sys.path.insert(0, str(Path.cwd()))
DIV = json.loads((Path.home()/"div_history_5y.json").read_text())

def load(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return None
    ds=[];px={}
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; d=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
            ds.append(d); px[d]=float(pt[4])
        except: continue
    return sorted(ds),px

suspect={}; ok=0; total=0; by_year={}
for code,info in DIV.items():
    r=load(code)
    if not r: continue
    ds,px=r
    for x in (info.get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: rec=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        if rec < date(2021,1,1): continue
        i=bisect.bisect_left(ds,rec)-1
        if i<0 or i>=len(ds): continue
        dy=a/px[ds[i]]*100
        total+=1
        by_year.setdefault(rec.year,[]).append(dy)
        if dy>15:    # 단일 배당 15% 초과 = 비현실적
            suspect.setdefault(code,[]).append((rec,dy,a,px[ds[i]]))
        else: ok+=1

print(f"2021년 이후 배당 이벤트: {total}건")
print(f"  정상(≤15%): {ok}건 ({ok/total*100:.1f}%)")
print(f"  의심(>15%): {total-ok}건 ({(total-ok)/total*100:.1f}%)  — {len(suspect)}종목")
print()
print("연도별 배당률 중앙값 (왜곡이 과거로 갈수록 심한가):")
for y in sorted(by_year):
    v=by_year[y]
    print(f"  {y}: 중앙값 {statistics.median(v):5.1f}%  최대 {max(v):6.1f}%  (n={len(v)})")
print()
print("의심 종목 (분할·전환 추정):")
name={c:DIV[c].get('name','') for c in DIV}
for code,lst in sorted(suspect.items(), key=lambda x:-len(x[1])):
    print(f"  {name.get(code,code)[:12]:12} {len(lst)}건  예: {lst[0][0]} 배당{lst[0][2]:,.0f}원/주가{lst[0][3]:,.0f}원={lst[0][1]:.0f}%")
