"""
배당주 필터용 재무·배당 데이터 래퍼.

KIS 국내주식 재무/배당 API 를 import 재사용 (원본 수정 금지).
배당주 3조건 판단에 필요한 값만 추려서 제공.

조건1 배당률 : ksdinfo_dividend (예탁원 배당) → 연배당금 합 ÷ 현재가
조건2 영업이익: finance_financial_ratio (영업이익증가율·ROE)
조건3 부채비율: finance_financial_ratio (lblt_rate)  ※ 조건2와 같은 API

⚠️ 재무 API 는 실전(prod) 전용. 호출 시 자동으로 실전 인증 필요.
  레이트리밋 주의 — 종목마다 2회 호출(financial_ratio + ksdinfo_dividend).

사용:
    from mytrading.finance_data import get_financials, get_dividend_yield
    fin = get_financials("005930")          # 부채비율·영업이익·ROE 추이
    dy = get_dividend_yield("005930", price=339500)  # 시가배당률 %
"""
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 재무 API 예제 경로 추가
_EX = _REPO_ROOT / "examples_llm" / "domestic_stock"
for sub in ("finance_financial_ratio", "ksdinfo_dividend"):
    p = _EX / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _to_float(v, default=None):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def get_financials(symbol: str, years: int = 5) -> Optional[dict]:
    """
    재무비율 추이 (financial_ratio, 년단위). 최근 years개.
    반환: {
      "symbol", "latest_year",
      "debt_ratio": 최근 부채비율(%),
      "roe": 최근 ROE,
      "rows": [{year, debt_ratio, op_profit_growth, roe}, ...]  최근→과거,
      "roe_positive_years": 최근 years개 중 ROE>0 인 해 수,
    }
    데이터 없으면 None.
    """
    try:
        from finance_financial_ratio import finance_financial_ratio
    except Exception:
        return None

    try:
        df = finance_financial_ratio(
            fid_div_cls_code="0",            # 0=년
            fid_cond_mrkt_div_code="J",
            fid_input_iscd=symbol,
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "year": str(r.get("stac_yymm", "")),
            "debt_ratio": _to_float(r.get("lblt_rate")),
            "op_profit_growth": _to_float(r.get("bsop_prfi_inrt")),
            "roe": _to_float(r.get("roe_val")),
        })
    if not rows:
        return None

    recent = rows[:years]
    roe_pos = sum(1 for x in recent if (x["roe"] or 0) > 0)

    return {
        "symbol": symbol,
        "latest_year": rows[0]["year"],
        "debt_ratio": rows[0]["debt_ratio"],
        "roe": rows[0]["roe"],
        "rows": recent,
        "roe_positive_years": roe_pos,
    }


def get_dividend_yield(symbol: str, price: float,
                       f_dt: str = None, t_dt: str = None) -> Optional[dict]:
    """
    시가배당률(%) = 최근 1년 주당 배당금 합 ÷ 현재가 × 100.
    ksdinfo_dividend(예탁원 배당) 의 per_sto_divi_amt(주당배당금) 합산.

    price: 현재가 (호출 측에서 get_history/inquire_price 로 확보해 전달)
    f_dt/t_dt: 조회 기간(YYYYMMDD). 기본 최근 1년+.
    반환: {symbol, annual_dividend(주당 연배당금), yield_pct(시가배당률), count(배당횟수)}
    """
    try:
        from ksdinfo_dividend import ksdinfo_dividend
    except Exception:
        return None

    if not f_dt or not t_dt:
        from datetime import date, timedelta
        today = date.today()
        t_dt = today.strftime("%Y%m%d")
        f_dt = (today - timedelta(days=400)).strftime("%Y%m%d")  # 약 1년+

    try:
        df = ksdinfo_dividend(cts="", gb1="0", f_dt=f_dt, t_dt=t_dt,
                              sht_cd=symbol, high_gb="")
    except Exception:
        return None
    if df is None or df.empty:
        return {"symbol": symbol, "annual_dividend": 0.0, "yield_pct": 0.0, "count": 0}

    # 최근 1년 주당 배당금 합 (현금배당만; 주식배당 제외)
    total = 0.0
    cnt = 0
    cutoff = (None if not f_dt else f_dt)
    for _, r in df.iterrows():
        rec = str(r.get("record_date", ""))
        if cutoff and rec < cutoff:
            continue
        amt = _to_float(r.get("per_sto_divi_amt"), 0.0) or 0.0
        if amt > 0:
            total += amt
            cnt += 1

    yld = (total / price * 100) if price and price > 0 else 0.0
    return {
        "symbol": symbol,
        "annual_dividend": round(total, 1),
        "yield_pct": round(yld, 2),
        "count": cnt,
    }


if __name__ == "__main__":
    # 간단 확인 (실전 인증 필요)
    from mytrading.common import init
    init(require_confirm=False)

    for sym, price in [("005930", 339500), ("049720", 9500), ("009680", 9460)]:
        print(f"\n=== {sym} ===")
        fin = get_financials(sym)
        if fin:
            print(f"  부채비율 {fin['debt_ratio']}% | ROE {fin['roe']} | "
                  f"ROE>0 최근5년 {fin['roe_positive_years']}회")
        dy = get_dividend_yield(sym, price)
        if dy:
            print(f"  연배당 {dy['annual_dividend']}원 | 시가배당률 {dy['yield_pct']}% "
                  f"| 배당 {dy['count']}회")