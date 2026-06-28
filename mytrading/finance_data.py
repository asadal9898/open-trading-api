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
for sub in ("finance_financial_ratio", "ksdinfo_dividend", "finance_income_statement"):
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
            "eps": _to_float(r.get("eps")),   # 주당순이익 (배당성향 검증용)
        })
    if not rows:
        return None

    recent = rows[:years]
    roe_pos = sum(1 for x in recent if (x["roe"] or 0) > 0)

    # 연간 결산 EPS (stac_yymm 이 12월). 첫 행은 분기(예:202603)일 수 있어
    # 분기 EPS 를 연간 배당과 나누면 배당성향이 뻥튀기됨 → 연간 EPS 만 사용.
    annual_eps = None
    annual_eps_year = None
    for x in rows:
        yr = str(x.get("year", ""))
        if yr.endswith("12") and x.get("eps") is not None:
            annual_eps = x["eps"]
            annual_eps_year = yr
            break

    return {
        "symbol": symbol,
        "latest_year": rows[0]["year"],
        "debt_ratio": rows[0]["debt_ratio"],
        "roe": rows[0]["roe"],
        "eps": rows[0]["eps"],              # 최근 EPS (분기 포함, 참고용)
        "annual_eps": annual_eps,           # 연간 결산 EPS (배당성향용)
        "annual_eps_year": annual_eps_year,
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

    # 가장 최근 "사업연도"의 배당만 합산 (현금배당; 주식배당 제외)
    # ⚠️ 기존 버그: 기간(400일) 안 모든 배당을 더해, 연1회 배당주는 2년치가 합산돼
    #    배당률이 2~3배 부풀려짐. → record_date 연도별로 묶어 최근 연도만 합산.
    by_year = {}   # 연도 -> [배당금...]
    for _, r in df.iterrows():
        rec = str(r.get("record_date", ""))
        if len(rec) < 4:
            continue
        amt = _to_float(r.get("per_sto_divi_amt"), 0.0) or 0.0
        if amt <= 0:
            continue
        yr = rec[:4]
        by_year.setdefault(yr, []).append(amt)

    if not by_year:
        return {"symbol": symbol, "annual_dividend": 0.0, "yield_pct": 0.0, "count": 0}

    latest_year = max(by_year.keys())     # 가장 최근 사업연도
    amts = by_year[latest_year]
    total = sum(amts)                     # 그 해 배당 합 (분기배당이면 여러 번)
    cnt = len(amts)

    yld = (total / price * 100) if price and price > 0 else 0.0
    return {
        "symbol": symbol,
        "annual_dividend": round(total, 1),
        "yield_pct": round(yld, 2),
        "count": cnt,
        "year": latest_year,
    }


def get_operating_profit(symbol: str, years: int = 5,
                         exclude_years=None) -> Optional[dict]:
    """
    영업이익(bsop_prti) 추이 — 손익계산서(finance_income_statement).
    "영업이익이 꾸준히 흑자인가" 판단용 (차영석 조건2).

    years: 최근 몇 년을 볼지
    exclude_years: 위기 연도 리스트(예: ["2008","2020"]) — 판단에서 제외.
                   시스템 위기(리먼·코로나)는 회사 잘못 아니므로 흑자 판단서 뺌.
    반환: {
      "symbol",
      "rows": [{year, op_profit}, ...] 최근→과거 (제외연도 표시),
      "checked_years": 위기 제외하고 실제 본 해 수,
      "positive_years": 그중 영업이익>0 인 해 수,
      "all_positive": 위기 제외 모든 해가 흑자인가 (조건2 통과 여부),
    }
    데이터 없으면 None.
    """
    exclude_years = set(str(y) for y in (exclude_years or []))
    try:
        from finance_income_statement import finance_income_statement
    except Exception:
        return None
    try:
        df = finance_income_statement(
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
        yymm = str(r.get("stac_yymm", ""))
        yr = yymm[:4] if len(yymm) >= 4 else yymm
        op = _to_float(r.get("bsop_prti"))  # 영업이익
        rows.append({"year": yr, "op_profit": op,
                     "excluded": yr in exclude_years})

    if not rows:
        return None

    recent = rows[:years]
    # 위기 연도 제외하고 흑자 판단
    checked = [x for x in recent if not x["excluded"] and x["op_profit"] is not None]
    positive = [x for x in checked if x["op_profit"] > 0]
    all_positive = (len(checked) > 0 and len(positive) == len(checked))

    return {
        "symbol": symbol,
        "rows": recent,
        "checked_years": len(checked),
        "positive_years": len(positive),
        "all_positive": all_positive,
    }


def _prev_trading_day(date_str: str, open_days: set = None) -> str:
    """date_str(YYYYMMDD)의 직전 거래일(YYYYMMDD). 휴장일은 건너뜀.
    배당락일 = 배당기준일의 1거래일 전 (이날 사면 배당 못 받음).

    open_days: 개장일 set(YYYYMMDD) 를 넘기면 그걸 사용 (API 호출 0회).
               없으면 캘린더를 1회 받아서 판정.
    """
    from datetime import datetime, timedelta
    d = datetime.strptime(date_str, "%Y%m%d")

    # 개장일 set 준비 (없으면 기준일 30일 이전부터 캘린더 1회 조회)
    if open_days is None:
        try:
            from mytrading.market_calendar import get_calendar
            base = (d - timedelta(days=30)).strftime("%Y%m%d")
            cal = get_calendar(base)
            open_days = {rec["date"] for rec in cal if rec.get("opnd_yn") == "Y"}
        except Exception:
            open_days = None

    for _ in range(15):   # 최대 15일(연휴 대비) 뒤로
        d = d - timedelta(days=1)
        ds = d.strftime("%Y%m%d")
        if open_days is not None:
            if ds in open_days:
                return ds
        else:
            # 캘린더 못 받으면 주말만 건너뜀 (월=0..금=4)
            if d.weekday() < 5:
                return ds
    return d.strftime("%Y%m%d")


def get_ex_dividend_dates(symbol: str, years_back: int = 2) -> Optional[dict]:
    """
    종목의 배당락일들 — 배당기준일(record_date)의 1거래일 전.
    ksdinfo_dividend 의 record_date 에서 계산. 배당 0원(미지급) 제외.
    반환: {
      "symbol",
      "ex_dates": [{record_date, ex_date, amount, year}, ...] 최근→과거,
    }  데이터 없으면 None.
    ※ 과거 배당 기준. 미래 배당락일은 공시 전까지 알 수 없음(작년 패턴 참고용).
    """
    try:
        from ksdinfo_dividend import ksdinfo_dividend
    except Exception:
        return None
    from datetime import date, timedelta
    today = date.today()
    f_dt = (today - timedelta(days=365 * years_back + 30)).strftime("%Y%m%d")
    t_dt = today.strftime("%Y%m%d")
    try:
        df = ksdinfo_dividend(cts="", gb1="0", f_dt=f_dt, t_dt=t_dt,
                              sht_cd=symbol, high_gb="")
    except Exception:
        return None
    if df is None or df.empty:
        return None

    # 배당락일 계산: 각 기준일마다 _prev_trading_day 가 그 기준일 근처
    # 캘린더를 받음(get_calendar 캐시가 받쳐줌). 한 캘린더로 2년치 커버하면
    # 504일 범위를 벗어나는 최근 기준일이 누락되므로, 기준일별 조회가 정확.
    ex_dates = []
    for _, r in df.iterrows():
        rec = str(r.get("record_date", "")).strip()
        if len(rec) != 8:
            continue
        amt = _to_float(r.get("per_sto_divi_amt"), 0.0) or 0.0
        if amt <= 0:
            continue   # 배당 0원(미지급)은 배당락 없음
        ex = _prev_trading_day(rec)
        ex_dates.append({
            "record_date": rec,
            "ex_date": ex,
            "amount": amt,
            "year": rec[:4],
        })
    if not ex_dates:
        return None
    return {"symbol": symbol, "ex_dates": ex_dates}


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
        op = get_operating_profit(sym, exclude_years=["2008", "2020"])
        if op:
            print(f"  영업이익 흑자: {op['positive_years']}/{op['checked_years']}년 "
                  f"(위기제외) → 꾸준흑자 {op['all_positive']}")