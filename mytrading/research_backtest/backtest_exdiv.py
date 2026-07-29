"""배당락 검증: div_history record_date → 배당락일 역산(1거래일 전).
   A. 배당락 전 수급 유입 (D-20~D-1 상승?)
   B. 배당락 갭 크기 (배당금 대비 실제 하락)
   C. 배당락 후 반등 (D+1~D+10, 과대낙폭 매수 기회?)"""
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
            ds.append(d)
            px[d]={"o":float(pt[1]),"h":float(pt[2]),"l":float(pt[3]),"c":float(pt[4])}
        except: continue
    return sorted(ds), px

# 배당락 이벤트 수집: record_date 직전 거래일 = 배당락일
events=[]   # {code, exday_idx, ds, px, amount, close_before}
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
        # record_date 직전 거래일 = 배당락일 (배당락일 종가엔 배당 반영 안됨)
        i = bisect.bisect_left(ds, rec)
        if i<=0 or i>=len(ds): continue
        ex_idx = i-1 if ds[i]==rec else i-1   # rec 당일 또는 직전
        # 실제로는 배당락일 = record_date 1거래일 전. rec가 거래일이면 그 전날.
        ex_idx = bisect.bisect_left(ds, rec) - 1
        if ex_idx < 21 or ex_idx >= len(ds)-11: continue
        events.append({"code":code,"ex":ex_idx,"ds":ds,"px":px,"amt":a})

print(f"배당락 이벤트: {len(events)}건 ({len(set(e['code'] for e in events))}종목)")
print()

# --- A. 배당락 전 20일 수급 (주가 상승?) ---
pre_rets=[]; pre_by_yield=[]
for e in events:
    ds,px,ex=e["ds"],e["px"],e["ex"]
    c_20 = px[ds[ex-20]]["c"]; c_1 = px[ds[ex]]["c"]
    if c_20>0:
        pre = (c_1/c_20-1)*100
        pre_rets.append(pre)
        dy = e["amt"]/c_1*100   # 배당수익률(단순)
        pre_by_yield.append((dy, pre))

print("##### A. 배당락 전 20거래일 주가변화 (수급 유입?) #####")
print(f"  평균: {statistics.mean(pre_rets):+.2f}%  중앙값: {statistics.median(pre_rets):+.2f}%")
print(f"  상승 비율: {sum(1 for r in pre_rets if r>0)/len(pre_rets)*100:.0f}%")
# 고배당 vs 저배당
hi=[p for dy,p in pre_by_yield if dy>=3]; lo=[p for dy,p in pre_by_yield if dy<3]
if hi and lo:
    print(f"  고배당(≥3%) 전20일: {statistics.mean(hi):+.2f}%  (n={len(hi)})")
    print(f"  저배당(<3%) 전20일: {statistics.mean(lo):+.2f}%  (n={len(lo)})")
print()

# --- B. 배당락 갭 (배당락일 종가 → 다음날 시가/종가) ---
gaps=[]
for e in events:
    ds,px,ex=e["ds"],e["px"],e["ex"]
    c_ex = px[ds[ex]]["c"]           # 배당락 전 마지막 종가 (배당 포함 가치)
    o_next = px[ds[ex+1]]["o"]       # 배당락일 시가
    if c_ex>0:
        gap=(o_next/c_ex-1)*100
        theo=-e["amt"]/c_ex*100      # 이론 배당락 (배당금만큼)
        gaps.append((gap, theo))
print("##### B. 배당락 갭 (배당락일 시가 vs 전일종가) #####")
print(f"  실제 갭 평균: {statistics.mean([g for g,_ in gaps]):+.2f}%")
print(f"  이론 배당락 평균: {statistics.mean([t for _,t in gaps]):+.2f}%")
print(f"  → 갭이 이론보다 크면(더 하락) 과대낙폭")
excess=[g-t for g,t in gaps]
print(f"  초과하락(갭-이론) 평균: {statistics.mean(excess):+.2f}%  중앙값: {statistics.median(excess):+.2f}%")
print()

# --- C. 배당락 후 10일 반등 (과대낙폭 매수 기회?) ---
post=[]
for e in events:
    ds,px,ex=e["ds"],e["px"],e["ex"]
    o_next=px[ds[ex+1]]["o"]; c_10=px[ds[ex+10]]["c"]
    if o_next>0:
        post.append((c_10/o_next-1)*100)
print("##### C. 배당락 다음날 시가 → 10일 후 (반등?) #####")
print(f"  평균: {statistics.mean(post):+.2f}%  중앙값: {statistics.median(post):+.2f}%")
print(f"  상승 비율: {sum(1 for r in post if r>0)/len(post)*100:.0f}%")
