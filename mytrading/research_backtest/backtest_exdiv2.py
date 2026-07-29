"""배당락 검증 v2 — 오류 수정판.
   수정1: 배당락일 인덱스 정정 (권리부 최종일 종가 → 배당락일 시가/종가)
   수정2: 배당수익률을 20일 전 주가 기준으로 (순환논리 제거)"""
import sys, json, statistics
from pathlib import Path
from datetime import date
import bisect
sys.path.insert(0, str(Path.cwd()))

DIV = json.loads((Path.home() / "div_history_5y.json").read_text())

def load_ohlc(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return None
    ds=[]; px={}
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; d=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
            ds.append(d); px[d]={"o":float(pt[1]),"c":float(pt[4])}
        except: continue
    return sorted(ds), px

events=[]
for code, info in DIV.items():
    r = load_ohlc(code)
    if not r: continue
    ds, px = r
    if len(ds) < 300: continue
    for x in (info.get("dividends") or []):
        a = x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: rec=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        # 배당락일 = record_date 직전 거래일
        ex = bisect.bisect_left(ds, rec) - 1
        if ex < 22 or ex >= len(ds)-11: continue
        events.append({"code":code,"ex":ex,"ds":ds,"px":px,"amt":a})

print(f"배당락 이벤트: {len(events)}건 ({len(set(e['code'] for e in events))}종목)")
print()

# A. 배당락 전 20일 (권리부 최종일까지)  ※ 배당률은 20일 전 주가 기준 (교란 제거)
pre=[]
for e in events:
    ds,px,ex=e["ds"],e["px"],e["ex"]
    c0=px[ds[ex-21]]["c"]; c1=px[ds[ex-1]]["c"]   # 권리부 최종일 = ex-1
    if c0>0:
        dy = e["amt"]/c0*100        # ★ 20일 전 주가 기준 (순환논리 제거)
        pre.append((dy, (c1/c0-1)*100))
print("##### A. 배당락 전 20일 (권리부 최종일까지) #####")
vals=[p for _,p in pre]
print(f"  평균 {statistics.mean(vals):+.2f}%  중앙값 {statistics.median(vals):+.2f}%  상승 {sum(1 for v in vals if v>0)/len(vals)*100:.0f}%")
hi=[p for dy,p in pre if dy>=3]; lo=[p for dy,p in pre if dy<3]
if hi and lo:
    print(f"  고배당(≥3%, 20일전 기준): {statistics.mean(hi):+.2f}% (n={len(hi)})")
    print(f"  저배당(<3%):              {statistics.mean(lo):+.2f}% (n={len(lo)})")
print()

# B. 배당락 갭 (권리부 최종일 종가 → 배당락일 시가/종가)  ★수정
gaps=[]
for e in events:
    ds,px,ex=e["ds"],e["px"],e["ex"]
    c_last=px[ds[ex-1]]["c"]       # 권리부 최종일 종가 (배당 권리 포함)
    o_ex=px[ds[ex]]["o"]           # 배당락일 시가
    c_ex=px[ds[ex]]["c"]           # 배당락일 종가
    if c_last>0:
        theo=-e["amt"]/c_last*100
        gaps.append(((o_ex/c_last-1)*100, (c_ex/c_last-1)*100, theo))
print("##### B. 배당락 갭 (권리부 최종일 종가 기준) #####")
print(f"  배당락일 시가 갭: {statistics.mean([g for g,_,_ in gaps]):+.2f}%")
print(f"  배당락일 종가 변화: {statistics.mean([c for _,c,_ in gaps]):+.2f}%")
print(f"  이론 배당락:      {statistics.mean([t for _,_,t in gaps]):+.2f}%")
ex_o=[g-t for g,_,t in gaps]; ex_c=[c-t for _,c,t in gaps]
print(f"  초과하락(시가-이론): 평균 {statistics.mean(ex_o):+.2f}%  중앙값 {statistics.median(ex_o):+.2f}%")
print(f"  초과하락(종가-이론): 평균 {statistics.mean(ex_c):+.2f}%  중앙값 {statistics.median(ex_c):+.2f}%")
print("  (음수 = 이론보다 더 떨어짐 = 과대낙폭)")
print()

# C. 배당락일 종가 → 10일 후
post=[]
for e in events:
    ds,px,ex=e["ds"],e["px"],e["ex"]
    c_ex=px[ds[ex]]["c"]; c10=px[ds[ex+10]]["c"]
    if c_ex>0: post.append((c10/c_ex-1)*100)
print("##### C. 배당락일 종가 → 10일 후 (반등?) #####")
print(f"  평균 {statistics.mean(post):+.2f}%  중앙값 {statistics.median(post):+.2f}%  상승 {sum(1 for v in post if v>0)/len(post)*100:.0f}%")
