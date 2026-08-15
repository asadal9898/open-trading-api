"""
포지션 상태 추적 — 물타기 1회 제한 + 주기 중복매수 방지(A-3).

계좌 잔고(account_snapshot)에는 "이미 물탔는지"/"이번 주기에 이미 샀는지" 정보가 없다.
- 백테스트 검증 전략은 평단 -30% 에서 물타기를 '1회만' 허용하므로, 종목별로 물타기
  여부를 파일에 남겨 중복 물타기를 막는다.
- value_range 는 order_pace.is_trade_day(요일 고정 게이트)를 쓰지 않는(신호 기반) 스타일이라,
  저점 신호가 며칠 지속되면 실행할 때마다 매번 매수 후보로 잡힐 수 있다. 마지막 매수일
  (last_bought)을 남겨 cadence 주기 안 재매수를 막는다.

파일: mytrading/position_state.yaml
  종목코드 → { entry_date, averaged_down, last_bought }

수명:
  - 신규 매수 시  mark_entry(code)        → averaged_down=False 로 시작
  - 물타기 실행 시 mark_averaged_down(code)
  - 매수 체결 시   mark_bought(code)      → last_bought 갱신(A-3).
                                            ⚠️ 이 함수를 호출하는 코드가 아직 없다 — 실발주(D)를
                                            붙일 때 체결 성공 직후 호출하도록 반드시 연결할 것.
                                            그전까진 last_bought 가 항상 비어있어
                                            bought_within_cadence() 는 늘 False(스킵 안 함)다.
  - 전량 매도 시   clear(code)            → 다음 진입에서 다시 1회 가능

사용:
    from mytrading.position_state import was_averaged_down, mark_averaged_down, clear
    from mytrading.position_state import mark_bought, bought_within_cadence
    if not was_averaged_down("005930"):
        ...  # 물타기 가능
    if not bought_within_cadence("005930", cadence_days=7):
        ...  # 이번 주(7일) 안 산 적 없음 — 매수 가능
"""
from datetime import date
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATH = _REPO_ROOT / "mytrading" / "position_state.yaml"


def _load() -> dict:
    """상태 파일 로드. 없으면 빈 dict."""
    if not _PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[position_state] 로드 실패: {e}")
        return {}


def _save(data: dict) -> None:
    """상태 파일 저장 (종목코드 순 정렬)."""
    try:
        import yaml
        with open(_PATH, "w", encoding="utf-8") as f:
            f.write("# 포지션 상태 — 물타기 1회 제한 + 주기 중복매수 방지 추적. 자동 생성.\n")
            f.write("# entry_date=최초 진입일, averaged_down=물타기 실행 여부, last_bought=마지막 매수일\n")
            yaml.safe_dump(dict(sorted(data.items())), f,
                           allow_unicode=True, sort_keys=False,
                           default_flow_style=False)
    except Exception as e:
        print(f"[position_state] 저장 실패: {e}")


def get(code: str) -> Optional[dict]:
    """종목 상태. 없으면 None."""
    return _load().get(str(code))


def was_averaged_down(code: str) -> bool:
    """이미 물타기했는가. 상태가 없으면 False (=물타기 가능)."""
    st = get(code)
    return bool(st and st.get("averaged_down"))


def mark_entry(code: str, entry_date: Optional[date] = None) -> None:
    """신규 진입 기록. 물타기 카운터를 초기화한다."""
    data = _load()
    data[str(code)] = {
        "entry_date": str(entry_date or date.today()),
        "averaged_down": False,
    }
    _save(data)


def mark_averaged_down(code: str) -> None:
    """물타기 실행 기록 (이후 추가 물타기 차단)."""
    data = _load()
    st = data.get(str(code)) or {"entry_date": str(date.today())}
    st["averaged_down"] = True
    data[str(code)] = st
    _save(data)


def mark_bought(code: str, bought_date: Optional[date] = None) -> None:
    """실발주 성공 시 매수일 기록 (주기 중복매수 판정용, A-3).

    ⚠️ TODO(D 실발주 붙일 때 연결): 이 함수를 호출하는 코드가 아직 없다.
    실제 주문 체결 성공 직후 이 함수를 호출하도록 연결해야 한다.
    그전까진 last_bought 가 항상 비어있어 bought_within_cadence() 는 늘 False(스킵 안 함)다.
    """
    data = _load()
    st = data.get(str(code)) or {}
    st["last_bought"] = str(bought_date or date.today())
    data[str(code)] = st
    _save(data)


def bought_within_cadence(code: str, cadence_days: int, asof: Optional[date] = None) -> bool:
    """마지막 매수(last_bought)로부터 cadence_days 미만 지났으면 True(=이번 주기에 이미
    산 것, 스킵 대상). 정확히 cadence_days 만큼 지났으면(경계값) False — 매수 대상이다.
    last_bought 기록이 없으면 False(=한 번도 안 샀으니 매수 가능)."""
    st = get(code)
    if not st or not st.get("last_bought"):
        return False
    last = date.fromisoformat(st["last_bought"])
    today = asof or date.today()
    return (today - last).days < cadence_days


def clear(code: str) -> None:
    """전량 매도 시 상태 삭제 → 다음 진입에서 물타기 1회 다시 가능."""
    data = _load()
    if str(code) in data:
        del data[str(code)]
        _save(data)


def sync_with_holdings(held_codes) -> None:
    """계좌에 없는 종목의 상태를 정리 (수동 매도·체결 누락 대비).

    held_codes: 현재 보유 종목코드 iterable.
    """
    held = {str(c) for c in held_codes}
    data = _load()
    stale = [c for c in data if c not in held]
    if stale:
        for c in stale:
            del data[c]
        _save(data)
        print(f"[position_state] 미보유 종목 상태 정리: {', '.join(stale)}")


if __name__ == "__main__":
    data = _load()
    print(f"=== 포지션 상태 ({_PATH.name}) ===")
    if not data:
        print("  (비어 있음)")
    for code, st in sorted(data.items()):
        mark = "물타기 완료" if st.get("averaged_down") else "물타기 가능"
        print(f"  {code}: 진입 {st.get('entry_date')} · {mark}")