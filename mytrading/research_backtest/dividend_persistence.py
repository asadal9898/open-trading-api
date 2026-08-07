# -*- coding: utf-8 -*-
"""
배당 지속성 검증 (수준 1) — 현재 배당이력 종목이 매년 ①배당 ②배당률>그해 국고채 였나.

각 종목 × 연도(2021~2025):
  연간배당금 = 그 해 모든 배당(반기+기말) 합
  배당률 = 연간배당금 / 그 해 연말 주가
  통과 = 배당률 > 그 해 국고채 3년 (그 해 금리로 판정 — 현재 금리로 과거 판단 금지)
주의: 수정주가 오염 방어로 배당률 15% 초과는 오류로 간주(제외).

실행: uv run --with pandas python mytrading/research_backtest/dividend_persistence.py
"""
import sys
import csv
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
DIV = _ROOT / "mytrading" / "research_backtest" / "data" / "div_history_5y.json"
KTB = _ROOT / "backtester" / ".lean-workspace" / "data" / "index" / "ktb3y.csv"
EQ = _ROOT / "backtester" / ".lean-workspace" / "data" / "equity" / "krx" / "daily"

YEARS = ["2021", "2022", "2023", "2024", "2025"]
MAX_YIELD = 15.0  # 이 초과는 수정주가 오염으로 간주


def load_ktb_by_year():
    """연도별 국고채 3년 (그 해 연말값)."""
    v = {}
    with open(KTB) as f:
        for r in csv.reader(f):
            y = r[0][:4]
            v[y] = float(r[4])  # 마지막 값이 그 해 연말
    return v


def year_end_price(code):
    """종목의 연말 종가 {year: close}. 없으면 빈 dict."""
    p = EQ / f"{code}.csv"
    if not p.exists():
        return {}
    out = {}
    with open(p) as f:
        for r in csv.reader(f):
            if len(r) < 5:
                continue
            y = r[0][:4]
            try:
                out[y] = float(r[4])  # 그 해 마지막 종가로 계속 덮어씀
            except ValueError:
                continue
    return out


def _fiscal_year(date_str):
    """배당기준일 → 회계연도.
    12월(11~12월) 기준일 = 그 해 회계연도.
    1~4월 기준일 = 전년도 회계연도 (2023 배당제도 개편: 결산배당 기준일이
    이듬해 봄으로 이동). 예: 20240320 → 2023 회계연도.
    """
    y = int(date_str[:4])
    m = int(date_str[4:6]) if len(date_str) >= 6 else 12
    if m <= 4:
        return str(y - 1)   # 봄 기준일 → 전년도 결산배당
    return str(y)


def annual_dividend(info):
    """회계연도별 배당금 합 {fiscal_year: sum}."""
    out = {}
    for d in info.get("dividends", []):
        fy = _fiscal_year(str(d.get("date", "")))
        amt = float(d.get("amount", 0) or 0)
        out[fy] = out.get(fy, 0.0) + amt
    return out


def main():
    div_data = json.load(open(DIV, encoding="utf-8"))
    ktb = load_ktb_by_year()

    rows = []  # (name, code, {year: (yield, pass)}, pass_count)
    for code, info in div_data.items():
        name = info.get("name", code)
        ann_div = annual_dividend(info)
        prices = year_end_price(code)
        year_res = {}
        pass_cnt = 0
        for y in YEARS:
            div = ann_div.get(y, 0.0)
            px = prices.get(y, 0.0)
            rate = ktb.get(y, 99.0)
            if div <= 0 or px <= 0:
                year_res[y] = None  # 배당 없음 or 주가 없음
                continue
            yld = div / px * 100.0
            if yld > MAX_YIELD:
                year_res[y] = ("oob", yld)  # 오염 의심
                continue
            ok = yld > rate
            year_res[y] = (ok, yld)
            if ok:
                pass_cnt += 1
        rows.append((name, code, year_res, pass_cnt))

    # 통과 횟수 내림차순
    rows.sort(key=lambda x: -x[3])

    # 헤더
    print(f"\n{'종목':<16}{'코드':>7}  " + "".join(f"{y[2:]:>7}" for y in YEARS) + f"{'통과':>6}")
    print(f"{'국고채기준':<16}{'':>7}  " + "".join(f"{ktb.get(y,0):>6.1f}%" for y in YEARS))
    print("-" * 66)

    core = []
    for name, code, yr, cnt in rows:
        cells = ""
        for y in YEARS:
            r = yr[y]
            if r is None:
                cells += f"{'-':>7}"
            elif r[0] == "oob":
                cells += f"{'?':>7}"
            else:
                mark = "✓" if r[0] else "✗"
                cells += f"{mark}{r[1]:>5.1f}"
        star = " ★" if cnt == 5 else ""
        print(f"{name[:15]:<16}{code:>7}  {cells}{cnt:>4}/5{star}")
        if cnt == 5:
            core.append((name, code))

    print("-" * 66)
    print(f"\n★ 5년 연속 통과(코어): {len(core)}종목")
    for name, code in core:
        print(f"   {name}({code})")
    print(f"\n※ ✓통과 ✗미달 -배당없음/주가없음 ?오염의심(배당률>{MAX_YIELD}%)")
    print("※ 그 해 국고채로 판정(현재 금리 아님). 배당률=연간배당금/연말주가")

    # 결과 저장 (JSON) — 매년 갱신 추적용
    import datetime
    out = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "years": YEARS, "ktb_by_year": {y: ktb.get(y) for y in YEARS},
           "core_5of5": [{"name": n, "code": c} for n, c in core],
           "stocks": []}
    for name, code, yr, cnt in rows:
        rec = {"name": name, "code": code, "pass_count": cnt, "by_year": {}}
        for y in YEARS:
            r = yr[y]
            if r is None:
                rec["by_year"][y] = None
            elif r[0] == "oob":
                rec["by_year"][y] = {"yield": round(r[1], 2), "status": "oob"}
            else:
                rec["by_year"][y] = {"yield": round(r[1], 2), "pass": r[0]}
        out["stocks"].append(rec)
    out_path = _ROOT / "mytrading" / "research_backtest" / "data" / "dividend_persistence.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {out_path.name} (코어 {len(core)}종목)")


if __name__ == "__main__":
    main()
