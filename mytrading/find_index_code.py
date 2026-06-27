"""
해외지수 마스터(frgn_code.mst) 파싱 — 일본·중국·금리 등 지수 코드 찾기.

KIS 공식 overseas_index_code.py 를 리눅스용으로 수정 + 검색 기능 추가.
inquire_daily_chartprice 에 쓸 지수 코드(FID_INPUT_ISCD)를 찾는 용도.

실행:
    uv run python mytrading/find_index_code.py            # 일본·중국 등 주요 지수 출력
    uv run python mytrading/find_index_code.py 니케이      # 키워드 검색
    uv run python mytrading/find_index_code.py --all       # 전체 덤프
"""
import sys
import urllib.request
import ssl
import zipfile
from pathlib import Path

_CACHE = Path("/tmp/frgn_code")
_MST_URL = "https://new.real.download.dws.co.kr/common/master/frgn_code.mst.zip"


def download_master() -> Path:
    """마스터 파일 다운로드 + 압축 해제. mst 파일 경로 반환."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    zip_path = _CACHE / "frgn_code.mst.zip"
    mst_path = _CACHE / "frgn_code.mst"

    if not mst_path.exists():
        print("해외지수 마스터 다운로드 중...")
        ssl._create_default_https_context = ssl._create_unverified_context
        urllib.request.urlretrieve(_MST_URL, str(zip_path))
        with zipfile.ZipFile(str(zip_path)) as z:
            z.extractall(str(_CACHE))
        print(f"  완료: {mst_path}")
    return mst_path


def parse_master(mst_path: Path):
    """mst 파싱 → [(구분코드, 심볼, 영문명, 한글명)]."""
    rows = []
    with open(mst_path, mode="r", encoding="cp949") as f:
        for row in f:
            rf1 = row[0:len(row) - 14]
            gubun = rf1[0:1]
            symbol = rf1[1:11].strip()
            if row[0:1] == 'X':
                eng = rf1[11:40].replace(",", "").strip()
                kor = rf1[40:80].replace(",", "").strip()
            else:
                eng = rf1[11:50].replace(",", "").strip()
                kor = row[50:75].replace(",", "").strip()
            rows.append((gubun, symbol, eng, kor))
    return rows


def main():
    args = sys.argv[1:]
    mst = download_master()
    rows = parse_master(mst)
    print(f"총 {len(rows)}개 항목\n")

    if "--all" in args:
        for g, sym, eng, kor in rows:
            print(f"  {g} | {sym:12} | {eng[:35]:35} | {kor}")
        return

    # 키워드: 인자로 받거나, 기본(주요 지수)
    if args:
        keywords = [a for a in args if not a.startswith("--")]
    else:
        keywords = ["NIKKEI", "NI225", "N225", "일본", "니케이", "TOKYO", "도쿄",
                    "SHANGHAI", "상해", "SHENZHEN", "심천", "CSI", "CHINA", "중국",
                    "HANG", "홍콩", "HONG", "KOSPI", "코스피", "SPX", "NASDAQ", "나스닥",
                    "DOW", "다우", "S&P"]

    print(f"=== 검색: {', '.join(keywords)} ===")
    found = 0
    for g, sym, eng, kor in rows:
        text = (eng + " " + kor).upper()
        if any(k.upper() in text for k in keywords):
            print(f"  심볼 {sym:12} | {eng[:35]:35} | {kor}")
            found += 1
    print(f"\n{found}개 발견")
    print("\n※ '심볼' 값을 inquire_daily_chartprice 의 fid_input_iscd 에 넣어 테스트하세요.")
    print("  (시장구분 N=지수. 점(.) 포함 여부는 심볼 그대로 시도)")


if __name__ == "__main__":
    main()