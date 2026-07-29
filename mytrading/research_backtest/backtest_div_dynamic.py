"""검증 B: 실전 배당 기준(국고채3년 + 프리미엄) 정확 재현.
   각 매수 시점의 실제 국고채 금리로 동적 하한 → 실전이 뭘 걸렀는지 5년 재현.
   비교군: 고정하한(참고) vs 국고채+0.5%p(실전 코스피) vs +0%p(완화) vs 국고채만."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}
BUY_ZONE, TARGET, STOP, AVGDOWN = 5.0, 15.0, -50.0, -30.0

# 국고채 3년물 시계열 로드 (date -> 수익률%)
def load_rate(path):
    r = {}
    for line in Path(path).read_text().splitlines():
        p = line.split(",")
        if len(p) < 5: continue
        try:
            s = p[0][:8]
            r[date(int(s[:4]),int(s[4:6]),int(s[6:8]))] = float(p[4])
        except: continue
    return r
KTB = load_rate("backtester/.lean-workspace/data/index/ktb3y.csv")
ktb_dates = sorted(KTB.keys())

def rate_at(d):
    """d 시점 유효한 국고채 금리 (그날 없으면 직전 영업일)."""
    import bisect
    i = bisect.bisect_right(ktb_dates, d) - 1
    return KTB[ktb_dates[i]] if i >= 0 else None

def load_ohlc(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily") / f"{code}.csv"
    if not p.exists(): return []
    rows = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 6: continue
        try:
            s = parts[0][:8]
            rows.append((date(int(s[:4]),int(s[4:6]),int(s[6:8])), float(parts[4])))
        except: continue
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
        if a<=0 or len(s)<8: continue
        try: dd = date(int(s[:4]),int(s[4:6]),int(s[6:8]))
        except: continue
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

def simulate(code, mode, premium=0.0, fixed=0.0):
    """mode: 'none'(필터만) / 'fixed'(고정 하한) / 'dynamic'(국고채+premium)"""
    bars, dl, name = data[code]
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0]>=cutoff), None)
    if idx0 is None: return None
    invested=proceeds=units=cost=0.0; entry=None; added=False; UNIT=100.0
    for i in range(idx0,len(bars)):
        d,p = bars[i]
        wc=[x[1] for x in bars[max(0,i-252):i+1]]
        if len(wc)<100: continue
        lo=min(wc); avg=(cost/units) if units else None
        ph=phase_at(code,d)
        if units==0:
            if (p/lo-1)*100<=BUY_ZONE and is_buyable_phase(ph):
                floor = 0.0
                if mode=="fixed": floor = fixed
                elif mode=="dynamic":
                    r = rate_at(d)
                    floor = (r + premium) if r is not None else fixed
                if floor>0 and div_yield_at(code,d,p) < floor:
                    continue
                invested+=UNIT; cost+=UNIT; units+=UNIT/p; entry=d; added=False
            continue
        gain=(p/avg-1)*100
        if gain<=AVGDOWN and not added:
            invested+=UNIT; cost+=UNIT; units+=UNIT/p; added=True
        if gain<=STOP or gain>=TARGET:
            got=sum(a for dt,a in dl if entry<dt<=d)
            proceeds+=units*(p+got); units=cost=0.0; added=False
    if units>0:
        d,p=bars[-1]; got=sum(a for dt,a in dl if entry<dt<=d)
        proceeds+=units*(p+got)
    if invested<=0: return None
    return (proceeds/invested-1)*100

print("##### 검증 B: 실전 배당 기준 재현 (동적 국고채, 5년) #####")
print(f"평균 국고채 3년물(2021~26): {statistics.mean([KTB[d] for d in ktb_dates if d.year>=2021]):.2f}%")
print()
print(f"{'배당 기준':32}{'종목':>4}{'평균':>8}{'중앙값':>8}{'승률':>6}")
print("-"*60)
configs = [
    ("none",   0.0, 0.0, "필터만 (배당 무시)"),
    ("dynamic",1.0, 0.0, "국고채+1.0%p (코스닥 실전)"),
    ("dynamic",0.5, 0.0, "국고채+0.5%p (코스피 실전) ★"),
    ("dynamic",0.0, 0.0, "국고채+0%p (완화안1)"),
    ("dynamic",-0.5,0.0, "국고채−0.5%p (완화안2)"),
    ("fixed",  0.0, 2.0, "고정 2.0% (오늘 발견 최적)"),
]
for mode, prem, fx, label in configs:
    vals=[v for code in data if (v:=simulate(code,mode,prem,fx)) is not None]
    if vals:
        print(f"{label:32}{len(vals):>4}{sum(vals)/len(vals):>+7.1f}%"
              f"{statistics.median(vals):>+7.1f}%"
              f"{sum(1 for r in vals if r>0)/len(vals)*100:>5.0f}%")
