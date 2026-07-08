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
