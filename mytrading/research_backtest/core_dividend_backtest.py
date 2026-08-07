# -*- coding: utf-8 -*-
"""
코어 배당주(5년 연속 통과 26종목) 수익성 백테스트 — 배당 지속성이 실제 수익으로 이어지나.

방식: 2021-01-04 코어 26종목 동일가중 매수 → 2025-12-30 보유.
      배당 포함(총수익, 세전). 비교: 코스피 buy&hold.

★생존편향 주의: 코어 26종목은 2025년 시점에서 5년 다 통과한 종목을 뒤돌아 고른 것.
  2021년엔 이 26종목을 특정할 수 없었음(미래 정보). 따라서 이 결과는 "완벽히
  골랐다면"의 상한선 — 실전은 이보다 낮음. 진짜 검증은 수준2(매년 그 시점 발굴).
  단, 메모리 기록상 배당전략은 배당률 필터가 생존편향을 상당 부분 방어(point-in-time
  == 고정 유니버스)했으므로 상한선과 실전 격차가 통상보다 작을 여지는 있음.

실행: uv run --with pandas python mytrading/research_backtest/core_dividend_backtest.py
"""
import json
import csv
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
EQ = _ROOT / "backtester" / ".lean-workspace" / "data" / "equity" / "krx" / "daily"
KOSPI = _ROOT / "backtester" / ".lean-workspace" / "data" / "index" / "kospi.csv"
DIV = _ROOT / "mytrading" / "research_backtest" / "data" / "div_history_5y.json"
PERS = _ROOT / "mytrading" / "research_backtest" / "data" / "dividend_persistence.json"

START = "20210104"
END = "20251230"


def load_prices(code):
    """{date: close} 종가 딕셔너리."""
    p = EQ / f"{code}.csv"
    if not p.exists():
        return {}
    out = {}
    for r in csv.reader(open(p)):
        if len(r) >= 5:
            try:
                out[r[0]] = float(r[4])
            except ValueError:
                pass
    return out


def load_kospi():
    out = {}
    for r in csv.reader(open(KOSPI)):
        if len(r) >= 5:
            try:
                out[r[0]] = float(r[4])
            except ValueError:
                pass
    return out


def annual_div_by_code(divdata):
    """{code: {year: 연간배당금}} — 회계연도 귀속."""
    def fy(dstr):
        y, m = int(dstr[:4]), int(dstr[4:6]) if len(dstr) >= 6 else 12
        return y - 1 if m <= 4 else y
    out = {}
    for code, info in divdata.items():
        d = {}
        for x in info.get("dividends", []):
            f = fy(str(x["date"]))
            d[f] = d.get(f, 0.0) + float(x.get("amount", 0) or 0)
        out[code] = d
    return out


def nearest_on_or_after(prices, date):
    ks = sorted(k for k in prices if k >= date)
    return prices[ks[0]] if ks else None


def nearest_on_or_before(prices, date):
    ks = sorted(k for k in prices if k <= date)
    return prices[ks[-1]] if ks else None


def mdd(series):
    peak = series[0]
    md = 0.0
    for v in series:
        peak = max(peak, v)
        md = min(md, v / peak - 1)
    return md * 100


def main():
    core = json.load(open(PERS, encoding="utf-8"))["core_5of5"]
    divdata = json.load(open(DIV, encoding="utf-8"))
    ann_div = annual_div_by_code(divdata)
    codes = [s["code"] for s in core]
    names = {s["code"]: s["name"] for s in core}

    # 각 종목 시작가·종료가 + 기간 배당 합
    per_stock = {}
    for code in codes:
        px = load_prices(code)
        p0 = nearest_on_or_after(px, START)
        p1 = nearest_on_or_before(px, END)
        if not p0 or not p1:
            continue
        # 2021~2025 배당 합 (보유 기간 배당)
        div_sum = sum(ann_div.get(code, {}).get(y, 0.0) for y in range(2021, 2026))
        price_ret = p1 / p0 - 1
        total_ret = (p1 + div_sum) / p0 - 1  # 배당 포함(세전)
        per_stock[code] = {
            "p0": p0, "p1": p1, "div": div_sum,
            "price_ret": price_ret * 100, "total_ret": total_ret * 100,
        }

    n = len(per_stock)
    # 동일가중 평균 수익률
    avg_price = sum(v["price_ret"] for v in per_stock.values()) / n
    avg_total = sum(v["total_ret"] for v in per_stock.values()) / n
    years = 5.0
    cagr_price = ((1 + avg_price / 100) ** (1 / years) - 1) * 100
    cagr_total = ((1 + avg_total / 100) ** (1 / years) - 1) * 100

    # 코스피 buy&hold
    kospi = load_kospi()
    k0 = nearest_on_or_after(kospi, START)
    k1 = nearest_on_or_before(kospi, END)
    kospi_ret = (k1 / k0 - 1) * 100
    kospi_cagr = ((k1 / k0) ** (1 / years) - 1) * 100

    # 동일가중 포트폴리오 일별 수익곡선 (MDD용, 가격만)
    all_dates = sorted(set().union(*[set(load_prices(c)) for c in per_stock]))
    all_dates = [d for d in all_dates if START <= d <= END]
    port = []
    price_cache = {c: load_prices(c) for c in per_stock}
    base = {c: nearest_on_or_after(price_cache[c], START) for c in per_stock}
    for d in all_dates:
        vals = []
        for c in per_stock:
            pv = nearest_on_or_before(price_cache[c], d)
            if pv and base[c]:
                vals.append(pv / base[c])
        if vals:
            port.append(sum(vals) / len(vals))
    port_mdd = mdd(port)
    kospi_series = [nearest_on_or_before(kospi, d) for d in all_dates]
    kospi_series = [x for x in kospi_series if x]
    kospi_mdd = mdd(kospi_series)

    print("=" * 60)
    print(f"코어 배당주 {n}종목 백테스트 ({START}~{END}, 5년)")
    print("=" * 60)
    print(f"\n{'':16}{'총수익':>10}{'CAGR':>9}{'MDD':>9}")
    print("-" * 46)
    print(f"{'코어(배당포함)':16}{avg_total:>+9.1f}%{cagr_total:>+8.1f}%{port_mdd:>+8.1f}%")
    print(f"{'코어(가격만)':16}{avg_price:>+9.1f}%{cagr_price:>+8.1f}%{port_mdd:>+8.1f}%")
    print(f"{'코스피 B&H':16}{kospi_ret:>+9.1f}%{kospi_cagr:>+8.1f}%{kospi_mdd:>+8.1f}%")
    print(f"\n참고: 기존 value_range 백테스트 CAGR +8.6% (5년, 세전)")
    print(f"\n※ 배당 세전. 배당소득세 15.4%·거래비용 미반영 (실전은 더 낮음)")
    print(f"※ ★생존편향: 코어는 2025년 시점에서 5년 통과 종목을 뒤돌아 고른 것 —")
    print(f"   이 결과는 상한선. 진짜 검증은 수준2(매년 그 시점 발굴)")

    # 종목별 상세 (총수익 순)
    print(f"\n{'종목':16}{'매수':>8}{'매도':>8}{'배당':>7}{'총수익':>9}")
    print("-" * 50)
    for code in sorted(per_stock, key=lambda c: -per_stock[c]["total_ret"]):
        v = per_stock[code]
        print(f"{names[code][:15]:16}{v['p0']:>8,.0f}{v['p1']:>8,.0f}"
              f"{v['div']:>7,.0f}{v['total_ret']:>+8.1f}%")


if __name__ == "__main__":
    main()
