"""
거시 지수 수집 — 해외/국내 주요 지수 일봉을 받아 CSV 캐시에 저장.

시장 국면 판단(market_regime)의 참고 데이터.
⚠️ 자동 매매 신호가 아니라, Owner 가 국면을 판단할 때 보는 참고 자료.

데이터 소스: KIS inquire_daily_chartprice (해외주식-012, FHKST03030100)
  - fid_cond_mrkt_div_code="N"(해외지수), env_dv="demo"(모의 가능 — 토큰충돌 없음)
  - 응답 output2: stck_bsop_date(날짜), ovrs_nmix_prpr(종가),
                  ovrs_nmix_oprc(시가), ovrs_nmix_hgpr(고가), ovrs_nmix_lwpr(저가), acml_vol(거래량)
  - 이 API 하나로 지수(N)/환율(X)/국채금리(I)/금선물(S) 다 받음 (확장 가능)

저장: backtester/.lean-workspace/data/index/{key}.csv
      형식: YYYYMMDD,open,high,low,close,volume  (기존 종목 일봉과 동일)

지수 코드 (검증 완료 / KIS 해외지수):
  한국: KOSPI
  미국: SPX(S&P500) COMP(나스닥종합) NDX(나스닥100)  ※ 다우는 가격가중이라 제외
  일본·중국: 코드 미확보 — KIS 해외지수 마스터/문의로 확보 후 INDICES 에 추가

사용:
    from mytrading.index_data import ensure_index_data, INDICES
    ensure_index_data("kospi", start="20200101")   # 하나
    ensure_all_indices(start="20200101")            # 전부

CLI:
    uv run python mytrading/index_data.py                 # 전체 수집
    uv run python mytrading/index_data.py kospi sp500     # 일부만
"""
import sys
import time
from pathlib import Path
from datetime import datetime, date
from typing import Optional, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INDEX_DIR = _REPO_ROOT / "backtester" / ".lean-workspace" / "data" / "index"

# 지수 목록 (key: 코드/시장구분/이름). 일본·중국은 코드 확보 후 추가.
# 다우(.DJI)는 가격가중 방식이라 시총가중인 S&P500/나스닥과 산출이 달라
# 국면 분석을 왜곡할 수 있어 제외. 시총가중 지수만 사용.
INDICES = {
    "kospi":     {"code": "KOSPI", "market": "N", "name": "코스피"},
    "sp500":     {"code": "SPX",   "market": "N", "name": "S&P500"},
    "nasdaq":    {"code": "COMP",  "market": "N", "name": "나스닥종합"},
    "nasdaq100": {"code": "NDX",   "market": "N", "name": "나스닥100"},
    # "nikkei":  {"code": "???",   "market": "N", "name": "니케이225"},   # 코드 확보 후
    # "shanghai":{"code": "???",   "market": "N", "name": "상해종합"},    # 코드 확보 후
}

_API_DIR = _REPO_ROOT / "examples_llm" / "overseas_stock" / "inquire_daily_chartprice"

# 수집 기본 시작일 (지수마다 실제 데이터 시작은 다름 — 빈 구간은 자동 스킵)
#   코스피 1990~ / 나스닥 2000~ / S&P500 2010~
DEFAULT_START = "19900101"

# 레이트리밋(EGW00201) 대응 — 충분히 느리게, 실패 시 점점 더 기다려 재시도
_SEG_SLEEP = 2.0       # 매 호출 전 기본 대기(초)
_MAX_RETRY = 4         # 빈/실패 응답 시 재시도 횟수
_RETRY_BACKOFF = 2.0   # 재시도마다 추가로 더 기다리는 시간(초)
_INDEX_GAP = 3.0       # 지수 전환 사이 추가 대기(초)


def _load_api():
    """KIS 해외지수 일봉 API 함수 로드 (examples_llm)."""
    if str(_API_DIR) not in sys.path:
        sys.path.insert(0, str(_API_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from inquire_daily_chartprice import inquire_daily_chartprice
    return inquire_daily_chartprice


def _cache_path(key: str) -> Path:
    return _INDEX_DIR / f"{key}.csv"


def _last_date_in_csv(path: Path) -> Optional[str]:
    """CSV 마지막 줄의 날짜(YYYYMMDD). 없으면 None."""
    if not path.exists():
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last = line.split(",")[0]
    return last


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


def ensure_index_data(key: str, start: str = DEFAULT_START,
                      end: Optional[str] = None, sleep: float = _SEG_SLEEP) -> int:
    """
    지수 하나의 일봉을 받아 CSV 저장 (증분: 기존 마지막 날짜 다음부터).
    반환: 새로 저장한 행 수.
    """
    if key not in INDICES:
        raise ValueError(f"알 수 없는 지수 key: {key} (가능: {list(INDICES)})")
    info = INDICES[key]
    end = end or _today_str()

    path = _cache_path(key)
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # 증분: 기존 마지막 날짜 다음날부터
    last = _last_date_in_csv(path)
    fetch_start = start
    if last:
        # 마지막 날짜 다음날 (간단히 +1일; 주말/휴장은 API가 알아서 비움)
        d = datetime.strptime(last, "%Y%m%d")
        from datetime import timedelta
        fetch_start = (d + timedelta(days=1)).strftime("%Y%m%d")
        if fetch_start > end:
            print(f"  - {info['name']}({key}): 최신 ({last}) — 받을 것 없음")
            return 0

    api = _load_api()

    # KIS 해외지수 API 는 한 번에 ~100건만 주고 연속조회 헤더를 안 준다.
    # → 기간을 90일(약 60거래일)씩 끊어 여러 번 호출해 합친다.
    # 레이트리밋(EGW00201 "초당 거래건수 초과")이 잦으므로:
    #   - 매 호출 전 sleep (기본 2.0초)
    #   - 빈 응답이면 레이트리밋일 수 있으니 점점 더 기다리며 재시도(backoff)
    from datetime import timedelta
    all_rows = {}  # date -> (o,h,low,c,v) 중복 자동 제거
    seg_start_dt = datetime.strptime(fetch_start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")

    def _fetch_segment(s1: str, s2: str):
        """한 구간 조회. 레이트리밋이면 backoff 재시도. (있으면 df, 없으면 None)"""
        for attempt in range(_MAX_RETRY):
            time.sleep(sleep + attempt * _RETRY_BACKOFF)  # 재시도마다 더 길게
            try:
                _d1, d2 = api(
                    fid_cond_mrkt_div_code=info["market"],
                    fid_input_iscd=info["code"],
                    fid_input_date_1=s1,
                    fid_input_date_2=s2,
                    fid_period_div_code="D",
                    env_dv="demo",
                )
            except Exception as e:
                msg = str(e)
                if "EGW00201" in msg or "초당" in msg:
                    # 레이트리밋 예외 → 더 기다렸다 재시도
                    print(f"      (레이트리밋, {attempt+1}/{_MAX_RETRY} 재시도 대기...)")
                    continue
                # 다른 예외는 재시도 의미 없음
                print(f"  - {info['name']}({key}): {s1}~{s2} 조회 실패 — {msg[:50]}")
                return None
            # 정상 응답이지만 빈 경우: 그 기간에 데이터가 없거나(옛날) 레이트리밋이
            # 조용히 빈 응답으로 온 경우. 데이터가 있는 시기인데 비면 재시도해본다.
            if d2 is not None and not d2.empty:
                return d2
            # 빈 응답 — 마지막 시도가 아니면 한 번 더(레이트리밋 가능성)
            if attempt < _MAX_RETRY - 1:
                continue
            return None  # 끝까지 비면 진짜 데이터 없는 구간
        return None

    seg_count = 0
    while seg_start_dt <= end_dt:
        seg_end_dt = min(seg_start_dt + timedelta(days=90), end_dt)
        s1 = seg_start_dt.strftime("%Y%m%d")
        s2 = seg_end_dt.strftime("%Y%m%d")
        seg_count += 1

        d2 = _fetch_segment(s1, s2)
        if d2 is not None and not d2.empty:
            for _, r in d2.iterrows():
                d = str(r.get("stck_bsop_date", "")).strip()
                if not d:
                    continue
                all_rows[d] = (
                    r.get("ovrs_nmix_oprc", ""),
                    r.get("ovrs_nmix_hgpr", ""),
                    r.get("ovrs_nmix_lwpr", ""),
                    r.get("ovrs_nmix_prpr", ""),
                    r.get("acml_vol", ""),
                )
            # 진행 표시 (긴 수집이라 살아있음을 보여줌)
            if seg_count % 10 == 0:
                print(f"      ... {info['name']} {s2[:4]}년 구간까지 누적 {len(all_rows)}건")
        seg_start_dt = seg_end_dt + timedelta(days=1)

    if not all_rows:
        print(f"  - {info['name']}({key}): 데이터 없음 (기간 {fetch_start}~{end})")
        return 0

    # 날짜 오름차순 정리
    rows = [(d, *all_rows[d]) for d in sorted(all_rows.keys())]

    # 기존 마지막 날짜 이하(중복)는 제외하고 append
    written = 0
    mode = "a" if path.exists() else "w"
    with open(path, mode, encoding="utf-8") as f:
        for (d, o, h, low, c, v) in rows:
            if last and d <= last:
                continue
            f.write(f"{d},{o},{h},{low},{c},{v}\n")
            written += 1

    print(f"  - {info['name']}({key}): {written}건 저장 → {key}.csv "
          f"(최신 {rows[-1][0]} 종가 {rows[-1][4]})")
    return written


def ensure_all_indices(start: str = DEFAULT_START, keys: Optional[List[str]] = None) -> None:
    """여러 지수 일괄 수집."""
    keys = keys or list(INDICES.keys())
    print(f"지수 수집: {len(keys)}개 (start={start})")
    print(f"  ⏳ 레이트리밋 방지를 위해 천천히 받습니다 (구간당 {_SEG_SLEEP}s+, 시간이 걸립니다)")
    total = 0
    for i, k in enumerate(keys):
        if i > 0:
            time.sleep(_INDEX_GAP)  # 지수 전환 사이 추가 대기
        total += ensure_index_data(k, start=start)
    print(f"지수 수집 완료 (총 {total}건 신규)")


if __name__ == "__main__":
    # 직접 실행 시 mytrading 패키지를 찾도록 repo 루트를 path 에 추가
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from mytrading.common import init
    args = sys.argv[1:]
    keys = [a for a in args if a in INDICES] or None
    if args and not keys:
        print(f"알 수 없는 지수. 가능: {list(INDICES)}")
        sys.exit(1)
    init(require_confirm=False)  # 모의 인증 (demo 조회용)
    ensure_all_indices(keys=keys)