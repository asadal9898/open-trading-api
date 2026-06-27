"""
국내 종목/ETF 마스터(kospi_code.mst) 검색 — 채권 ETF 등 종목 코드 찾기.

KIS 공식 kis_kospi_code_mst.py 의 파싱을 리눅스용으로 정리 + 검색 기능 추가.
universe.yaml 에 넣을 국내 종목/ETF 의 단축코드(6자리)를 찾는 용도.

ETF·종목 모두 코스피 마스터(kospi_code.mst)에 있음. 코스닥 종목은 --kosdaq.

실행:
    uv run python mytrading/find_stock_code.py 채권          # 키워드 검색
    uv run python mytrading/find_stock_code.py 국고채 회사채   # 여러 키워드(OR)
    uv run python mytrading/find_stock_code.py TIGER 미국채    # ETF 브랜드+종류
    uv run python mytrading/find_stock_code.py --kosdaq 바이오  # 코스닥에서 검색
    uv run python mytrading/find_stock_code.py --etf 채권       # ETF로 보이는 것만(이름에 ETF브랜드)
    uv run python mytrading/find_stock_code.py --vol 114100 114260 114460  # 코드들 거래량 비교
"""
import sys
import urllib.request
import ssl
import zipfile
from pathlib import Path

_CACHE = Path("/tmp/krx_code")
_URLS = {
    "kospi":  "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "kosdaq": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
}
# 국내 ETF 주요 운용사 브랜드 (이름 앞에 붙음) — --etf 필터용
_ETF_BRANDS = ["KODEX", "TIGER", "KBSTAR", "ARIRANG", "KOSEF", "HANARO",
               "KINDEX", "SOL", "ACE", "PLUS", "RISE", "WON", "TIMEFOLIO",
               "히어로즈", "마이티", "FOCUS", "BNK", "KIWOOM", "마이다스"]


def download_master(market: str) -> Path:
    """kospi/kosdaq 마스터 다운로드 + 압축 해제. mst 파일 경로 반환."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    zip_path = _CACHE / f"{market}_code.zip"
    mst_path = _CACHE / f"{market}_code.mst"

    if not mst_path.exists():
        print(f"{market} 종목 마스터 다운로드 중...")
        ssl._create_default_https_context = ssl._create_unverified_context
        urllib.request.urlretrieve(_URLS[market], str(zip_path))
        with zipfile.ZipFile(str(zip_path)) as z:
            z.extractall(str(_CACHE))
        print(f"  완료: {mst_path}")
    return mst_path


def parse_master(mst_path: Path):
    """mst 파싱 → [(단축코드, 표준코드, 한글명)].

    kis_kospi_code_mst.py 와 동일한 레이아웃:
      row[0:9]   = 단축코드 (앞 6자리가 종목코드)
      row[9:21]  = 표준코드
      row[21:-228] = 한글명 (뒤 228바이트는 상세 필드 — 검색엔 불필요)
    """
    rows = []
    with open(mst_path, mode="r", encoding="cp949") as f:
        for row in f:
            rf1 = row[0:len(row) - 228]
            short = rf1[0:9].rstrip()
            std = rf1[9:21].rstrip()
            name = rf1[21:].strip()
            # 단축코드는 보통 'A005930' 형태 → 앞 A 제거하고 6자리만
            code6 = short[1:] if short[:1].isalpha() else short
            rows.append((code6, std, name))
    return rows


def _name_map(market: str = "kospi") -> dict:
    """코드 → 종목명 매핑 (거래량 출력 시 이름 표시용). 코스피+코스닥 합침."""
    names = {}
    for mkt in ("kospi", "kosdaq"):
        try:
            for code6, _std, name in parse_master(download_master(mkt)):
                names.setdefault(code6, name)
        except Exception:
            pass
    return names


def _market_cap(code: str):
    """inquire_price(FHKST01010100)로 시가총액(억) 조회. 실패 시 None.
    ETF·주식 모두 hts_avls 필드에 시가총액(억원)이 들어옴."""
    import importlib
    _repo = Path(__file__).resolve().parents[1]
    ip_dir = _repo / "examples_llm" / "domestic_stock" / "inquire_price"
    if str(ip_dir) not in sys.path:
        sys.path.insert(0, str(ip_dir))
    try:
        ip = importlib.import_module("inquire_price")
        df = ip.inquire_price(env_dv="demo", fid_cond_mrkt_div_code="J",
                              fid_input_iscd=code)
        if df is not None and not df.empty:
            val = df.iloc[0].get("hts_avls", None)
            return int(val) if val not in (None, "", "?") else None
    except Exception:
        return None
    return None


def show_volume(codes: list, days: int = 10):
    """주어진 코드들의 최근 거래량·종가·시가총액 비교 출력.

    거래량: KISDataProvider.get_history → List[Bar]
    시가총액: inquire_price(FHKST01010100)의 hts_avls(억원)
    자동매매는 유동성이 중요 — 수수료 싸도 거래량 적으면 호가 손실 큼.
    """
    import time
    from datetime import date, timedelta
    # mytrading 패키지 import 가능하게 경로 추가
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from mytrading.common import init, get_data_provider

    init(require_confirm=False)
    dp = get_data_provider()
    names = _name_map()

    end = date.today()
    start = end - timedelta(days=days)
    print(f"{'코드':8} {'종목명':22} {'종가':>10} {'거래량':>12} {'시가총액':>12}")
    print("-" * 70)
    rows = []
    for code in codes:
        nm = names.get(code, "?")
        try:
            bars = dp.get_history(code, start, end)
            time.sleep(0.6)  # 레이트리밋 방지
            cap = _market_cap(code)
            time.sleep(0.6)
            if bars:
                b = bars[-1]
                vol = int(getattr(b, "volume", 0) or 0)
                close = getattr(b, "close", 0)
                cap_s = f"{cap:,}억" if cap is not None else "?"
                print(f"{code:8} {nm:22} {close:>10,.0f} {vol:>12,} {cap_s:>12}")
                rows.append((code, nm, vol, cap))
            else:
                print(f"{code:8} {nm:22} {'데이터 없음':>25}")
        except Exception as e:
            print(f"{code:8} {nm:22} 오류: {str(e)[:30]}")
    if rows:
        rows.sort(key=lambda r: r[2], reverse=True)
        print(f"\n거래량 많은 순: {' > '.join(f'{nm}({v:,})' for _c, nm, v, _cap in rows)}")
        print("※ 자동매매는 거래량 충분한 종목이 유리 (호가 스프레드 손실↓).")
        print("  시가총액(순자산 규모)도 클수록 안정. 수수료(총보수)는 증권사 ETF 정보서 별도 확인.")


def main():
    args = sys.argv[1:]

    # --vol: 거래량 비교 모드 (코드들을 인자로)
    if "--vol" in args:
        codes = [a for a in args if not a.startswith("--")]
        if not codes:
            print("사용: find_stock_code.py --vol 114100 114260 114460")
            return
        show_volume(codes)
        return

    market = "kosdaq" if "--kosdaq" in args else "kospi"
    etf_only = "--etf" in args
    keywords = [a for a in args if not a.startswith("--")]

    mst = download_master(market)
    rows = parse_master(mst)
    print(f"총 {len(rows)}개 종목 ({market})\n")

    if not keywords:
        print("키워드를 입력하세요. 예: find_stock_code.py 채권 국고채")
        print("옵션: --kosdaq(코스닥), --etf(ETF브랜드만), --vol(거래량비교)")
        return

    print(f"=== 검색: {', '.join(keywords)}{'  [ETF만]' if etf_only else ''} ===")
    found = 0
    for code6, std, name in rows:
        up = name.upper()
        if not any(k.upper() in up for k in keywords):
            continue
        if etf_only and not any(b.upper() in up for b in _ETF_BRANDS):
            continue
        print(f"  {code6}  | {name}")
        found += 1
    print(f"\n{found}개 발견")
    print("\n※ 6자리 코드를 universe.yaml 의 code 에 넣으세요.")
    print("  거래량 비교: find_stock_code.py --vol 코드1 코드2 ...")
    print("  (자동매매에 쓰려면 반드시 백테스트 검증 후 추가)")


if __name__ == "__main__":
    main()