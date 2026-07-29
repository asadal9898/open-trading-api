"""체결 현실성 검증:
   1. "종가 < 시가" 경향이 실제로 있는가 (한국주식 관찰 검증)
   2. 매수 판단 시점: 당일종가 vs 전일종가
   3. 체결 가격: 당일종가 / 다음날시가 / 다음날종가
   → 승률 86% 가 실전 체결에서도 버티는지"""
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

def load_ohlc(code):
    """반환: [(date, open, high, low, close), ...]"""
    p = Path("backtester/.lean-workspace/data/equity/krx/daily") / f"{code}.csv"
    if not p.exists(): return []
    rows = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 6: continue
        try:
            s = parts[0][:8]
            rows.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])),
                         float(parts[1]), float(parts[2]),
                         float(parts[3]), float(parts[4])))
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
            rc = date(yr + (1 if q == "Q4" else 0), m, dd)
            if rc > as_of: continue
            hist.setdefault(yr, {})[q] = v
            rcepts[(yr, q)] = rc
    return hist, rcepts

_pcache = {}
def buyable_at(code, d):
    h, r = hist_rcepts(code, d)
    key = (code, tuple(sorted(r.values())))
    if key not in _pcache:
        res = _judge_phase_from_data(h, r, d)
        _pcache[key] = is_buyable_phase(res["phase"])
    return _pcache[key]

data = {}
for code, info in OP.items():
    if info["style"] != "value_range": continue
    bars = load_ohlc(code)
    if len(bars) < 300: continue
    data[code] = (bars, divs_of(code), info["name"])

# --- 검증 1: "종가 < 시가" 경향 ---
print("##### 1. 종가 vs 시가 경향 (전체 거래일) #####")
allc = alld = 0; oc_diffs = []
for code, (bars, _, _) in data.items():
    for d, o, h, l, c in bars:
        if o > 0:
            oc_diffs.append((c/o - 1) * 100)
            allc += 1
            if c < o: alld += 1
print(f"  전체 {allc:,}일 중 종가<시가: {alld:,}일 ({alld/allc*100:.1f}%)")
print(f"  (종가/시가-1) 평균: {statistics.mean(oc_diffs):+.3f}%  중앙값: {statistics.median(oc_diffs):+.3f}%")
print()

# --- 검증 2+3: 체결 방식별 백테스트 ---
def simulate(code, judge_mode, fill_mode):
    """judge_mode: 'today'(당일종가로 판단) / 'prev'(전일종가로 판단)
       fill_mode:  'close'(당일종가) / 'next_open'(다음날시가) / 'next_close'(다음날종가)"""
    bars, dl, name = data[code]
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0] >= cutoff), None)
    if idx0 is None or idx0 < 1: return None
    invested = proceeds = units = cost = 0.0
    entry = None; added = False; UNIT = 100.0

    def buy_price(i):
        if fill_mode == "close":      return bars[i][4]
        if fill_mode == "next_open":  return bars[i+1][1] if i+1 < len(bars) else None
        if fill_mode == "next_close": return bars[i+1][4] if i+1 < len(bars) else None

    for i in range(idx0, len(bars)):
        d, o, h, l, c = bars[i]
        # 판단 시점 가격
        if judge_mode == "today":
            jp = c; ji = i
        else:  # prev — 전일 종가로 판단
            jp = bars[i-1][4]; ji = i-1
        win = [b[4] for b in bars[max(0,ji-252):ji+1]]
        if len(win) < 100: continue
        pct_low = (jp/min(win) - 1) * 100
        avg = (cost/units) if units else None

        if units == 0:
            if pct_low <= BUY_ZONE and buyable_at(code, d):
                bp = buy_price(i)
                if bp is None or bp <= 0: continue
                invested += UNIT; cost += UNIT; units += UNIT/bp
                entry = d; added = False
            continue
        gain = (c/avg - 1) * 100
        if gain <= AVGDOWN and not added and buyable_at(code, d):
            bp = buy_price(i)
            if bp and bp > 0:
                invested += UNIT; cost += UNIT; units += UNIT/bp; added = True
        if gain <= STOP or gain >= TARGET:
            got = sum(a for dt, a in dl if entry < dt <= d)
            proceeds += units * (c + got)
            units = cost = 0.0; added = False
    if units > 0:
        d, o, h, l, c = bars[-1]
        got = sum(a for dt, a in dl if entry < dt <= d)
        proceeds += units * (c + got)
    if invested <= 0: return None
    return (proceeds/invested - 1) * 100

print("##### 2+3. 체결 방식별 성과 (5년, value_range) #####")
print(f"{'판단/체결':28} {'종목':>4} {'평균':>9} {'중앙값':>9} {'승률':>6}")
print("-" * 62)
combos = [
    ("today", "close",      "당일종가판단/당일종가체결 ★기존"),
    ("prev",  "next_open",  "전일종가판단/다음날시가체결 ◎실전"),
    ("prev",  "next_close", "전일종가판단/다음날종가체결"),
    ("today", "next_open",  "당일종가판단/다음날시가체결"),
]
for jm, fm, label in combos:
    vals = [v for code in data if (v := simulate(code, jm, fm)) is not None]
    if vals:
        print(f"{label:28} {len(vals):>4} {sum(vals)/len(vals):>+8.1f}% "
              f"{statistics.median(vals):>+8.1f}% "
              f"{sum(1 for r in vals if r>0)/len(vals)*100:>5.0f}%")
