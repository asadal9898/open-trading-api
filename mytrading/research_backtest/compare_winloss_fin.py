"""손실 종목 vs 승자 종목 — 매수 시점 재무 신호 대조.
   점수화 강화로 손실을 사전에 걸러낼 수 있었는지 판정."""
import sys, json, statistics
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path.cwd()))

from mytrading.finance_data import _judge_phase_from_data, is_buyable_phase

OP = json.loads((Path.home() / "op_history.json").read_text())
DIV = json.loads((Path.home() / "div_history_5y.json").read_text())
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}
BUY_ZONE = 5.0

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
            rc = date(yr + (1 if q == "Q4" else 0), m, dd)
            if rc > as_of: continue
            hist.setdefault(yr, {})[q] = v
            rcepts[(yr, q)] = rc
    return hist, rcepts

def div_yield_at(code, d, price):
    """매수 시점 직전 1년 배당 합 / 매수가 → 시가배당률(%)."""
    divs = DIV.get(code, {}).get("dividends") or []
    total = 0.0
    for x in divs:
        a = x.get("amount") or 0; s = str(x.get("date",""))
        if a <= 0 or len(s) < 8: continue
        try: dd = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except Exception: continue
        if d - timedelta(days=365) <= dd <= d:
            total += a
    return (total/price*100) if price else 0.0

def op_trend(code, as_of):
    """매수 시점 기준, 직전 4분기 영업이익 추세 (증가/감소/적자)."""
    h, r = hist_rcepts(code, as_of)
    if not r: return "데이터없음", []
    # 공시순 정렬 → 최근 4개
    seq = sorted(r.items(), key=lambda kv: kv[1])[-4:]
    vals = [h[y][q] for (y,q),_ in seq]
    if not vals: return "데이터없음", []
    neg = sum(1 for v in vals if v < 0)
    trend = "적자포함" if neg else ("증가추세" if vals[-1] > vals[0] else "감소추세")
    return trend, vals

# 각 종목의 첫 매수 시점 찾기 (5년 백테스트 구간)
def first_buy(code):
    bars = load_ohlc(code)
    if len(bars) < 300: return None
    cutoff = date.today() - timedelta(days=1825)
    idx0 = next((i for i,b in enumerate(bars) if b[0] >= cutoff), None)
    if idx0 is None: return None
    for i in range(idx0, len(bars)):
        d, p = bars[i]
        win = [b[1] for b in bars[max(0,i-252):i+1]]
        if len(win) < 100: continue
        if (p/min(win) - 1)*100 <= BUY_ZONE:
            return d, p
    return None

GROUPS = {
    "손실": [("069080","웹젠"), ("215000","골프존"), ("097520","엠씨넥스"),
             ("873260","KG파이낸셜"), ("016380","KG스틸")],
    "우량승자": [("900290","CR홀딩스"), ("001230","동국홀딩스"),
               ("073570","우리손에프앤지")],
}

# 종목코드 확인 — op_history 에 있는 코드로 매핑
name2code = {}
for c, info in OP.items():
    name2code[info["name"]] = c

for label, lst in GROUPS.items():
    print(f"\n{'='*60}")
    print(f"【 {label} 】")
    print(f"{'='*60}")
    for _, name in lst:
        code = name2code.get(name)
        if not code:
            # 부분 매칭
            for nm, c in name2code.items():
                if name[:4] in nm:
                    code = c; name = nm; break
        if not code:
            print(f"  {name}: op_history 에 없음 (스킵)")
            continue
        fb = first_buy(code)
        if not fb:
            print(f"  {name}: 매수 시점 못 찾음")
            continue
        d, p = fb
        h, r = hist_rcepts(code, d)
        ph = _judge_phase_from_data(h, r, d)
        trend, vals = op_trend(code, d)
        dy = div_yield_at(code, d, p)
        print(f"\n  ● {name} ({code}) — 첫 매수 {d}")
        print(f"     국면: {ph['phase']}  ({ph['note']})")
        print(f"     영업이익 추세: {trend}  최근값 {[round(v,0) for v in vals]}")
        print(f"     매수시점 배당률: {dy:.1f}%")
