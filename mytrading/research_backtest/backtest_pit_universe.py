"""동적 유니버스 백테스트 (point-in-time).
   각 매수 시점에 '그때 실제 배당 이력이 있는 종목만' 매수 → 생존편향 제거.
   비교: 고정 유니버스(기존) vs 동적 유니버스."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
import bisect
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}
BUY_ZONE, TARGET, STOP, AVGDOWN = 5.0, 15.0, -50.0, -30.0
PREMIUM = 0.0

def load_rate(path):
    r = {}
    for line in Path(path).read_text().splitlines():
        p = line.split(",")
        if len(p) < 5: continue
        try:
            s=p[0][:8]; r[date(int(s[:4]),int(s[4:6]),int(s[6:8]))]=float(p[4])
        except: continue
    return r
KTB = load_rate("backtester/.lean-workspace/data/index/ktb3y.csv")
kd = sorted(KTB.keys())
def rate_at(d):
    i=bisect.bisect_right(kd,d)-1
    return KTB[kd[i]] if i>=0 else None

def load_ohlc(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily")/f"{code}.csv"
    if not p.exists(): return []
    rows=[]
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; rows.append((date(int(s[:4]),int(s[4:6]),int(s[6:8])),float(pt[4])))
        except: continue
    return sorted(rows)

def hist_rcepts(code, as_of):
    op=OP[code]["op"]; hist={}; rc={}
    for ys,qd in op.items():
        yr=int(ys)
        for q in ("Q1","Q2","Q3","Q4"):
            v=qd.get(q)
            if v is None: continue
            m,dd=PUB[q]; r=date(yr+(1 if q=="Q4" else 0),m,dd)
            if r>as_of: continue
            hist.setdefault(yr,{})[q]=v; rc[(yr,q)]=r
    return hist,rc
_pc={}
def phase_at(code,d):
    h,r=hist_rcepts(code,d); key=(code,tuple(sorted(r.values())))
    if key not in _pc: _pc[key]=_judge_phase_from_data(h,r,d)["phase"]
    return _pc[key]

def divs_sorted(code):
    out=[]
    for x in (DIV.get(code,{}).get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: out.append((date(int(s[:4]),int(s[4:6]),int(s[6:8])),a))
        except: pass
    return sorted(out)
DIVS={c:divs_sorted(c) for c in DIV}

def div_yield_at(code,d,price):
    t=sum(a for dd,a in DIVS.get(code,[]) if d-timedelta(days=365)<=dd<=d)
    return (t/price*100) if price else 0.0

def has_dividend_history(code, d):
    """d 시점에 '이미 배당 이력이 있는가' = 첫 배당일이 d보다 앞서는가."""
    dv = DIVS.get(code, [])
    return bool(dv) and dv[0][0] <= d

def divs_between(code,d0,d1):
    return sum(a for dd,a in DIVS.get(code,[]) if d0<dd<=d1)

data={}
for code,info in OP.items():
    if info["style"]!="value_range": continue
    bars=load_ohlc(code)
    if len(bars)<300: continue
    data[code]=(bars, info["name"])

def simulate(code, dynamic):
    """dynamic=True: 매수 시점에 배당 이력 있어야 매수 (point-in-time)"""
    bars,name=data[code]
    cutoff=date.today()-timedelta(days=1825)
    idx0=next((i for i,b in enumerate(bars) if b[0]>=cutoff),None)
    if idx0 is None: return None
    inv=proc=units=cost=0.0; entry=None; added=False; UNIT=100.0
    for i in range(idx0,len(bars)):
        d,p=bars[i]
        wc=[x[1] for x in bars[max(0,i-252):i+1]]
        if len(wc)<100: continue
        lo=min(wc); avg=(cost/units) if units else None
        if units==0:
            if (p/lo-1)*100<=BUY_ZONE and is_buyable_phase(phase_at(code,d)):
                # 동적: 배당 이력 확인
                if dynamic and not has_dividend_history(code,d): continue
                r=rate_at(d); floor=(r+PREMIUM) if r else 0
                if floor>0 and div_yield_at(code,d,p)<floor: continue
                inv+=UNIT; cost+=UNIT; units+=UNIT/p; entry=d; added=False
            continue
        gain=(p/avg-1)*100
        if gain<=AVGDOWN and not added:
            inv+=UNIT; cost+=UNIT; units+=UNIT/p; added=True
        if gain<=STOP or gain>=TARGET:
            got=divs_between(code,entry,d); proc+=units*(p+got)
            units=cost=0.0; added=False
    if units>0:
        d,p=bars[-1]; got=divs_between(code,entry,d); proc+=units*(p+got)
    if inv<=0: return None
    return (proc/inv-1)*100

print("##### 고정 vs 동적 유니버스 (생존편향 제거, 국고채+0%p) #####")
print(f"{'유니버스':20}{'종목':>5}{'평균':>9}{'중앙값':>9}{'승률':>6}")
print("-"*52)
for dyn,label in [(False,"고정(기존)"),(True,"동적(PIT)")]:
    vals=[v for code in data if (v:=simulate(code,dyn)) is not None]
    if vals:
        print(f"{label:20}{len(vals):>5}{sum(vals)/len(vals):>+8.1f}%"
              f"{statistics.median(vals):>+8.1f}%"
              f"{sum(1 for r in vals if r>0)/len(vals)*100:>5.0f}%")

# 8개 후발 배당 종목이 어떻게 빠지는지
print()
print("배당 시작이 2021년 이후인 종목 (동적에서 초기 제외됨):")
for code in data:
    dv=DIVS.get(code,[])
    if dv and dv[0][0].year>=2021:
        print(f"  {data[code][1][:12]:12} 첫 배당 {dv[0][0]}")
