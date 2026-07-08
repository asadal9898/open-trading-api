"""
재무 데이터 통합 계층 — KIS 우선, DART 폴백/보완.
KIS(finance_data)와 DART(dart_data)를 조합하는 상위 모듈.

주목적: 결측 보완. KIS 재무 API 가 안 되거나(모의 vps) 빈 값일 때 DART 로 채운다.
부목적: 교차검증. 두 출처 원본을 나란히 보존해 나중에 비교 가능.

형식2 반환:
    {
      "symbol", "source",          # "KIS" / "DART" / "KIS+DART" / None
      "debt_ratio", "roe",         # 공통 지표 (KIS 우선, 없으면 DART)
      "kis":  {...} | None,        # KIS 원본 (finance_data.get_financials)
      "dart": {...} | None,        # DART 원본 (dart_data.get_financials_full)
    }

사용:
    from mytrading.finance_unified import get_financials_safe
    r = get_financials_safe("005930")
    print(r["debt_ratio"], r["source"])
"""
import datetime
from typing import Optional


def _try_kis(code: str, init_kis: bool) -> Optional[dict]:
    """KIS 재무 조회. init 필요(실전 전용). 실패 시 None."""
    try:
        if init_kis:
            from mytrading.common import init
            init(require_confirm=False)
        from mytrading.finance_data import get_financials
        return get_financials(str(code))
    except Exception as e:
        print(f"[unified] KIS 조회 실패: {e}")
        return None


def _try_dart(code: str) -> Optional[dict]:
    """DART 재무 조회. 최근 연도부터 역순으로 데이터 있는 첫 해를 사용."""
    try:
        from mytrading.dart_data import get_financials_full
        this_year = datetime.date.today().year
        for y in range(this_year - 1, this_year - 4, -1):
            r = get_financials_full(str(code), y)
            if r is not None and r.get("debt_ratio") is not None:
                return r
        return None
    except Exception as e:
        print(f"[unified] DART 조회 실패: {e}")
        return None


def get_financials_safe(code: str, init_kis: bool = True) -> Optional[dict]:
    """재무 통합 조회. KIS 우선, 결측 시 DART 보완.
    init_kis=False 면 KIS init 을 건너뜀(이미 init 된 컨텍스트에서 호출 시).
    둘 다 실패하면 None."""
    kis = _try_kis(code, init_kis)
    dart = None

    debt_ratio = kis.get("debt_ratio") if kis else None
    roe = kis.get("roe") if kis else None

    need_dart = (kis is None) or (debt_ratio is None) or (roe is None)
    if need_dart:
        dart = _try_dart(code)
        if dart:
            if debt_ratio is None:
                debt_ratio = dart.get("debt_ratio")
            if roe is None:
                roe = dart.get("roe")

    if kis is None and dart is None:
        return None

    if kis and dart:
        source = "KIS+DART"
    elif kis:
        source = "KIS"
    else:
        source = "DART"

    return {
        "symbol": str(code),
        "source": source,
        "debt_ratio": debt_ratio,
        "roe": roe,
        "kis": kis,
        "dart": dart,
    }


if __name__ == "__main__":
    import json
    r = get_financials_safe("005930")
    if r:
        print("source:", r["source"])
        print("debt_ratio:", r["debt_ratio"], "/ roe:", r["roe"])
        print("KIS 있음:", r["kis"] is not None, "/ DART 있음:", r["dart"] is not None)
    else:
        print("조회 실패")
