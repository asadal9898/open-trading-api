"""국고채+0.5%p(실전) vs 국고채+0%p(최적) — 종목별 수익률·매수건수 상세."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}
BUY_ZONE, TARGET, STOP, AVGDOWN = 5.0, 15.0, -50.0, -30.0

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
import bisect
def rate_at(d):
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

def simulate(code, premium):
    """반환: (수익률%, 매수건수, 익절, 손절, 보유중여부)"""
    bars, dl, name = data[code]
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0]>=cutoff), None)
    if idx0 is None: return None
    invested=proceeds=units=cost=0.0; entry=None; added=False; UNIT=100.0
    nbuy=win_exit=loss_exit=0; holding=False
    for i in range(idx0,len(bars)):
        d,p = bars[i]
        wc=[x[1] for x in bars[max(0,i-252):i+1]]
        if len(wc)<100: continue
        lo=min(wc); avg=(cost/units) if units else None
        ph=phase_at(code,d)
        if units==0:
            if (p/lo-1)*100<=BUY_ZONE and is_buyable_phase(ph):
                r = rate_at(d); floor = (r+premium) if r is not None else 0
                if floor>0 and div_yield_at(code,d,p) < floor: continue
                invested+=UNIT; cost+=UNIT; units+=UNIT/p; entry=d; added=False; nbuy+=1
            continue
        gain=(p/avg-1)*100
        if gain<=AVGDOWN and not added:
            invested+=UNIT; cost+=UNIT; units+=UNIT/p; added=True; nbuy+=1
        if gain<=STOP or gain>=TARGET:
            got=sum(a for dt,a in dl if entry<dt<=d)
            proceeds+=units*(p+got)
            if gain>=TARGET: win_exit+=1
            else: loss_exit+=1
            units=cost=0.0; added=False
    if units>0:
        d,p=bars[-1]; got=sum(a for dt,a in dl if entry<dt<=d)
        proceeds+=units*(p+got); holding=True
    if invested<=0: return None
    ret=(proceeds/invested-1)*100
    return (ret, nbuy, win_exit, loss_exit, holding)

# 종목별 상세
print("="*78)
print(f"{'종목':14}{'수익(0.5%)':>11}{'수익(0%)':>10}{'매수':>5}{'익절':>5}{'손절':>5}{'보유':>5}")
print("="*78)
rows=[]
for code in data:
    r05 = simulate(code, 0.5)
    r00 = simulate(code, 0.0)
    if r00 is None: continue
    ret05 = r05[0] if r05 else None
    ret00, nbuy, we, le, hold = r00
    rows.append((data[code][2], ret05, ret00, nbuy, we, le, hold))
# 0% 수익률 기준 정렬
rows.sort(key=lambda x: x[2], reverse=True)
for name, r05, r00, nbuy, we, le, hold in rows:
    s05 = f"{r05:+.1f}%" if r05 is not None else "  배제"
    hs = "보유" if hold else ""
    print(f"{name[:14]:14}{s05:>11}{r00:>+9.1f}%{nbuy:>5}{we:>5}{le:>5}{hs:>5}")

# 요약
print("="*78)
for prem, lbl in [(0.5,"국고채+0.5%p (실전)"), (0.0,"국고채+0%p (최적)")]:
    vals=[]; tot_buy=0
    for code in data:
        r=simulate(code,prem)
        if r: vals.append(r[0]); tot_buy+=r[1]
    print(f"{lbl:24} 종목 {len(vals)}  총매수 {tot_buy}건  "
          f"평균 {sum(vals)/len(vals):+.1f}%  중앙값 {statistics.median(vals):+.1f}%  "
          f"승률 {sum(1 for v in vals if v>0)/len(vals)*100:.0f}%")
