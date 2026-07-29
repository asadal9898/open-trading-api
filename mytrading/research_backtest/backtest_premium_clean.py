"""오염 제거 상태로 배당 프리미엄 재스윕 — 국고채+0%p 가 여전히 최적인가."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
import bisect
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home()/"op_history.json").read_text())
DIV = json.loads((Path.home()/"div_history_5y.json").read_text())
PUB = {"Q1":(5,15),"Q2":(8,15),"Q3":(11,15),"Q4":(3,15)}
BUY_ZONE,TARGET,STOP,AVGDOWN = 5.0,15.0,-50.0,-30.0
MAX_YIELD = 15.0

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

def load_ohlc(code):
    p=Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return []
    rows=[]
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; rows.append((date(int(s[:4]),int(s[4:6]),int(s[6:8])),float(pt[4])))
        except: continue
    return sorted(rows)

PX={c:load_ohlc(c) for c in OP if OP[c]["style"]=="value_range"}
PX={c:v for c,v in PX.items() if len(v)>=300}

DL={}
for code in PX:
    bars=PX[code]; ds=[b[0] for b in bars]; pxm={b[0]:b[1] for b in bars}
    out=[]
    for x in (DIV.get(code,{}).get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: dd=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        i=bisect.bisect_left(ds,dd)-1
        if 0<=i<len(ds) and pxm[ds[i]]>0 and a/pxm[ds[i]]*100>MAX_YIELD: continue
        out.append((dd,a))
    DL[code]=sorted(out)

def hist_rcepts(code,as_of):
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
def phase_at(code,d):
    h,r=hist_rcepts(code,d); k=(code,tuple(sorted(r.values())))
    if k not in _pc: _pc[k]=_judge_phase_from_data(h,r,d)["phase"]
    return _pc[k]

def simulate(code, premium, use_filter=True):
    bars=PX[code]; dl=DL[code]
    cutoff=date.today()-timedelta(days=1825)
    idx0=next((i for i,b in enumerate(bars) if b[0]>=cutoff),None)
    if idx0 is None: return None
    inv=proc=units=cost=0.0; entry=None; added=False; UNIT=100.0; nbuy=0
    for i in range(idx0,len(bars)):
        d,p=bars[i]
        wc=[x[1] for x in bars[max(0,i-252):i+1]]
        if len(wc)<100: continue
        lo=min(wc); avg=(cost/units) if units else None
        if units==0:
            if (p/lo-1)*100<=BUY_ZONE and is_buyable_phase(phase_at(code,d)):
                if use_filter:
                    dy=sum(a for dd,a in dl if d-timedelta(days=365)<=dd<=d)/p*100
                    r=rate_at(d); floor=(r+premium) if r else 0
                    if floor>0 and dy<floor: continue
                inv+=UNIT; cost+=UNIT; units+=UNIT/p; entry=d; added=False; nbuy+=1
            continue
        gain=(p/avg-1)*100
        if gain<=AVGDOWN and not added:
            inv+=UNIT; cost+=UNIT; units+=UNIT/p; added=True; nbuy+=1
        if gain<=STOP or gain>=TARGET:
            got=sum(a for dd,a in dl if entry<dd<=d)
            proc+=units*(p+got); units=cost=0.0; added=False
    if units>0:
        d,p=bars[-1]; got=sum(a for dd,a in dl if entry<dd<=d)
        proc+=units*(p+got)
    if inv<=0: return None
    return (proc/inv-1)*100, nbuy

print("##### 배당 프리미엄 재스윕 (오염 제거, 5년) #####")
print(f"{'기준':26}{'종목':>5}{'매수':>6}{'평균':>9}{'중앙값':>9}{'승률':>6}")
print("-"*62)
res=[(None,"필터만 (배당 무시)")]+[(x,f"국고채{x:+.1f}%p") for x in (-0.5,0.0,0.5,1.0,1.5)]
for prem,lbl in res:
    vals=[];tb=0
    for c in PX:
        r=simulate(c,prem if prem is not None else 0.0, use_filter=(prem is not None))
        if r: vals.append(r[0]); tb+=r[1]
    if vals:
        star=" ★실전" if prem==0.5 else (" ◎" if prem==0.0 else "")
        print(f"{lbl+star:26}{len(vals):>5}{tb:>6}{sum(vals)/len(vals):>+8.1f}%"
              f"{statistics.median(vals):>+8.1f}%"
              f"{sum(1 for v in vals if v>0)/len(vals)*100:>5.0f}%")
