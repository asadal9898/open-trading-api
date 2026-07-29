"""손실로 끝난 '그 트레이드'의 진입 시점 + 그 시점 배당률 정밀 추적.
   첫 매수가 아니라, 실제 손실 트레이드의 entry 를 잡는다."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))
from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}
BUY_ZONE, TARGET, STOP, AVGDOWN = 5.0, 15.0, -50.0, -30.0
name2code = {info["name"]: c for c, info in OP.items()}

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

def buyable_at(code, d):
    h, r = hist_rcepts(code, d)
    return is_buyable_phase(_judge_phase_from_data(h, r, d)["phase"])

def div_yield_at(code, d, price):
    divs = DIV.get(code, {}).get("dividends") or []
    total = 0.0
    for x in divs:
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

def trace(code, name):
    bars = load_ohlc(code)
    if len(bars) < 300: return
    dl = divs_of(code)
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0]>=cutoff), None)
    if idx0 is None: return
    units=cost=0.0; entry=None; added=False; inv=0.0; UNIT=100.0
    print(f"\n{'='*55}\n● {name} ({code}) — 모든 트레이드")
    for i in range(idx0,len(bars)):
        d,p = bars[i]
        win=[b[1] for b in bars[max(0,i-252):i+1]]
        if len(win)<100: continue
        avg=(cost/units) if units else None
        if units==0:
            if (p/min(win)-1)*100<=BUY_ZONE and buyable_at(code,d):
                units+=UNIT/p; cost+=UNIT; inv=UNIT; entry=d; added=False
                dy=div_yield_at(code,d,p)
                print(f"  진입 {d} @ {p:,.0f}원  배당률 {dy:.1f}%")
            continue
        gain=(p/avg-1)*100
        if gain<=AVGDOWN and not added and buyable_at(code,d):
            units+=UNIT/p; cost+=UNIT; inv+=UNIT; added=True
            print(f"    물타기 {d} @ {p:,.0f}원 (평단 대비 {gain:.0f}%)")
        r=None
        if gain<=STOP: r="손절"
        elif gain>=TARGET: r="익절"
        if r:
            got=sum(a for dt,a in dl if entry<dt<=d)
            ret=(units*(p+got)/inv-1)*100
            print(f"  → {r} {d} @ {p:,.0f}원  수익 {ret:+.1f}%")
            units=cost=0.0; added=False; inv=0.0
    if units>0:
        d,p=bars[-1]; got=sum(a for dt,a in dl if entry<dt<=d)
        ret=(units*(p+got)/inv-1)*100
        print(f"  → 보유중 (종료시점 {d} @ {p:,.0f}원)  평가수익 {ret:+.1f}%")

for name in ["웹젠","골프존","엠씨넥스","KG파이낸셜","KG스틸"]:
    code=name2code.get(name)
    if not code:
        for nm,c in name2code.items():
            if name[:3] in nm: code,name=c,nm; break
    if code: trace(code,name)
