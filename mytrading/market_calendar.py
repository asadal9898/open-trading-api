"""
휴장일/개장일 확인 모듈
원본 examples_llm 의 chk_holiday(국내휴장일조회 CTCA0903R)를 감싸서
"오늘(또는 특정일) 장이 열리는가"를 간단히 확인한다.

⚠️ KIS 주의: 국내휴장일조회는 원장서비스 연관이라 1일 1회 호출 권장.
   → 이 모듈은 조회 결과를 당일 파일 캐시에 저장해 중복 호출을 막는다.

[휴장일 소스 선택 — 검토 결과(2026-06)]
  - 국내: KIS API(chk_holiday) 사용. pandas-market-calendars(XKRX)로 통일을
    검토했으나, XKRX 가 제헌절(7/17, 법정공휴일 아니지만 증시는 휴장) 등
    한국 증시 고유 휴장을 놓침. KIS 가 더 정확하므로 국내는 KIS 유지.
    (토큰 충돌은 common 의 모드전환 토큰무효화 + 1일1회 캐시로 완화됨)
  - 해외(미국/일본): pandas-market-calendars(NYSE/JPX) 사용. KIS 의
    countries-holiday 는 휴장일이 아니라 '결제일자' 조회라 부적합.
    market-calendars 가 거래소 실제 휴장(독립기념일 대체휴장 등)을 정확히 잡음.
  → 시장별로 가장 정확한 소스를 쓴다 (통일보다 정확도 우선).

사용:
    from mytrading.market_calendar import is_market_open, next_holidays
    if is_market_open():          # 오늘 개장일인가?
        ...                       # 주문 진행
    holidays = next_holidays(14)  # 향후 14일 중 휴장일 리스트

CLI:
    uv run python mytrading/market_calendar.py            # 오늘 개장 여부
    uv run python mytrading/market_calendar.py 20260626   # 특정일
    uv run python mytrading/market_calendar.py --next 14  # 향후 N일 휴장일
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 원본 chk_holiday 가 있는 경로를 sys.path 에 추가 (원본 수정 없이 import)
_ROOT = Path(__file__).resolve().parents[1]
_CHK_DIR = _ROOT / "examples_llm" / "domestic_stock" / "chk_holiday"
if str(_CHK_DIR) not in sys.path:
    sys.path.insert(0, str(_CHK_DIR))

# 당일 1회 호출 캐시 (저장소 밖, ~/KIS/cache)
_CACHE_DIR = Path.home() / "KIS" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _cache_path(bass_dt: str) -> Path:
    return _CACHE_DIR / f"holiday_{bass_dt}.json"


def _fetch_holiday_df(bass_dt: str):
    """
    원본 chk_holiday 호출.
    ⚠️ 휴장일조회(CTCA0903R)는 실전 서버에만 있는 서비스 (모의 서버엔 없음).
       → 실전 계좌 키로 인증해서 조회한다. (주문이 아니라 시장 달력 조회라 안전)

    토큰 충돌 회피: kis_auth 는 모의/실전이 같은 날짜 파일(KIS{YYYYMMDD})을 공유해서
    모의 토큰이 남아있으면 실전 인증이 "만료/무효" 에러를 낸다.
    → 실전 인증 직전에 토큰 캐시를 비워 강제 재발급시킨다. (원본 파일은 비우기만)
    """
    import pandas as pd
    from chk_holiday import chk_holiday
    try:
        import kis_auth as ka
        from mytrading.common import _resolve_account, _inject_auth_cfg, resolve_mode

        # 호출 전 원래 모드 기억 (휴장일 조회는 실전 서버를 잠깐 빌려 씀 → 끝나면 반납)
        original_mode = resolve_mode()  # "vps" or "prod"

        # 실전(prod) 계좌 키 주입 후 실전 서버 인증
        # (토큰 모드 충돌은 _inject_auth_cfg 가 모드 전환 감지해 자동 처리)
        acc = _resolve_account(is_paper=False)
        _inject_auth_cfg(acc, is_paper=False)
        ka.auth(svr="prod")
    except Exception as e:
        print(f"[market_calendar] 실전 인증 실패: {e}")
        print("  (휴장일 조회는 실전 앱키가 필요합니다. 일반증권 my_app/my_sec 확인)")
        return pd.DataFrame()

    try:
        # max_depth 넉넉히 (1년치 ~264건을 경고 없이 받아 당일 캐시 → 재호출 없음)
        return chk_holiday(bass_dt=bass_dt, max_depth=20)
    finally:
        # 원래 모드가 모의(vps)였으면 모의로 복원 (실전 모드 잔류로 인한
        # 후속 계좌조회 EGW02005 '실전 TR이 아닙니다' 방지)
        if original_mode == "vps":
            try:
                back_acc = _resolve_account(is_paper=True)
                _inject_auth_cfg(back_acc, is_paper=True)
                ka.auth(svr="vps")
            except Exception as e:
                print(f"[market_calendar] 모의 모드 복원 실패(무시): {e}")


def get_calendar(bass_dt: str = None, use_cache: bool = True) -> list:
    """
    기준일부터의 휴장일 정보 리스트 반환 (1일 1회 호출 권장 → 캐시 사용).
    반환: [{date, opnd_yn, bzdy_yn, tr_day_yn, sttl_day_yn}, ...]
    """
    bass_dt = bass_dt or _today_str()
    cache_file = _cache_path(bass_dt)

    # 캐시 확인 (같은 기준일이면 재호출 안 함)
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    df = _fetch_holiday_df(bass_dt)
    records = []
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            records.append({
                "date": str(row.get("bass_dt", "")),
                "opnd_yn": str(row.get("opnd_yn", "")),      # 개장일 Y/N
                "bzdy_yn": str(row.get("bzdy_yn", "")),      # 영업일
                "tr_day_yn": str(row.get("tr_day_yn", "")),  # 거래일
                "sttl_day_yn": str(row.get("sttl_day_yn", "")),  # 결제일
            })
    # 캐시 저장
    try:
        cache_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return records


def is_market_open(date_str: str = None) -> bool:
    """특정일(기본 오늘)이 개장일인가? opnd_yn == 'Y'."""
    target = date_str or _today_str()
    cal = get_calendar(target)
    for rec in cal:
        if rec["date"] == target:
            return rec["opnd_yn"] == "Y"
    # 응답에 해당일이 없으면 보수적으로 False (모르면 주문 안 함)
    return False


def next_holidays(days: int = 14, from_date: str = None) -> list:
    """
    기준일부터 향후 N일 중 휴장일(개장 안 하는 날) 리스트.
    반환: [{date, weekday}, ...]  (주말 포함 — 개장 안 하는 모든 날)
    """
    start = datetime.strptime(from_date, "%Y%m%d") if from_date else datetime.now()
    cal = get_calendar(start.strftime("%Y%m%d"))
    cal_map = {rec["date"]: rec for rec in cal}

    wd_kr = ["월", "화", "수", "목", "금", "토", "일"]
    holidays = []
    for i in range(days):
        d = start + timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        rec = cal_map.get(ds)
        # 캘린더에 있으면 opnd_yn 기준, 없으면 주말 여부로 판단
        if rec is not None:
            is_open = rec["opnd_yn"] == "Y"
        else:
            is_open = d.weekday() < 5  # 주중이면 개장 가정 (캘린더 없을 때 폴백)
        if not is_open:
            holidays.append({"date": ds, "weekday": wd_kr[d.weekday()]})
    return holidays


def next_week_holidays(from_date: str = None) -> list:
    """
    '다음 주' 월~금 중 휴장(공휴일)만 반환. 주말(토/일)은 제외.
    - 기준일이 속한 주의 '다음 주 월요일'부터 그 주 금요일까지 확인
    - 그 평일 중 개장 안 하는 날(opnd_yn=N)만 = 공휴일
    반환: [{date, weekday}, ...]  (없으면 빈 리스트)
    """
    base = datetime.strptime(from_date, "%Y%m%d") if from_date else datetime.now()
    # 이번 주 월요일 = base - base.weekday()일, 다음 주 월요일 = +7일
    this_monday = base - timedelta(days=base.weekday())
    next_monday = this_monday + timedelta(days=7)

    cal = get_calendar(base.strftime("%Y%m%d"))
    cal_map = {rec["date"]: rec for rec in cal}

    wd_kr = ["월", "화", "수", "목", "금", "토", "일"]
    holidays = []
    for i in range(5):  # 월~금만
        d = next_monday + timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        rec = cal_map.get(ds)
        if rec is not None:
            is_open = rec["opnd_yn"] == "Y"
        else:
            is_open = True  # 캘린더에 없으면 개장으로 간주 (평일이므로)
        if not is_open:  # 평일인데 개장 안 함 = 공휴일
            holidays.append({"date": ds, "weekday": wd_kr[d.weekday()]})
    return holidays


# 거래소 코드 (pandas-market-calendars)
_EXCHANGE = {
    "us": "NYSE",     # 미국 (뉴욕증권거래소)
    "japan": "JPX",   # 일본 (일본거래소)
    "china": "SSE",    # ← 추가 (상해증권거래소)
    "korea": "XKRX",  # 한국 (참고용)
}


def overseas_next_week_holidays(market: str, from_date: str = None) -> list:
    """
    해외 거래소의 '다음 주' 월~금 휴장일 (주말 제외).
    pandas-market-calendars 의 개장일 스케줄을 받아, 평일 중 개장 안 하는 날을 휴장으로 판단.

    market: "us"(NYSE) / "japan"(JPX) / "korea"(XKRX)
    반환: [{date(YYYYMMDD), weekday}, ...]  (없으면 빈 리스트)
    """
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        return []  # 라이브러리 없으면 빈 결과 (설정 꺼두면 됨)

    code = _EXCHANGE.get(market)
    if not code:
        return []

    base = datetime.strptime(from_date, "%Y%m%d") if from_date else datetime.now()
    this_monday = base - timedelta(days=base.weekday())
    next_monday = this_monday + timedelta(days=7)
    next_friday = next_monday + timedelta(days=4)

    try:
        cal = mcal.get_calendar(code)
        sched = cal.schedule(start_date=next_monday.strftime("%Y-%m-%d"),
                             end_date=next_friday.strftime("%Y-%m-%d"))
        # 개장일 집합 (YYYYMMDD)
        open_days = set(sched.index.strftime("%Y%m%d").tolist())
    except Exception:
        return []

    wd_kr = ["월", "화", "수", "목", "금", "토", "일"]
    holidays = []
    for i in range(5):  # 월~금
        d = next_monday + timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        if ds not in open_days:  # 평일인데 개장일 목록에 없음 = 휴장
            holidays.append({"date": ds, "weekday": wd_kr[d.weekday()]})
    return holidays


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--next":
        n = int(args[1]) if len(args) > 1 else 14
        hs = next_holidays(n)
        print(f"향후 {n}일 휴장일:")
        for h in hs:
            print(f"  {h['date']} ({h['weekday']})")
        if not hs:
            print("  (없음)")
    else:
        target = args[0] if args else _today_str()
        cal = get_calendar(target)
        print(f"기준일 {target} 캘린더 ({len(cal)}건):")
        for rec in cal[:10]:
            mark = "🟢개장" if rec["opnd_yn"] == "Y" else "🔴휴장"
            print(f"  {rec['date']}: {mark} (영업일 {rec['bzdy_yn']}, 거래일 {rec['tr_day_yn']})")
        print(f"\n오늘 개장 여부: {is_market_open(target)}")