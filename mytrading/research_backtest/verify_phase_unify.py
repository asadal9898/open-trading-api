"""통일 검증: 백테스트 자체 로직(build_disclosed/phase_at) vs 공유 로직(_judge_phase_from_data)
   두 방식이 같은 국면을 내는지 종목·시점별로 대조."""
import sys, json
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path.cwd()))

from mytrading.finance_data import _judge_phase_from_data

OP = json.loads((Path.home() / "op_history.json").read_text())

# --- 기존 백테스트 로직 (timesfm_risk2.py 에서 그대로 복사) ---
BIG, GROWTH_AVG = 25.0, 20.0
PUB = {"Q1": (5,15), "Q2": (8,15), "Q3": (11,15), "Q4": (3,15)}

def build_disclosed(code):
    op = OP[code]["op"]; out = []
    for year in range(2021, 2026):
        for q in ("Q1","Q2","Q3","Q4"):
            cur = op.get(str(year), {}).get(q)
            prev = op.get(str(year-1), {}).get(q)
            if cur is None or prev is None or prev == 0: continue
            yoy = (cur/prev - 1) * 100
            hist = [op[str(y)][q] for y in range(year-5, year)
                    if str(y) in op and op[str(y)].get(q) is not None]
            if len(hist) < 2: continue
            avg = sum(hist)/len(hist)
            vs = (cur/avg - 1) * 100 if avg else 0
            ph = ("평범" if abs(yoy) < BIG else
                  ("성장" if vs >= GROWTH_AVG else "턴어라운드") if yoy >= BIG else
                  ("정점통과" if vs > 0 else "침체"))
            m, dd = PUB[q]
            out.append((date(year+(1 if q=="Q4" else 0), m, dd), ph))
    return sorted(out)

def phase_at(disc, d):
    ph = "평범"
    for pub, p in disc:
        if pub <= d: ph = p
    return ph

# --- 공유 로직용 어댑터: op_history + PUB → hist/rcepts (as_of 시점) ---
def to_hist_rcepts(code, as_of):
    op = OP[code]["op"]
    hist, rcepts = {}, {}
    for ys, qd in op.items():
        yr = int(ys)
        for q in ("Q1","Q2","Q3","Q4"):
            v = qd.get(q)
            if v is None: continue
            m, dd = PUB[q]
            rc = date(yr + (1 if q == "Q4" else 0), m, dd)
            if rc > as_of: continue          # 룩어헤드 차단 (기존과 동일 기준)
            hist.setdefault(yr, {})[q] = v
            rcepts[(yr, q)] = rc
    return hist, rcepts

# --- 대조 ---
test_dates = [date(y, m, 20) for y in range(2021, 2026) for m in (2,5,8,11)]
codes = [c for c, info in OP.items() if info.get("style") == "value_range"]

total = mismatch = 0
examples = []
for code in codes:
    disc = build_disclosed(code)
    for d in test_dates:
        old = phase_at(disc, d)
        h, r = to_hist_rcepts(code, d)
        res = _judge_phase_from_data(h, r, d)
        new = res["phase"] if res["phase"] is not None else "평범"  # 판정불가→평범(기존 기본값)
        total += 1
        if old != new:
            mismatch += 1
            if len(examples) < 12:
                examples.append(f"  {OP[code]['name'][:10]:10} {d}: 기존={old} 공유={new} ({res['note']})")

print(f"대조: {len(codes)}종목 × {len(test_dates)}시점 = {total}건")
print(f"불일치: {mismatch}건 ({mismatch/total*100:.1f}%)")
if examples:
    print("\n불일치 예시:")
    for e in examples: print(e)
else:
    print("\n✅ 완전 일치 — 공유 로직이 기존 로직을 정확히 재현")
