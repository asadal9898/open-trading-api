"""배당하한 넓게 스윕 (0~3.5%) + 매수건수·종목수 추적.
   물타기는 현행(-30% 1회) 고정. 승률 개선이 표본 축소 착시인지 확인."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}
BUY_ZONE, TARGET, STOP, AVGDOWN = 5.0, 15.0, -50.0, -30.0

def load_ohlc(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily") / f"{code}.csv"
    if not p.exists(): return []
    rows = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 6: continue
        try:
            s = parts[0][:8]
            rows.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])), float(parts[4])))
        except Exception: continue
    return sorted(rows)

def hist_rcepts(code, as_of):
    op = OP[code]["op"]; hist, rcepts = {}, {}
    for ys, qd in op.items():
        yr = int(ys)
        for q in ("Q1","Q2","Q3","Q4"):
            v = qd.get(q)
            if v is None: continue
            m, dd = PUB[q]
            rc = date(yr + (1 if q=="Q4" else 0), m, dd)
            if rc > as_of: continue
            hist.setdefault(yr, {})[q] = v; rcepts[(yr,q)] = rc
    return hist, rcepts

_pc = {}
def phase_at(code, d):
    h, r = hist_rcepts(code, d)
    key = (code, tuple(sorted(r.values())))
    if key not in _pc:
        _pc[key] = _judge_phase_from_data(h, r, d)["phase"]
    return _pc[key]

def div_yield_at(code, d, price):
    total = 0.0
    for x in (DIV.get(code, {}).get("dividends") or []):
        a = x.get("amount") or 0; s = str(x.get("date",""))
        if a <= 0 or len(s) < 8: continue
        try: dd = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except Exception: continue
        if d - timedelta(days=365) <= dd <= d: total += a
    return (total/price*100) if price else 0.0

def divs_of(code):
    out=[]
    for x in (DIV.get(code,{}).get("dividends") or []):
        a=x.get("amount") or 0; s=str(x.get("date",""))
        if a<=0 or len(s)<8: continue
        try: out.append((date(int(s[:4]),int(s[4:6]),int(s[6:8])),float(a)))
        except: pass
    return sorted(out)

data = {}
for code, info in OP.items():
    if info["style"] != "value_range": continue
    bars = load_ohlc(code)
    if len(bars) < 300: continue
    data[code] = (bars, divs_of(code), info["name"])

def simulate(code, div_floor):
    """반환: (종목수익%, 매수건수)"""
    bars, dl, name = data[code]
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0]>=cutoff), None)
    if idx0 is None: return None
    invested=proceeds=units=cost=0.0; entry=None; added=False; UNIT=100.0
    nbuy=0
    for i in range(idx0,len(bars)):
        d,p = bars[i]
        wc=[x[1] for x in bars[max(0,i-252):i+1]]
        if len(wc)<100: continue
        lo=min(wc); avg=(cost/units) if units else None
        ph=phase_at(code,d)
        if units==0:
            if (p/lo-1)*100<=BUY_ZONE and is_buyable_phase(ph):
                if div_floor>0 and div_yield_at(code,d,p)<div_floor: continue
                invested+=UNIT; cost+=UNIT; units+=UNIT/p; entry=d; added=False; nbuy+=1
            continue
        gain=(p/avg-1)*100
        if gain<=AVGDOWN and not added:
            invested+=UNIT; cost+=UNIT; units+=UNIT/p; added=True; nbuy+=1
        if gain<=STOP or gain>=TARGET:
            got=sum(a for dt,a in dl if entry<dt<=d)
            proceeds+=units*(p+got); units=cost=0.0; added=False
    if units>0:
        d,p=bars[-1]; got=sum(a for dt,a in dl if entry<dt<=d)
        proceeds+=units*(p+got)
    if invested<=0: return None
    return (proceeds/invested-1)*100, nbuy

print("##### 배당하한 스윕 (물타기 현행 고정, 5년) #####")
print(f"{'배당하한':10}{'종목':>5}{'매수건수':>8}{'평균':>9}{'중앙값':>9}{'승률':>6}")
print("-"*50)
for df in (0.0, 2.0, 3.0, 3.5, 4.0, 4.22, 4.5, 5.0):
    dfl = "필터만" if df==0 else f"{df}%↑"
    res=[(v,nb) for code in data if (rv:=simulate(code,df)) is not None for v,nb in [rv]]
    if res:
        vals=[v for v,_ in res]; tot_buy=sum(nb for _,nb in res)
        # 매수 0건 종목은 제외하고 수익 집계
        active=[v for v,nb in res if nb>0]
        print(f"{dfl:10}{len(active):>5}{tot_buy:>8}{sum(active)/len(active):>+8.1f}%"
              f"{statistics.median(active):>+8.1f}%"
              f"{sum(1 for r in active if r>0)/len(active)*100:>5.0f}%")
