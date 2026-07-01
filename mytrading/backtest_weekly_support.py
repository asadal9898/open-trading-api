"""주봉 추세 + 지지선 분할매수 백테스트 (차영석 18년 실전법)
- 주봉 변환
- 우상향 판단 (고점·저점·20주선 종합)
- 지지선 (과거 저점 군집)
- 지지선 근처 분할매수
"""
import csv
from datetime import datetime
from collections import defaultdict

DAILY_DIR = "backtester/.lean-workspace/data/equity/krx/daily"


def to_weekly(code):
    """일봉 CSV → 주봉 (마지막 미완성 주 제외)"""
    f = f"{DAILY_DIR}/{code.lower()}.csv"
    weeks = defaultdict(list)
    with open(f) as fp:
        for row in csv.reader(fp):
            if len(row) < 6:
                continue
            ds = row[0].strip()
            if len(ds) != 8 or not ds.isdigit():
                continue
            try:
                d = datetime.strptime(ds, "%Y%m%d")
                o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            except Exception:
                continue
            weeks[d.isocalendar()[:2]].append((ds, o, h, l, c))
    out = []
    for wk in sorted(weeks):
        b = weeks[wk]
        out.append({
            "week": f"{wk[0]}-W{wk[1]:02d}",
            "date": b[-1][0],
            "open": b[0][1],
            "high": max(x[2] for x in b),
            "low": min(x[3] for x in b),
            "close": b[-1][4],
        })
    return out[:-1]  # 마지막 미완성 주 제외


def trend_score(weekly, idx, n=52):
    """idx 시점 기준 우상향 점수 (0~3)"""
    if idx < n:
        return None
    w = weekly[idx - n:idx]
    half = n // 2
    front_hi = max(b["high"] for b in w[:half])
    back_hi = max(b["high"] for b in w[half:])
    hi_up = back_hi > front_hi
    front_lo = min(b["low"] for b in w[:half])
    back_lo = min(b["low"] for b in w[half:])
    lo_up = back_lo > front_lo
    closes = [b["close"] for b in weekly[:idx]]
    if len(closes) < 24:
        ma_up = False
    else:
        ma_now = sum(closes[-20:]) / 20
        ma_prev = sum(closes[-24:-4]) / 20
        ma_up = ma_now > ma_prev
    return {
        "score": sum([hi_up, lo_up, ma_up]),
        "고점": hi_up, "저점": lo_up, "20주선": ma_up,
    }


def find_support(weekly, idx, lookback=52, tol=0.03):
    """idx 시점 기준 지지선 = 과거 저점 군집
    lookback 주 동안의 주봉 저점들을 모아, 가까운 가격끼리 묶어
    여러 번 닿은 가격대를 지지선으로 (눈으로 보는 수평선 근사)
    """
    if idx < lookback:
        return []
    lows = sorted(b["low"] for b in weekly[idx - lookback:idx])
    # 가까운 저점끼리 군집 (tol 이내면 같은 지지대)
    clusters = []
    for lo in lows:
        placed = False
        for cl in clusters:
            if abs(lo - cl["price"]) / cl["price"] <= tol:
                cl["lows"].append(lo)
                cl["price"] = sum(cl["lows"]) / len(cl["lows"])
                placed = True
                break
        if not placed:
            clusters.append({"price": lo, "lows": [lo]})
    # 2번 이상 닿은 군집만 지지선으로 (touch 많을수록 강한 지지)
    supports = [
        {"price": cl["price"], "touches": len(cl["lows"])}
        for cl in clusters if len(cl["lows"]) >= 2
    ]
    supports.sort(key=lambda x: -x["touches"])
    return supports


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    w = to_weekly(code)
    print(f"{code} 주봉 {len(w)}주")
    # 현재 시점 우상향 + 지지선
    ts = trend_score(w, len(w))
    print(f"우상향: {ts}")
    sup = find_support(w, len(w))
    cur = w[-1]["close"]
    print(f"현재가: {cur:.0f}")
    print("지지선 (touch 많은 순):")
    for s in sup[:5]:
        gap = (cur - s["price"]) / cur * 100
        print(f"  {s['price']:.0f} ({s['touches']}회 닿음, 현재가 대비 -{gap:.1f}%)")
