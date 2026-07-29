"""1000만원 포트폴리오 시뮬레이션 (연도별 잔고 추이).
   종목당 100만원 고정, 최대 10종목 동시보유, 회수금 재투자(복리).
   배당 기준: 국고채3년 + 0%p (최적)."""
import sys, json
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
CAPITAL = 10_000_000       # 1000만원
MIN_UNIT = 1_000_000      # 슬롯당 최소 금액 (100만원)
MAX_SLOTS = 20            # 슬롯 상한 — 이후로는 종목당 금액이 늘어남

def slots_unit(v):
    """총자산 v → (슬롯수, 종목당 금액).
       자산÷100만 만큼 슬롯을 늘리되 20개 상한. 이후엔 종목당 금액이 커진다."""
    s = max(1, min(int(v // MIN_UNIT), MAX_SLOTS))
    return s, v / s

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
    if not p.exists(): return {}
    out={}
    for line in p.read_text().splitlines():
        pt=line.split(",")
        if len(pt)<6: continue
        try:
            s=pt[0][:8]; out[date(int(s[:4]),int(s[4:6]),int(s[6:8]))]=float(pt[4])
        except: continue
    return out

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

def div_yield_at(code,d,price):
    t=0.0
    for x in (DIV.get(code,{}).get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: dd=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        if _bad(code,dd,a): continue
        if d-timedelta(days=365)<=dd<=d: t+=a
    return (t/price*100) if price else 0.0

MAX_YIELD=15.0
def _bad(code, dd, a):
    ds=sorted_dates.get(code) or []
    if not ds: return False
    i=bisect.bisect_left(ds,dd)-1
    if 0<=i<len(ds):
        p=prices[code][ds[i]]
        if p>0 and a/p*100>MAX_YIELD: return True
    return False

def divs_between(code, d0, d1):
    t=0.0
    for x in (DIV.get(code,{}).get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: dd=date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
        if _bad(code,dd,a): continue
        if d0<dd<=d1: t+=a
    return t

# 데이터 준비
prices={}; lowwin={}
codes=[]
for code,info in OP.items():
    if info["style"]!="value_range": continue
    px=load_ohlc(code)
    if len(px)<300: continue
    prices[code]=px; codes.append(code)

# 전체 거래일 (모든 종목 합집합)
alldays=sorted(set().union(*[set(prices[c].keys()) for c in codes]))
start=date.today()-timedelta(days=1825)
alldays=[d for d in alldays if d>=start]

# 52주 저점 계산용: 각 종목 정렬된 날짜
sorted_dates={c:sorted(prices[c].keys()) for c in codes}
def low52(code,d):
    ds=sorted_dates[code]
    i=bisect.bisect_right(ds,d)
    win=[prices[code][x] for x in ds[max(0,i-252):i]]
    return min(win) if len(win)>=100 else None

# 포트폴리오 상태
cash=CAPITAL
positions={}   # code -> {units, cost, entry, added}
yearly={}
_t22=[]
_stat=[]      # year -> 연말 총자산

def total_value(d):
    v=cash
    for code,pos in positions.items():
        ds=sorted_dates[code]; i=bisect.bisect_right(ds,d)-1
        if i<0: continue
        p=prices[code][ds[i]]
        v+=pos["units"]*p
    return v

for d in alldays:
    # 1) 보유 종목 청산 체크 (익절/손절)
    for code in list(positions.keys()):
        if d not in prices[code]: continue
        p=prices[code][d]; pos=positions[code]
        avg=pos["cost"]/pos["units"]
        gain=(p/avg-1)*100
        # 물타기
        _s,_u = slots_unit(total_value(d))
        if gain<=AVGDOWN and not pos["added"] and cash>=_u:
            pos["units"]+=_u/p; pos["cost"]+=_u; pos["added"]=True; cash-=_u
        if gain<=STOP or gain>=TARGET:
            got=divs_between(code,pos["entry"],d)
            cash+=pos["units"]*(p+got)
            del positions[code]
    # 2) 신규 매수 체크
    for code in codes:
        if code in positions: continue
        _s,_u = slots_unit(total_value(d))
        if len(positions)>=_s: break
        if cash<_u: break
        if d not in prices[code]: continue
        p=prices[code][d]; lo=low52(code,d)
        if lo is None: continue
        if (p/lo-1)*100<=BUY_ZONE and is_buyable_phase(phase_at(code,d)):
            r=rate_at(d); floor=(r+PREMIUM) if r else 0
            if floor>0 and div_yield_at(code,d,p)<floor: continue
            positions[code]={"units":_u/p,"cost":_u,"entry":d,"added":False}
            cash-=_u
    # 연말 스냅샷
    yearly[d.year]=total_value(d)
    if d.year==2022:
        _t22.append((d, len(positions), cash, total_value(d)))
    _v=total_value(d); _stat.append((len(positions), slots_unit(_v)[0], cash/_v*100 if _v else 0))

print("##### 1000만원 포트폴리오 (종목당 100만원, 최대 10종목, 복리) #####")
print(f"배당기준 국고채+0%p / 기간 {alldays[0]} ~ {alldays[-1]}")
print()
print(f"{'연도':6}{'연말자산':>15}{'연수익률':>10}{'누적수익률':>12}")
print("-"*45)
prev=CAPITAL
for y in sorted(yearly):
    val=yearly[y]
    yr_ret=(val/prev-1)*100
    cum_ret=(val/CAPITAL-1)*100
    print(f"{y:<6}{val:>14,.0f}원{yr_ret:>+9.1f}%{cum_ret:>+11.1f}%")
    prev=val
final=yearly[max(yearly)]
n_years=(alldays[-1]-alldays[0]).days/365.25
cagr=((final/CAPITAL)**(1/n_years)-1)*100
print("-"*45)
print(f"최종자산: {final:,.0f}원  (원금 {CAPITAL:,}원)")
print(f"총수익: {final-CAPITAL:+,.0f}원  ({(final/CAPITAL-1)*100:+.1f}%)")
print(f"연평균(CAGR): {cagr:+.1f}%  /  기간 {n_years:.1f}년")
print()
print(f"현재 미청산 보유: {len(positions)}종목")

import statistics as _st
print()
print("##### 슬롯 활용 #####")
print(f"  평균 보유종목: {_st.mean([a for a,_,_ in _stat]):.1f}개")
print(f"  평균 슬롯수  : {_st.mean([b for _,b,_ in _stat]):.1f}개 (상한 20)")
print(f"  평균 현금비중: {_st.mean([c for _,_,c in _stat]):.1f}%")

print()
print("##### 2022년 월별 #####")
seen=set()
for d,n,c,v in _t22:
    k=(d.year,d.month)
    if k in seen: continue
    seen.add(k)
    print(f"  {d}: 자산 {v:>12,.0f}  보유 {n:>2}종목  현금 {c/v*100:>5.1f}%")
