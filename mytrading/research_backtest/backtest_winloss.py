"""손익비 분해: 승률 89% 가 착시인지 실력인지.
   각 완결된 트레이드(익절/손절/청산)를 개별 기록 → 승자·패자 분리 분석."""
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
    p = Path("backtester/.lean-workspace/data/equity/krx/daily") / f"{code}.csv"
    if not p.exists(): return []
    rows = []
    for line in p.read_text().splitlines():
        parts = line.split(",")
        if len(parts) < 6: continue
        try:
            s = parts[0][:8]
            rows.append((date(int(s[:4]), int(s[4:6]), int(s[6:8])),
                         float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])))
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
        _pcache[key] = is_buyable_phase(_judge_phase_from_data(h, r, d)["phase"])
    return _pcache[key]

data = {}
for code, info in OP.items():
    if info["style"] != "value_range": continue
    bars = load_ohlc(code)
    if len(bars) < 300: continue
    data[code] = (bars, divs_of(code), info["name"])

# 개별 트레이드 기록 (진입~청산을 하나의 트레이드로)
trades = []   # {code, name, ret%, exit_reason, added, days}
def simulate(code):
    bars, dl, name = data[code]
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0] >= cutoff), None)
    if idx0 is None: return
    units = cost = 0.0; entry = None; added = False; UNIT = 100.0
    inv_this = 0.0
    for i in range(idx0, len(bars)):
        d, o, h, l, c = bars[i]
        win = [b[4] for b in bars[max(0,i-252):i+1]]
        if len(win) < 100: continue
        pct_low = (c/min(win) - 1) * 100
        avg = (cost/units) if units else None
        if units == 0:
            if pct_low <= BUY_ZONE and buyable_at(code, d):
                units += UNIT/c; cost += UNIT; inv_this = UNIT
                entry = d; added = False
            continue
        gain = (c/avg - 1) * 100
        if gain <= AVGDOWN and not added and buyable_at(code, d):
            units += UNIT/c; cost += UNIT; inv_this += UNIT; added = True
        reason = None
        if gain <= STOP: reason = "손절"
        elif gain >= TARGET: reason = "익절"
        if reason:
            got = sum(a for dt, a in dl if entry < dt <= d)
            proceeds = units * (c + got)
            ret = (proceeds/inv_this - 1) * 100
            trades.append({"code":code,"name":name,"ret":ret,"reason":reason,
                           "added":added,"days":(d-entry).days})
            units = cost = 0.0; added = False; inv_this = 0.0
    # 미청산 보유 → 평가청산
    if units > 0:
        d, o, h, l, c = bars[-1]
        got = sum(a for dt, a in dl if entry < dt <= d)
        proceeds = units * (c + got)
        ret = (proceeds/inv_this - 1) * 100
        trades.append({"code":code,"name":name,"ret":ret,"reason":"보유중",
                       "added":added,"days":(d-entry).days})

for code in data:
    simulate(code)

wins = [t for t in trades if t["ret"] > 0]
losses = [t for t in trades if t["ret"] <= 0]
n = len(trades)
print(f"##### 손익비 분해 (개별 트레이드 {n}건) #####")
print(f"  승률: {len(wins)/n*100:.0f}%  ({len(wins)}승 {len(losses)}패)")
print()
print(f"  평균 승자: {statistics.mean([t['ret'] for t in wins]):+.1f}%")
print(f"  평균 패자: {statistics.mean([t['ret'] for t in losses]):+.1f}%")
avgw = statistics.mean([t['ret'] for t in wins])
avgl = statistics.mean([t['ret'] for t in losses])
print(f"  손익비(승자평균/|패자평균|): {avgw/abs(avgl):.2f}")
exp = len(wins)/n*avgw + len(losses)/n*avgl
print(f"  기댓값(트레이드당): {exp:+.2f}%")
print()
# 청산 사유별
print("  청산 사유별:")
for r in ("익절","손절","보유중"):
    sub = [t for t in trades if t["reason"]==r]
    if sub:
        print(f"    {r}: {len(sub)}건  평균 {statistics.mean([t['ret'] for t in sub]):+.1f}%")
print()
# 최악/최선
srt = sorted(trades, key=lambda t:t["ret"])
print("  최악 5건:")
for t in srt[:5]:
    print(f"    {t['name'][:10]:10} {t['ret']:+7.1f}%  {t['reason']} ({t['days']}일)")
print("  최선 5건:")
for t in srt[-5:]:
    print(f"    {t['name'][:10]:10} {t['ret']:+7.1f}%  {t['reason']} ({t['days']}일)")
print()
# 물타기 효과
adds = [t for t in trades if t["added"]]
print(f"  물타기 발생: {len(adds)}건, 평균 {statistics.mean([t['ret'] for t in adds]):+.1f}%" if adds else "  물타기 없음")
