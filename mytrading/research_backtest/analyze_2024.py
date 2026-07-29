"""2024년 마이너스 원인 분석: 월별 자산추이 + 매매내역 + 연말 보유종목 평가손익
   + 같은 기간 코스피와 비교."""
import sys, json
from pathlib import Path
from datetime import date, timedelta
import bisect
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP=json.loads((Path.home()/"op_history.json").read_text())
DIV=json.loads((Path.home()/"div_history_5y.json").read_text())
PUB={"Q1":(5,15),"Q2":(8,15),"Q3":(11,15),"Q4":(3,15)}
BUY_ZONE,TARGET,STOP,AVGDOWN=5.0,15.0,-50.0,-30.0
PREMIUM=0.0; CAPITAL=10_000_000; UNIT=1_000_000; MAX_POS=10; MAX_YIELD=15.0

def load_rate(p):
    r={}
    for line in Path(p).read_text().splitlines():
        pt=line.split(",")
        if len(pt)<5: continue
        try:
            s=pt[0][:8]; r[date(int(s[:4]),int(s[4:6]),int(s[6:8]))]=float(pt[4])
        except: continue
    return r
KTB=load_rate("backtester/.lean-workspace/data/index/ktb3y.csv"); kd=sorted(KTB)
def rate_at(d):
    i=bisect.bisect_right(kd,d)-1
    return KTB[kd[i]] if i>=0 else None

def load_px(code):
    p=Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return {}
    out={}
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; out[date(int(s[:4]),int(s[4:6]),int(s[6:8]))]=float(pt[4])
        except: continue
    return out

prices={}; codes=[]
for c,info in OP.items():
    if info["style"]!="value_range": continue
    px=load_px(c)
    if len(px)<300: continue
    prices[c]=px; codes.append(c)
sd={c:sorted(prices[c]) for c in codes}
name={c:OP[c]["name"] for c in codes}

def bad(code,dd,a):
    ds=sd[code]; i=bisect.bisect_left(ds,dd)-1
    if 0<=i<len(ds):
        p=prices[code][ds[i]]
        if p>0 and a/p*100>MAX_YIELD: return True
    return False
DL={}
for c in codes:
    out=[]
    for x in (DIV.get(c,{}).get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: dd=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        if bad(c,dd,a): continue
        out.append((dd,a))
    DL[c]=sorted(out)

def hr(code,as_of):
    op=OP[code]["op"]; h={}; rc={}
    for ys,qd in op.items():
        yr=int(ys)
        for q in ("Q1","Q2","Q3","Q4"):
            v=qd.get(q)
            if v is None: continue
            m,dd=PUB[q]; r=date(yr+(1 if q=="Q4" else 0),m,dd)
            if r>as_of: continue
            h.setdefault(yr,{})[q]=v; rc[(yr,q)]=r
    return h,rc
_pc={}
def phase_at(c,d):
    h,r=hr(c,d); k=(c,tuple(sorted(r.values())))
    if k not in _pc: _pc[k]=_judge_phase_from_data(h,r,d)["phase"]
    return _pc[k]
def dy_at(c,d,p):
    t=sum(a for dd,a in DL[c] if d-timedelta(days=365)<=dd<=d)
    return t/p*100 if p else 0
def dv(c,d0,d1): return sum(a for dd,a in DL[c] if d0<dd<=d1)

alldays=sorted(set().union(*[set(prices[c]) for c in codes]))
alldays=[d for d in alldays if d>=date.today()-timedelta(days=1825)]
def low52(c,d):
    ds=sd[c]; i=bisect.bisect_right(ds,d)
    w=[prices[c][x] for x in ds[max(0,i-252):i]]
    return min(w) if len(w)>=100 else None

cash=CAPITAL; pos={}; trades2024=[]; monthly={}
def tv(d):
    v=cash
    for c,p_ in pos.items():
        ds=sd[c]; i=bisect.bisect_right(ds,d)-1
        if i>=0: v+=p_["u"]*prices[c][ds[i]]
    return v

for d in alldays:
    for c in list(pos):
        if d not in prices[c]: continue
        p=prices[c][d]; ps=pos[c]; avg=ps["cost"]/ps["u"]; g=(p/avg-1)*100
        if g<=AVGDOWN and not ps["add"] and cash>=UNIT:
            ps["u"]+=UNIT/p; ps["cost"]+=UNIT; ps["add"]=True; cash-=UNIT
            if d.year==2024: trades2024.append(("물타기",d,name[c],p,0))
        if g<=STOP or g>=TARGET:
            got=dv(c,ps["ent"],d); ret=(ps["u"]*(p+got)/ps["cost"]-1)*100
            cash+=ps["u"]*(p+got)
            if d.year==2024: trades2024.append(("익절" if g>=TARGET else "손절",d,name[c],p,ret))
            del pos[c]
    for c in codes:
        if c in pos or len(pos)>=MAX_POS or cash<UNIT: continue
        if d not in prices[c]: continue
        p=prices[c][d]; lo=low52(c,d)
        if lo is None or (p/lo-1)*100>BUY_ZONE: continue
        if not is_buyable_phase(phase_at(c,d)): continue
        r=rate_at(d); fl=(r+PREMIUM) if r else 0
        if fl>0 and dy_at(c,d,p)<fl: continue
        pos[c]={"u":UNIT/p,"cost":UNIT,"ent":d,"add":False}; cash-=UNIT
        if d.year==2024: trades2024.append(("매수",d,name[c],p,0))
    monthly[(d.year,d.month)]=tv(d)

print("##### 2024년 월별 자산 추이 #####")
for (y,m) in sorted(monthly):
    if y==2024 or (y==2023 and m==12):
        print(f"  {y}-{m:02d}: {monthly[(y,m)]:>12,.0f}원")
print()
print(f"##### 2024년 매매 ({len(trades2024)}건) #####")
for t,d,n,p,r in trades2024:
    rs=f"  수익 {r:+.1f}%" if r else ""
    print(f"  {d} {t:4} {n[:12]:12} @{p:>9,.0f}{rs}")
print()
print("##### 2024년말 보유종목 평가손익 #####")
dend=[d for d in alldays if d.year==2024][-1]
for c,ps in pos.items():
    ds=sd[c]; i=bisect.bisect_right(ds,dend)-1
    if i<0: continue
    p=prices[c][ds[i]]; avg=ps["cost"]/ps["u"]
    print(f"  {name[c][:12]:12} 평단{avg:>9,.0f} 현재{p:>9,.0f}  {(p/avg-1)*100:+6.1f}%  (진입 {ps['ent']})")
print(f"\n  현금: {cash:,.0f}원 / 보유 {len(pos)}종목")

# 코스피 비교
kp=load_px_kospi=None
kpath=Path("backtester/.lean-workspace/data/index/kospi.csv")
if kpath.exists():
    kp={}
    for line in kpath.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; kp[date(int(s[:4]),int(s[4:6]),int(s[6:8]))]=float(pt[4])
        except: continue
    k24=[(d,v) for d,v in sorted(kp.items()) if d.year==2024]
    if k24:
        print(f"\n##### 코스피 2024 #####")
        print(f"  연초 {k24[0][1]:,.0f} → 연말 {k24[-1][1]:,.0f}  ({(k24[-1][1]/k24[0][1]-1)*100:+.1f}%)")
