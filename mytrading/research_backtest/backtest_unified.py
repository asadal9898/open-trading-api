"""통일 백테스트: 매수 국면 판정을 공유 로직(_judge_phase_from_data + is_buyable_phase)으로.
   기존 자체 로직(build_disclosed/phase_at) 결과와 비교 → 승률 83% 유지 확인."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))

from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())

BUY_ZONE, TARGET, STOP, AVGDOWN = 5.0, 15.0, -50.0, -30.0
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}

def divs_of(code):
    out = []
    for x in (DIV.get(code, {}).get("dividends") or []):
        a = x.get("amount") or 0; s = str(x.get("date", ""))
        if a <= 0 or len(s) < 8: continue
        try: out.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])), float(a)))
        except Exception: pass
    return sorted(out)

def load_closes(code):
    p = Path("backtester/.lean-workspace/data/equity/krx/daily") / f"{code}.csv"
    if not p.exists(): return []
    rows = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 5: continue
        try:
            s = parts[0][:8]
            rows.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])), float(parts[4])))
        except Exception: continue
    return sorted(rows)

def hist_rcepts(code, as_of):
    """op_history + PUB → hist/rcepts (as_of 시점 공시분만)."""
    op = OP[code]["op"]; hist, rcepts = {}, {}
    for ys, qd in op.items():
        yr = int(ys)
        for q in ("Q1","Q2","Q3","Q4"):
            v = qd.get(q)
            if v is None: continue
            m, dd = PUB[q]
            rc = date(yr + (1 if q == "Q4" else 0), m, dd)
            if rc > as_of: continue
            hist.setdefault(yr, {})[q] = v
            rcepts[(yr, q)] = rc
    return hist, rcepts

# 국면 캐시 (같은 code+분기공시상태면 동일 → 날짜별 재계산 줄임)
_pcache = {}
def buyable_at(code, d):
    h, r = hist_rcepts(code, d)
    key = (code, tuple(sorted(r.values())))   # 공시된 분기 집합이 같으면 국면 동일
    if key not in _pcache:
        res = _judge_phase_from_data(h, r, d)
        _pcache[key] = is_buyable_phase(res["phase"])
    return _pcache[key]

data = {}
for code, info in OP.items():
    if info["style"] != "value_range": continue
    closes = load_closes(code)
    if len(closes) < 300: continue
    data[code] = (closes, divs_of(code), info["name"])

def simulate(code):
    closes, dl, name = data[code]
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,(d,_) in enumerate(closes) if d >= cutoff), None)
    if idx0 is None: return None
    invested = proceeds = units = cost = 0.0
    entry = None; added = False; UNIT = 100.0
    for i in range(idx0, len(closes)):
        d, p = closes[i]
        win = [c for _,c in closes[max(0,i-252):i+1]]
        if len(win) < 100: continue
        pct_low = (p/min(win) - 1) * 100
        avg = (cost/units) if units else None
        if units == 0:
            if pct_low <= BUY_ZONE and buyable_at(code, d):   # ← 공유 로직
                invested += UNIT; cost += UNIT; units += UNIT/p
                entry = d; added = False
            continue
        gain = (p/avg - 1) * 100
        if gain <= AVGDOWN and not added and buyable_at(code, d):
            invested += UNIT; cost += UNIT; units += UNIT/p; added = True
        if gain <= STOP or gain >= TARGET:
            got = sum(a for dt, a in dl if entry < dt <= d)
            proceeds += units * (p + got)
            units = cost = 0.0; added = False
    if units > 0:
        d, p = closes[-1]
        got = sum(a for dt, a in dl if entry < dt <= d)
        proceeds += units * (p + got)
    if invested <= 0: return None
    return (proceeds/invested - 1) * 100

vals = [v for code in data if (v := simulate(code)) is not None]
print("##### 통일 백테스트 (공유 로직, 5년, value_range) #####")
print(f"  종목: {len(vals)}")
print(f"  평균: {sum(vals)/len(vals):+.1f}%")
print(f"  중앙값: {statistics.median(vals):+.1f}%")
print(f"  승률: {sum(1 for r in vals if r>0)/len(vals)*100:.0f}%")
print()
print("  (기존 자체 로직 기준값: 평균 +13.5% / 중앙값 +15.3% / 승률 83% / 36종목)")
