"""
DART (전자공시) 데이터 조회 래퍼 — 검증용 보조 수단.
OpenDartReader(서드파티) 를 감싸서 종목코드 기반으로 업종·기업개황·재무를 제공.

KIS 데이터와 교차검증하거나, 모의(vps)에서 KIS 재무 API 가 막힐 때 보완용으로 사용.
※ OpenDartReader 는 서드파티 라이브러리. 나중에 직접 호출로 바꾸려면 이 파일만 수정하면 됨
  (바깥 코드는 get_industry / get_company / get_financials 시그니처만 의존).

인증키: ~/KIS/config/kis_devlp.yaml 의 my_DART_APIkey
업종변환표: mytrading/data/KSIC_09.csv (DART induty_code 3자리 → 한글 업종명)

사용:
    from mytrading.dart_data import get_industry, get_company, get_financials
    ind = get_industry("005930")            # "통신 및 방송 장비 제조업"
    comp = get_company("005930")            # dict: 대표자·설립일·주소 등
    fin = get_financials("005930", 2023)    # DataFrame: 재무제표
"""
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore")  # OpenDartReader 내부 SyntaxWarning 억제

_REPO_ROOT = Path(__file__).resolve().parents[1]
_KSIC_CSV = _REPO_ROOT / "mytrading" / "data" / "KSIC_09.csv"
_CONFIG = Path.home() / "KIS" / "config" / "kis_devlp.yaml"

_dart = None
_ksic = None


def _get_dart():
    """OpenDartReader 인스턴스 (지연 생성, 캐싱)."""
    global _dart
    if _dart is None:
        import yaml
        import OpenDartReader
        cfg = yaml.safe_load(open(_CONFIG, encoding="utf-8"))
        key = str(cfg.get("my_DART_APIkey", "")).strip()
        if not key:
            raise RuntimeError("kis_devlp.yaml 에 my_DART_APIkey 가 없습니다.")
        _dart = OpenDartReader(key)
    return _dart


def _get_ksic():
    """KSIC 코드→업종명 매핑 (로컬 CSV, 캐싱)."""
    global _ksic
    if _ksic is None:
        import csv
        _ksic = {}
        with open(_KSIC_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                _ksic[row["Industy_code"]] = row["Industy_name"]
    return _ksic


def get_industry(code: str) -> str:
    """종목코드 → 한글 업종명 (DART induty_code + KSIC 변환).
    실패 시 빈 문자열."""
    try:
        comp = _get_dart().company(str(code))
        icode = str(comp.get("induty_code", "")).strip()
        if not icode:
            return ""
        ksic = _get_ksic()
        if icode in ksic:
            return ksic[icode]
        for n in (len(icode) - 1, 3):
            if len(icode) > n >= 3 and icode[:n] in ksic:
                return ksic[icode[:n]]
        return ""
    except Exception as e:
        print(f"[dart] get_industry({code}) 실패: {e}")
        return ""


# 분기 보고서 코드 (DART reprt_code)
_REPRT = {
    "Q1": "11013",   # 1분기보고서
    "H1": "11012",   # 반기보고서 (= Q2 단독 EPS)
    "Q3": "11014",   # 3분기보고서
    "FY": "11011",   # 사업보고서 (연간)
}


def _find_eps(df):
    """전체재무제표에서 기본주당이익 추출.
    계정명이 보고서마다 다름: '기본주당이익' / '기본주당이익(손실)'
    → startswith 로 매칭.
    """
    if df is None or getattr(df, "empty", True):
        return None
    for _, r in df.iterrows():
        nm = str(r.get("account_nm", ""))
        if nm.startswith("기본주당이익"):
            v = str(r.get("thstrm_amount", "")).replace(",", "").strip()
            try:
                return float(v)
            except Exception:
                return None
    return None


def get_eps(code: str, year: int, period: str = "FY", fs: str = "CFS"):
    """DART 주당순이익(EPS).
    period: FY(연간) / Q1 / H1(=Q2 단독) / Q3.
    ※ DART 분기 EPS는 '단일 분기' 값 (KIS 는 누적이라 차분 필요 — 다름 주의).
    반환: float | None
    """
    reprt = _REPRT.get(period.upper())
    if not reprt:
        return None
    try:
        d = _get_dart()
        for _fs in (fs, "OFS" if fs == "CFS" else "CFS"):
            df = d.finstate_all(code, year, reprt_code=reprt, fs_div=_fs)
            v = _find_eps(df)
            if v is not None:
                return v
        return None
    except Exception as e:
        print(f"[dart] get_eps({code}, {year}, {period}) 실패: {e}")
        return None


def _find_op_profit(df):
    """전체재무제표에서 영업이익 추출 (억원 단위).
    계정명이 보고서마다 다름 → 정확 매칭 우선, 없으면 부분 매칭.
    반환: float(억원) | None
    """
    if df is None or getattr(df, "empty", True):
        return None
    op = df[df["account_nm"].str.strip() == "영업이익"]
    if op.empty:
        op = df[df["account_nm"].str.contains("영업이익", na=False)]
    if op.empty:
        return None
    v = str(op.iloc[0].get("thstrm_amount", "")).replace(",", "").strip()
    try:
        return float(v) / 1e8      # 원 → 억원
    except Exception:
        return None


# 정기보고서 report_nm 의 (YYYY.MM) → 분기 매핑
_MONTH_Q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "FY"}


def _disclosure_dates(code, year):
    """DART list(kind='A') 로 해당 연도 정기보고서의 실제 공시일(rcept_dt) 조회.
    반환: {"Q1": date, "Q2": date, "Q3": date, "FY": date}  (없으면 키 누락)
    ※ Q4 실적은 사업보고서(FY)로 처음 공개되므로 Q4 공시일 = FY 공시일.
    """
    from datetime import date as _date
    import re
    out = {}
    try:
        d = _get_dart()
        df = d.list(code, start=f"{year}-01-01", end=f"{year+1}-06-30", kind="A")
        if df is None or getattr(df, "empty", True):
            return out
        for _, r in df.iterrows():
            nm = str(r.get("report_nm", ""))
            if not any(k in nm for k in ("분기보고서", "반기보고서", "사업보고서")):
                continue
            m = re.search(r"\((\d{4})\.(\d{2})\)", nm)
            if not m:
                continue
            yr, mm = m.group(1), m.group(2)
            if yr != str(year):
                continue
            q = _MONTH_Q.get(mm)
            if not q:
                continue
            rd = str(r.get("rcept_dt", "")).strip()
            if len(rd) == 8:
                out[q] = _date(int(rd[:4]), int(rd[4:6]), int(rd[6:8]))
    except Exception as e:
        print(f"[dart] _disclosure_dates({code}, {year}) 실패: {e}")
    return out


def get_quarterly_op(code: str, year: int, fs: str = "CFS", with_dates: bool = True):
    """DART 분기별 영업이익(억원) + 실제 공시일.

    백테스트(collect_op.py)와 실전이 공유하는 단일 데이터 소스.
    데이터·룩어헤드 판정을 한 곳에서 관리 → 로직 분기(divergence) 방지.

    ※ Q2 는 반기보고서(11012) 값 = 상반기 '누적'.
      (collect_op.py 와 동일하게 재현 — 백테스트 검증 로직 유지가 목적.
       Q2 단독이 아님에 주의. 단독 필요 시 Q2 - Q1 로 별도 계산.)
    ※ Q4 = FY - (Q1 + Q2 + Q3): collect_op 방식 그대로 유지.
      (Q2가 반기누적이라 엄밀히는 부정확하나, 백테스트가 쓴 정의를
       그대로 재현해야 재검증이 의미를 가짐. 국면 판정은 YoY·5년평균
       '비율' 비교라 이 정의로도 일관성 유지됨.)
    ※ 공시일(rcept): 그 분기 실적이 시장에 처음 공개된 날.
      실전 국면 판정 시 "rcept <= 현재일" 인 분기만 사용 → 룩어헤드 차단.

    반환: {
      "Q1": {"op": 억원|None, "rcept": date|None},
      "Q2": {...}, "Q3": {...}, "Q4": {...}, "FY": {...},
      "year": year,
    }
    """
    reprt_map = [("11013", "Q1"), ("11012", "Q2"),
                 ("11014", "Q3"), ("11011", "FY")]
    ops = {}
    d = _get_dart()
    for reprt, q in reprt_map:
        # 연결(CFS) 우선, 없으면 개별(OFS) 폴백.
        #   종속회사가 없는 소규모 기업은 연결재무제표를 작성하지 않아 CFS 가 빈 결과.
        #   실측: DSR제강·삼일기업공사는 OFS 에만 존재. 둘 다 있으면 CFS 가 그룹 전체 실적.
        val = None
        for _fs in (fs, "OFS" if fs == "CFS" else "CFS"):
            try:
                df = d.finstate_all(code, year, reprt_code=reprt, fs_div=_fs)
                val = _find_op_profit(df)
            except Exception:
                val = None
            if val is not None:
                break
        ops[q] = val

    if all(ops.get(k) is not None for k in ("Q1", "Q2", "Q3", "FY")):
        ops["Q4"] = round(ops["FY"] - (ops["Q1"] + ops["Q2"] + ops["Q3"]), 1)
    else:
        ops["Q4"] = None

    dates = _disclosure_dates(code, year) if with_dates else {}
    fy_date = dates.get("FY")

    out = {"year": year}
    for q in ("Q1", "Q2", "Q3", "Q4", "FY"):
        rcept = fy_date if q == "Q4" else dates.get(q)
        out[q] = {"op": ops.get(q), "rcept": rcept}
    return out


def get_quarterly_eps(code: str, year: int, fs: str = "CFS"):
    """DART 분기별 EPS (단일 분기 값).
    반환: {"Q1":.., "Q2":.., "Q3":.., "FY":.., "year":year}
    ※ Q2 는 반기보고서(H1)의 값 = 2분기 단독.
    ※ Q4 는 별도 보고서 없음 → FY - (Q1+Q2+Q3) 로 계산.
    """
    q1 = get_eps(code, year, "Q1", fs)
    q2 = get_eps(code, year, "H1", fs)
    q3 = get_eps(code, year, "Q3", fs)
    fy = get_eps(code, year, "FY", fs)
    q4 = None
    if None not in (q1, q2, q3, fy):
        q4 = round(fy - (q1 + q2 + q3), 1)
    return {"year": year, "Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "FY": fy}


def get_company(code: str) -> dict:
    """종목코드 → 기업개황 dict (대표자·설립일·주소·업종코드 등).
    실패 시 빈 dict."""
    try:
        comp = _get_dart().company(str(code))
        if comp.get("status") != "000":
            return {}
        return dict(comp)
    except Exception as e:
        print(f"[dart] get_company({code}) 실패: {e}")
        return {}


def get_financials(code: str, year: int, reprt: str = "11011"):
    """종목코드 → 재무제표 DataFrame (KIS 교차검증용).
    year: 사업연도(예 2023). reprt: 11011=사업보고서(연간).
    실패 시 None."""
    try:
        return _get_dart().finstate(str(code), year, reprt_code=reprt)
    except Exception as e:
        print(f"[dart] get_financials({code}, {year}) 실패: {e}")
        return None




# 계정명 → dict 키 매핑 (BS: 재무상태표, IS: 손익계산서)
_ACCOUNTS = {
    "자산총계": "assets", "부채총계": "liabilities", "자본총계": "equity",
    "유동자산": "current_assets", "유동부채": "current_liabilities",
    "이익잉여금": "retained_earnings",
    "매출액": "revenue", "영업이익": "operating_profit",
    "당기순이익(손실)": "net_income",
}


def _pick_amount(df, account_nm, fs_div):
    """특정 계정의 당기금액을 float으로. 없으면 None."""
    m = df[(df["account_nm"] == account_nm) & (df["fs_div"] == fs_div)]
    if len(m) == 0:
        return None
    v = str(m.iloc[0]["thstrm_amount"]).replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def get_financials_full(code: str, year: int, fs: str = "CFS",
                        reprt: str = "11011") -> Optional[dict]:
    """형식2: 재무제표 원천값 전체 + 주요 비율 계산.
    fs: CFS(연결, 기본) / OFS(별도).
    반환: {symbol, year, fs_type, raw:{계정→금액}, debt_ratio, roe,
           op_margin, net_margin}. 실패 시 None.
    KIS get_financials 와 교차검증·결측보완 목적."""
    df = get_financials(code, year, reprt)
    if df is None or len(df) == 0:
        return None
    # fs_div 값 확인 (해당 구분 없으면 있는 것으로 폴백)
    have = set(df["fs_div"].unique()) if "fs_div" in df.columns else set()
    if fs not in have:
        fs = "CFS" if "CFS" in have else ("OFS" if "OFS" in have else fs)

    raw = {}
    for acc_nm, key in _ACCOUNTS.items():
        val = _pick_amount(df, acc_nm, fs)
        if val is not None:
            raw[key] = val

    def _ratio(num, den):
        if raw.get(num) is not None and raw.get(den):
            return round(raw[num] / raw[den] * 100, 2)
        return None

    return {
        "symbol": str(code),
        "year": year,
        "fs_type": fs,
        "raw": raw,
        "debt_ratio": _ratio("liabilities", "equity"),
        "roe": _ratio("net_income", "equity"),
        "op_margin": _ratio("operating_profit", "revenue"),
        "net_margin": _ratio("net_income", "revenue"),
    }


if __name__ == "__main__":
    print("업종:", get_industry("005930"))
    c = get_company("005930")
    print("회사명:", c.get("corp_name"), "/ 설립:", c.get("est_dt"))
    fs = get_financials("005930", 2023)
    print("재무 항목 수:", len(fs) if fs is not None else 0)
