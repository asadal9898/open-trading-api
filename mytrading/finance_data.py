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

p = "mytrading/finance_data.py"
s = open(p, encoding="utf-8").read()

def _load_debt_rules():
    """mytrading_config.yaml의 debt_industry_rules + debt_default 로드.
    키워드 매칭: KIS industry 텍스트에 키워드 포함 시 규칙 적용.
    각 규칙 = {sector, debt}. debt=None(yaml none) 이면 부채비율 안 봄.
    반환: (rules_dict, default_rule)
    """
    default = {"sector": "기타", "debt": 100}
    rules = {
        "은행": {"sector": "금융", "debt": None},
        "보험": {"sector": "금융", "debt": None},
        "증권": {"sector": "금융", "debt": None},
        "여신": {"sector": "금융", "debt": None},
        "금융": {"sector": "금융", "debt": None},
        "선박": {"sector": "산업재", "debt": 200},
        "건설": {"sector": "건설", "debt": 150},
    }
    try:
        import yaml
        cfg_path = _REPO_ROOT / "mytrading" / "mytrading_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        r = cfg.get("debt_industry_rules")
        if r and isinstance(r, dict):
            rules = r
        d = cfg.get("debt_default")
        if d and isinstance(d, dict):
            default = d
    except Exception:
        pass
    return rules, default


def _match_debt_rule(industry):
    """industry(KIS 표준산업분류)에 맞는 부채 규칙. 키워드 포함 매칭.
    없으면 기본. 반환: {sector, debt}."""
    if industry:
        for kw, rule in _DEBT_RULES.items():
            if kw in industry:
                return rule
    return _DEBT_DEFAULT



_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 재무 API 예제 경로 추가
_EX = _REPO_ROOT / "examples_llm" / "domestic_stock"
for sub in ("finance_financial_ratio", "ksdinfo_dividend", "finance_income_statement", "finance_growth_ratio"):
    p = _EX / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# 부채 규칙 로드 — ※ _REPO_ROOT 정의 뒤여야 함!
# (앞에 두면 NameError → except 로 조용히 기본값 폴백 → config 무시됨)
_DEBT_RULES, _DEBT_DEFAULT = _load_debt_rules()


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


def get_growth(symbol: str) -> Optional[dict]:
    """성장성 비율 (finance_growth_ratio).
    반환: {
      "revenue_growth": 매출액 증가율(%),
      "op_profit_growth": 영업이익 증가율(%),
      "equity_growth": 자기자본 증가율(%),
    }
    ※ 영업이익증가율은 이 API(growth_ratio)에 있음.
      financial_ratio 에는 없어서 get_financials 에선 None 나옴.
    """
    try:
        from finance_growth_ratio import finance_growth_ratio
    except Exception:
        return None
    try:
        df = finance_growth_ratio(
            fid_input_iscd=symbol,
            fid_div_cls_code="0",           # 0=년
            fid_cond_mrkt_div_code="J",
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None
    r = df.iloc[0]
    return {
        "symbol": symbol,
        "revenue_growth": _to_float(r.get("grs")),           # 매출액 증가율
        "op_profit_growth": _to_float(r.get("bsop_prfi_inrt")),  # 영업이익 증가율
        "equity_growth": _to_float(r.get("equt_inrt")),      # 자기자본 증가율
    }


def _debt_note(debt_ratio, industry=None):
    """부채비율 해석 (업종별 기준). debt_industry_rules 기반.
    - debt=None 업종(은행·보험·증권 등): 부채비율로 판단 안 함.
    - 그 외: 업종별 상한 대비 판정.
    """
    if debt_ratio is None:
        return "부채비율 없음"
    rule = _match_debt_rule(industry)
    limit = rule.get("debt")
    sector = rule.get("sector", "기타")
    if limit is None:
        return f"{debt_ratio:.0f}% ({sector}: 업 특성상 부채비율 판단 제외)"
    if debt_ratio < limit:
        return f"{debt_ratio:.0f}% (양호 · {sector} 기준 {limit:.0f}%)"
    if debt_ratio < limit * 1.5:
        return f"{debt_ratio:.0f}% (보통 · {sector} 기준 {limit:.0f}%)"
    return f"{debt_ratio:.0f}% (높음 — 주의 · {sector} 기준 {limit:.0f}%)"


def _quarter_standalone(rows):
    """누적(YTD) 분기 EPS를 단일 분기 EPS로 차분.
    KIS는 누적값: Q1=1186, Q2=1920(누적), Q3=3701, 연간=6564
    → 단일: Q1=1186, Q2=734, Q3=1781, Q4=2863.
    rows: [{year, eps}, ...] 최근→과거. 반환: [{quarter, eps_cum, eps_q}] 최근→과거.
    """
    asc = list(reversed(rows))
    out = []
    prev_year = None
    prev_cum = 0.0
    for r in asc:
        ym = str(r.get("year", ""))
        eps_cum = r.get("eps")
        if eps_cum is None or len(ym) < 6:
            continue
        year, mm = ym[:4], ym[4:6]
        if mm == "03" or year != prev_year:
            eps_q = eps_cum
        else:
            eps_q = eps_cum - prev_cum
        out.append({"quarter": ym, "eps_cum": eps_cum, "eps_q": round(eps_q, 1)})
        prev_year = year
        prev_cum = eps_cum
    return list(reversed(out))


def get_quarterly_eps(symbol, init_kis=False):
    """분기별 단일 EPS 추세 (누적 차분).
    반환: {symbol, quarters:[{quarter, eps_cum, eps_q}] 최근→과거, ttm_eps}."""
    if init_kis:
        try:
            from mytrading.common import init
            init(require_confirm=False)
        except Exception:
            pass
    try:
        from finance_financial_ratio import finance_financial_ratio
    except Exception:
        return None
    try:
        df = finance_financial_ratio(
            fid_div_cls_code="1",
            fid_cond_mrkt_div_code="J",
            fid_input_iscd=symbol,
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None
    rows = [{"year": str(r.get("stac_yymm", "")), "eps": _to_float(r.get("eps"))}
            for _, r in df.iterrows()]
    quarters = _quarter_standalone(rows)
    if not quarters:
        return None
    ttm = None
    q_eps = [q["eps_q"] for q in quarters[:4] if q["eps_q"] is not None]
    if len(q_eps) == 4:
        ttm = round(sum(q_eps), 1)
    return {"symbol": symbol, "quarters": quarters, "ttm_eps": ttm}


def _is_prod():
    """현재 KIS 모드가 실전(prod)인지. 모의(vps)면 KIS 재무 API 불가."""
    import os
    return os.environ.get("KIS_MODE", "vps").lower() == "prod"


def get_per(symbol, price=None, init_kis=False, year=None):
    """PER (연간 + TTM). KIS + DART 교차검증.

    - 모의(vps) 모드: KIS 재무 API 불가 → DART 만 사용.
    - 실전(prod) 모드: KIS + DART 둘 다 조회 → 값 비교(교차검증).
      두 소스 EPS 차이가 3% 초과면 cross_check 에 경고.

    price 없으면 get_trend 로 조회.
    반환: {symbol, price, eps_annual, per_annual, eps_ttm, per_ttm,
           source, kis_eps, dart_eps, cross_check, diff_pct}
    """
    from datetime import date
    if year is None:
        year = date.today().year - 1        # 직전 결산연도

    if init_kis and _is_prod():
        try:
            from mytrading.common import init
            init(require_confirm=False)
        except Exception:
            pass

    # 현재가
    if price is None:
        price = _price_of(symbol)
    if not price:
        return None

    kis_eps = kis_ttm = None
    if _is_prod():
        fin = get_financials(symbol)
        kis_eps = fin.get("annual_eps") if fin else None
        qe = get_quarterly_eps(symbol)
        kis_ttm = qe.get("ttm_eps") if qe else None

    # DART (모드 무관)
    dart_eps = None
    try:
        from mytrading.dart_data import get_eps as dart_get_eps
        dart_eps = dart_get_eps(symbol, year, "FY")
    except Exception:
        pass

    # 교차검증 (KIS vs DART EPS)
    _DIFF_LIMIT = 0.03                  # 3% 초과면 "차이 큼"
    diff_pct = None
    cross_check = "소스 1개"
    if kis_eps and dart_eps and dart_eps != 0:
        diff_pct = round(abs(kis_eps - dart_eps) / abs(dart_eps) * 100, 1)
        cross_check = ("일치" if diff_pct <= _DIFF_LIMIT * 100
                       else "차이 큼 — 확인 필요")

    eps_annual = kis_eps or dart_eps
    source = ("KIS+DART" if (kis_eps and dart_eps)
              else "KIS" if kis_eps else "DART" if dart_eps else None)

    def _per(eps):
        if eps and eps > 0:
            return round(price / eps, 1)
        return None

    return {
        "symbol": symbol,
        "price": price,
        "eps_annual": eps_annual,
        "per_annual": _per(eps_annual),
        "eps_ttm": kis_ttm,
        "per_ttm": _per(kis_ttm),
        "source": source,
        "kis_eps": kis_eps,
        "dart_eps": dart_eps,
        "cross_check": cross_check,
        "diff_pct": diff_pct,
    }


def _load_op_rules():
    """config의 op_profit_rules 로드 (영업이익 평가 규칙)."""
    default = {
        "check_years": 5,
        "min_positive_years": 3,
        "exclude_years": ["2008", "2020"],
        "grades": [
            {"min": 25, "label": "호재", "score": 2},
            {"min": 10, "label": "좋음", "score": 1},
            {"min": -10, "label": "보통", "score": 0},
            {"min": -25, "label": "경고", "score": -1},
            {"min": None, "label": "위험", "score": -2},
        ],
    }
    try:
        import yaml
        cfg_path = _REPO_ROOT / "mytrading" / "mytrading_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        r = cfg.get("op_profit_rules")
        if r and isinstance(r, dict):
            return r
    except Exception:
        pass
    return default
_OP_RULES = _load_op_rules()


def get_recent_quarter_growth(symbol, init_kis=False):
    """최근 분기 영업이익증가율 (YoY — 작년 동기 대비, 계절성 상쇄).
    KIS finance_financial_ratio(div=1) 의 bsop_prfi_inrt 첫 행.
    반환: {quarter, op_growth} | None
    """
    if init_kis:
        try:
            from mytrading.common import init
            init(require_confirm=False)
        except Exception:
            pass
    try:
        from finance_financial_ratio import finance_financial_ratio
    except Exception:
        return None
    try:
        df = finance_financial_ratio(
            fid_div_cls_code="1",
            fid_cond_mrkt_div_code="J",
            fid_input_iscd=symbol,
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None
    r = df.iloc[0]
    return {
        "quarter": str(r.get("stac_yymm", "")),
        "op_growth": _to_float(r.get("bsop_prfi_inrt")),
    }


def _op_grade(growth):
    """영업이익증가율 → (등급, 점수).

    등급: config grades (대호재/호재/좋음/보통/경고/위험/대악재)
    점수 (차영석 확정):
      |증가율| >= score_step(25%) → 계단식: 부호 x (1 + |증가율| // 25)
        예: +25%→+2, +50%→+3, +100%→+5, +756%→+31
            -25%→-2, -50%→-3
      |증가율| <  25% → grades 의 고정 score (좋음 +1 / 보통 0 / 경고 -1)
    ※ 상한 없음 (차영석 확정). 결합 설계 시 정규화 검토.
    """
    if growth is None:
        return ("판단 불가", None)

    # 등급 (양수 구간은 "이상", 음수 구간은 "이하"로 판정)
    label = "대악재"
    fixed = None
    for g in _OP_RULES.get("grades", []):
        lim = g.get("min")
        if lim is None:
            label = g.get("label", "?")
            fixed = g.get("score")
            break
        hit = (growth >= lim) if lim >= 0 else (growth > lim)
        if hit:
            label = g.get("label", "?")
            fixed = g.get("score")
            break

    # 점수
    step = _OP_RULES.get("score_step", 25)
    if abs(growth) >= step:
        sign = 1 if growth > 0 else -1
        score = sign * (1 + int(abs(growth) // step))
    else:
        score = fixed if fixed is not None else 0

    return (label, score)


# 국면 판정 임계값 (백테스트 collect_op/build_disclosed 와 동일 — 통일 목적)
_PHASE_BIG = 25.0        # |YoY| 이 값 이상이면 "큰 변화"
_PHASE_GROWTH_AVG = 20.0 # 성장 vs 턴어라운드 가르는 5년평균 대비 기준
_PHASE_QUARTERS = ("Q1", "Q2", "Q3", "Q4")


# 국면 → 매수 가능 여부 (실전·백테스트 공유)
#   침체 = 실적 국면 악화 → 배제
#   None(판정불가) = 데이터 부족 → 안전 우선으로 배제 (차영석 확정: 모르면 안 산다)
_PHASE_BLOCK_BUY = {"침체", None}


def is_buyable_phase(phase):
    """국면이 매수 허용인지. 침체·판정불가(None)면 False."""
    return phase not in _PHASE_BLOCK_BUY


def _judge_phase_from_data(hist, rcepts, as_of):
    """국면 판정 — 순수 로직 (데이터 주입식).

    hist:   {year: {Q1: op억원, Q2: op, ...}}  — 공시 확인된 값만
    rcepts: {(year, q): date}                  — 각 값의 실제 공시일
    as_of:  판정 기준일 (date)

    실전(_judge_phase)·백테스트가 공유하는 단일 판정부.
    데이터 소스(실전=DART, 백테스트=op_history.json)와 무관하게
    동일한 국면을 산출 → 로직 분기(divergence) 원천 차단.

    반환: {"phase", "yoy", "vs", "quarter", "note"}  (판정 불가 시 phase=None)
    """
    # 가장 최근에 공시된 (year, q) = 당분기
    if not rcepts:
        return {"phase": None, "yoy": None, "vs": None,
                "quarter": None, "note": "공시된 분기 실적 없음"}
    (cyr, cq), _ = max(rcepts.items(), key=lambda kv: kv[1])
    cur = hist[cyr][cq]

    # 전년 동일분기 → YoY
    prev = hist.get(cyr - 1, {}).get(cq)
    if prev is None or prev == 0:
        return {"phase": None, "yoy": None, "vs": None,
                "quarter": f"{cyr}{cq}", "note": "전년 동기 없음 → YoY 불가"}
    yoy = (cur / prev - 1) * 100

    # 지난 5년 동일분기 평균 → vs
    past = [hist[y][cq] for y in range(cyr - 5, cyr)
            if y in hist and cq in hist[y]]
    if len(past) < 2:
        return {"phase": None, "yoy": yoy, "vs": None,
                "quarter": f"{cyr}{cq}", "note": "5년평균 표본 부족(<2)"}
    avg = sum(past) / len(past)
    vs = (cur / avg - 1) * 100 if avg else 0

    # 5분류 (build_disclosed 와 동일)
    if abs(yoy) < _PHASE_BIG:
        phase = "평범"
    elif yoy >= _PHASE_BIG:
        phase = "성장" if vs >= _PHASE_GROWTH_AVG else "턴어라운드"
    else:  # yoy <= -_PHASE_BIG
        phase = "정점통과" if vs > 0 else "침체"

    note = f"{cyr}{cq}: YoY {yoy:+.1f}% · 5년평균대비 {vs:+.1f}% → {phase}"
    return {"phase": phase, "yoy": yoy, "vs": vs,
            "quarter": f"{cyr}{cq}", "note": note}


# 국면 판정 캐시 — (종목, 기준일) 단위. 국면은 분기 공시 때만 바뀌므로 일 단위로 충분.
#   _judge_phase 는 종목당 DART 30회 호출(6년 x 4보고서 + list) → 캐시 필수.
_PHASE_CACHE = {}
_PHASE_CACHE_MAX = 2000


def _judge_phase(symbol, as_of=None):
    """영업이익 국면 판정 (실전) — DART 로 데이터 수집 후 _judge_phase_from_data 호출.

    관심사 분리: passed(종목 품질) 와 별개로 "지금 살 국면인가"만 판정.
    매수 로직에서 phase == "침체" 이면 매수 배제.

    as_of: 판정 기준일 (date). None 이면 오늘.
      rcept(실제 공시일) <= as_of 인 분기만 사용 → 룩어헤드 차단.

    ※ 판정 로직은 _judge_phase_from_data 에 있음 (백테스트와 공유).
      이 함수는 DART 수집 껍데기.
    반환: _judge_phase_from_data 와 동일.
    """
    from datetime import date
    from mytrading.dart_data import get_quarterly_op

    if as_of is None:
        as_of = date.today()

    ckey = (str(symbol), as_of)
    if ckey in _PHASE_CACHE:
        return _PHASE_CACHE[ckey]

    # 최근 6년치 분기 영업이익 + 공시일 수집
    #   (5년 동일분기 평균 + 당해 → 최소 6년 필요)
    hist = {}   # {year: {Q1: op, Q2: op, ...}}, 공시된 것만
    rcepts = {} # {(year, q): date}
    for yr in range(as_of.year - 6, as_of.year + 1):
        try:
            qd = get_quarterly_op(symbol, yr)
        except Exception:
            continue
        for q in _PHASE_QUARTERS:
            cell = qd.get(q) or {}
            op = cell.get("op")
            rc = cell.get("rcept")
            # 공시 확인된(rcept<=as_of) 분기만 채택 → 룩어헤드 차단
            if op is None or rc is None or rc > as_of:
                continue
            hist.setdefault(yr, {})[q] = op
            rcepts[(yr, q)] = rc

    res = _judge_phase_from_data(hist, rcepts, as_of)
    if len(_PHASE_CACHE) >= _PHASE_CACHE_MAX:
        _PHASE_CACHE.clear()          # 단순 전체 비움 (날짜 바뀌면 어차피 무효)
    _PHASE_CACHE[ckey] = res
    return res


def evaluate_operating_profit(symbol, init_kis=False):
    """영업이익 평가 (재무 2순위). config op_profit_rules 기준.

    1) 흑자 지속성 — 최근 N년 중 M년 이상 흑자 (위기연도 제외)
    2) 최근 분기 영업이익증가율(YoY) → 등급·점수
       +25%↑ 호재(+2) / +10%↑ 좋음(+1) / ±10% 보통(0)
       / -10%↓ 경고(-1) / -25%↓ 위험(-2)

    passed = 흑자 지속성 충족 AND 등급이 "위험" 아님.
    반환: {symbol, positive_years, checked_years, steady,
           recent_quarter, op_growth, grade, score, passed, note}
    """
    if init_kis:
        try:
            from mytrading.common import init
            init(require_confirm=False)
        except Exception:
            pass

    years = _OP_RULES.get("check_years", 5)
    min_pos = _OP_RULES.get("min_positive_years", 3)
    excl = _OP_RULES.get("exclude_years", ["2008", "2020"])

    op = get_operating_profit(symbol, years=years, exclude_years=excl)
    q = get_recent_quarter_growth(symbol)

    pos = op.get("positive_years") if op else None
    checked = op.get("checked_years") if op else None
    growth = q.get("op_growth") if q else None
    quarter = q.get("quarter") if q else None
    grade, score = _op_grade(growth)

    steady = (pos is not None and pos >= min_pos)
    passed = bool(steady and grade not in ("위험", "대악재", "판단 불가"))

    if pos is None:
        note = "영업이익 데이터 없음"
    else:
        g = f"{growth:+.1f}%" if growth is not None else "증가율 없음"
        note = f"흑자 {pos}/{checked}년 · 최근분기({quarter}) {g} → {grade}"

    # 국면 판정 (방식 B: passed 와 분리, 정보로만 반환)
    #   매수 로직에서 phase == "침체" 이면 매수 배제.
    try:
        ph = _judge_phase(symbol)
    except Exception as e:
        ph = {"phase": None, "yoy": None, "vs": None,
              "quarter": None, "note": f"국면 판정 실패: {e}"}

    return {
        "symbol": symbol,
        "positive_years": pos,
        "checked_years": checked,
        "steady": steady,
        "recent_quarter": quarter,
        "op_growth": growth,
        "grade": grade,
        "score": score,
        "passed": passed,
        "note": note,
        "phase": ph.get("phase"),
        "phase_yoy": ph.get("yoy"),
        "phase_vs": ph.get("vs"),
        "phase_quarter": ph.get("quarter"),
        "phase_note": ph.get("note"),
    }


def _price_of(symbol):
    """현재가. inquire_price(실시간 API) 우선, 실패 시 CSV 캐시(get_trend).
    ※ universe에 갓 추가된 종목은 일봉 CSV가 아직 없음 (daily_update 전).
      캐시만 믿으면 배당률 계산이 안 됨 → API 직접 조회가 안전.
    """
    # 1순위: inquire_price (캐시 불필요)
    try:
        ip_dir = _REPO_ROOT / "examples_llm" / "domestic_stock" / "inquire_price"
        if str(ip_dir) not in sys.path:
            sys.path.insert(0, str(ip_dir))
        import inquire_price as _ip
        df = _ip.inquire_price(env_dv="real", fid_cond_mrkt_div_code="J",
                               fid_input_iscd=symbol)
        if df is not None and not df.empty:
            v = df.iloc[0].get("stck_prpr", None)
            if v not in (None, "", "0"):
                return float(str(v).replace(",", ""))
    except Exception:
        pass
    # 2순위: CSV 캐시
    try:
        from mytrading.data_manager import get_trend
        t = get_trend(symbol)
        return t.get("last") if t else None
    except Exception:
        return None


def get_base_rate(fallback=3.0):
    """국고채 3년물 금리 (%) — 배당 매력 판단의 기준(무위험 수익률).

    출처: KCIF INSIGHT 파싱 결과 YAML (월간 갱신).
      history / {최신호} / categories / 국내 채권시장 / 시장금리 / 국고채 3년
      → 시계열의 마지막 값(최근월)

    한국은 국고채 3년물이 시장금리 벤치마크 (미국·일본은 10년물).
    배당주는 중기 보유 자산이라 3년물과 비교하는 게 합리적.

    실패 시 fallback (config 또는 기본 3.0%).
    반환: float (%) — 예: 3.04
    """
    try:
        import yaml
        p = (_REPO_ROOT / "mytrading" / "reports" / "investment_checklist"
             / "kcif_insight_key_indicators_history.yaml")
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        hist = d.get("history") or {}
        if not hist:
            return fallback
        latest = sorted(hist.keys())[-1]              # 최신 호
        series = (hist[latest].get("categories", {})
                  .get("국내 채권시장", {})
                  .get("시장금리", {})
                  .get("국고채 3년"))
        if series and isinstance(series, list):
            vals = [v for v in series if v is not None]
            if vals:
                return float(vals[-1])                # 최근월 값
    except Exception:
        pass
    return fallback


def _load_div_rules():
    """config의 dividend_filter 로드 (배당 평가 규칙)."""
    default = {
        "base_rate_source": "kcif",
        "fallback_rate": 3.5,
        "kospi": {"premium": 0.5, "rate_threshold": 4.0},
        "kosdaq": {"premium": 1.0, "rate_threshold": 4.5},
    }
    try:
        import yaml
        cfg_path = _REPO_ROOT / "mytrading" / "mytrading_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        r = cfg.get("dividend_filter")
        if r and isinstance(r, dict):
            return r
    except Exception:
        pass
    return default


def dividend_threshold(market="kospi"):
    """배당률 기준선 (%) — 국고채 3년물 + 프리미엄.

    base_rate_source:
      kcif  → KCIF INSIGHT의 국고채 3년물 + premium (금리 환경 자동 반영)
      fixed → config의 rate_threshold 고정값

    반환: {threshold, base_rate, premium, source}
    """
    rules = _load_div_rules()
    mk = rules.get(market, {}) or {}
    src = rules.get("base_rate_source", "kcif")

    if src == "fixed":
        th = mk.get("rate_threshold", 4.0)
        return {"threshold": th, "base_rate": None,
                "premium": None, "source": "fixed"}

    fb = rules.get("fallback_rate", 3.5)
    base = get_base_rate(fallback=fb)
    prem = mk.get("premium", 0.5)
    return {"threshold": round(base + prem, 2), "base_rate": base,
            "premium": prem, "source": "국고채3년+프리미엄"}


def evaluate_dividend(symbol, price=None, market="kospi", init_kis=False):
    """배당 평가 (재무 3순위).

    기준: 시가배당률 >= 국고채 3년물 + 프리미엄
      (코스피 +0.5%p / 코스닥 +1.0%p — 보수적. config 조정 가능)
      한국 시장금리 벤치마크 = 국고채 3년물. 금리 오르면 기준도 자동 상승.

    반환: {symbol, dividend_yield, threshold, base_rate, premium,
           passed, note}
    """
    if init_kis:
        try:
            from mytrading.common import init
            init(require_confirm=False)
        except Exception:
            pass

    if price is None:
        price = _price_of(symbol)          # 실시간 API 우선 (캐시 없어도 OK)

    div = get_dividend_yield(symbol, price) if price else None
    dy = div.get("yield_pct") if div else None      # 시가배당률(%)
    th = dividend_threshold(market)
    limit = th["threshold"]

    passed = bool(dy is not None and dy >= limit)

    if dy is None:
        note = "배당 데이터 없음"
    else:
        base = th["base_rate"]
        b = f"국고채 {base}% + {th['premium']}%p" if base else "고정"
        mark = "통과" if passed else "미달"
        note = f"배당 {dy:.2f}% vs 기준 {limit:.2f}% ({b}) → {mark}"

    return {
        "symbol": symbol,
        "dividend_yield": dy,
        "annual_dividend": div.get("annual_dividend") if div else None,
        "threshold": limit,
        "base_rate": th["base_rate"],
        "premium": th["premium"],
        "passed": passed,
        "note": note,
    }


def _load_style_rules():
    """config의 style_rules 로드 (스타일별 재무 평가 기준)."""
    default = {
        "value_range": {"debt": "required", "op_profit": "required",
                        "dividend": "required"},
        "momentum": {"debt": "required", "op_profit": "required",
                     "op_min_growth": 10, "dividend": "skip"},
        "accumulate": {"skip_financial": True},
        "free_holdings": {"advisory": True},
    }
    try:
        import yaml
        cfg_path = _REPO_ROOT / "mytrading" / "mytrading_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        r = cfg.get("style_rules")
        if r and isinstance(r, dict):
            return r
    except Exception:
        pass
    return default
_STYLE_RULES = _load_style_rules()


def evaluate_financials(symbol, style="momentum", industry=None,
                        market="kospi", price=None, init_kis=False):
    """재무 종합 평가 (스타일별 기준). 종목 평가의 재무 절반.

    스타일별 기준 (config style_rules):
      value_range  (배당주)   — 부채 + 영업이익 + 배당(필수)
      momentum     (공격적)   — 부채 + 영업이익(증가율 +10%↑). 배당 안 봄.
      accumulate   (ETF)      — 재무 평가 스킵 (개별 기업 아님)
      free_holdings(자유투자) — 평가는 하되 강제 안 함 (사람 판단)

    반환: {symbol, style, passed, advisory, score, debt, op, dividend, note}
      passed: 재무 기준 통과 여부 (advisory면 None)
      score:  참고 점수 (영업이익 등급 점수 기반)
    """
    rules = _STYLE_RULES.get(style, {}) or {}

    # ETF 등 — 재무 평가 스킵
    if rules.get("skip_financial"):
        return {"symbol": symbol, "style": style, "passed": None,
                "advisory": False, "skipped": True, "score": None,
                "note": f"{style}: 재무 평가 대상 아님 (ETF — 섹터 전망·차트로 판단)"}

    if init_kis:
        try:
            from mytrading.common import init
            init(require_confirm=False)
        except Exception:
            pass

    advisory = bool(rules.get("advisory"))       # 자유투자 = 알림만
    checks = []                                   # (항목, 통과여부, 설명)

    # --- 1순위: 부채 ---
    debt_info = None
    if rules.get("debt") == "required" or advisory:
        fin = get_financials(symbol)
        dr = fin.get("debt_ratio") if fin else None
        rule = _match_debt_rule(industry)
        limit = rule.get("debt")
        note = _debt_note(dr, industry)
        if limit is None:
            ok = True                             # 금융 등 — 부채 판단 제외
        elif dr is None:
            ok = False
        else:
            ok = dr <= limit
        debt_info = {"ratio": dr, "limit": limit,
                     "sector": rule.get("sector"), "passed": ok, "note": note}
        checks.append(("부채", ok, note))

    # --- 2순위: 영업이익 ---
    op_info = None
    if rules.get("op_profit") == "required" or advisory:
        op = evaluate_operating_profit(symbol)
        ok = bool(op and op.get("passed"))
        # 공격적(momentum)은 증가율 하한 추가 (성장 확인)
        min_g = rules.get("op_min_growth")
        if ok and min_g is not None:
            g = op.get("op_growth")
            if g is None or g < min_g:
                ok = False
        op_info = dict(op or {})
        op_info["passed"] = ok
        note = op.get("note") if op else "영업이익 데이터 없음"
        if min_g is not None:
            note += f" (기준 +{min_g}%↑)"
        checks.append(("영업이익", ok, note))

    # --- 3순위: 배당 (배당주만) ---
    div_info = None
    if rules.get("dividend") == "required" or advisory:
        dv = evaluate_dividend(symbol, price=price, market=market)
        ok = bool(dv and dv.get("passed"))
        div_info = dv
        checks.append(("배당", ok, dv.get("note") if dv else "배당 데이터 없음"))

    all_ok = all(c[1] for c in checks) if checks else False
    score = op_info.get("score") if op_info else None

    lines = [f"{n}: {'O' if ok else 'X'} {d}" for n, ok, d in checks]
    head = ("[참고] " if advisory else "") + \
           (f"{'통과' if all_ok else '탈락'}")
    note = head + " — " + " / ".join(lines)

    return {
        "symbol": symbol,
        "style": style,
        "passed": None if advisory else all_ok,
        "advisory": advisory,
        "skipped": False,
        "score": score,
        "debt": debt_info,
        "op": op_info,
        "dividend": div_info,
        "note": note,
    }


def get_financial_summary(symbol: str, industry: str = None) -> Optional[dict]:
    """재무 종합 — 개별 함수들을 한 번에 묶어서 반환.
    industry(표준산업분류) 주면 부채비율을 업종별로 해석.
    반환: {symbol, debt_ratio, debt_note, roe, op_positive,
           revenue_growth, op_profit_growth}
    ※ 재무 API 여러 개 호출 → 레이트리밋 주의 (스캔 시 남발 금지)
    """
    fin = get_financials(symbol)
    growth = get_growth(symbol)
    op = get_operating_profit(symbol, exclude_years=["2008", "2020"])
    if not fin and not growth:
        return None
    debt = fin.get("debt_ratio") if fin else None
    return {
        "symbol": symbol,
        "debt_ratio": debt,
        "debt_note": _debt_note(debt, industry),
        "roe": fin.get("roe") if fin else None,
        "op_positive": (f"{op['positive_years']}/{op['checked_years']}년"
                        if op else None),
        "revenue_growth": growth.get("revenue_growth") if growth else None,
        "op_profit_growth": growth.get("op_profit_growth") if growth else None,
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