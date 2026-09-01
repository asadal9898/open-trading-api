# -*- coding: utf-8 -*-
"""
ETF 파킹 체결 확인 — 오늘 `moderate_etf_parking.py --live` 실행이 실제로 체결됐는지
사후 검증. 파킹(10:25) 직후가 아니라 충분히 시간이 지난 뒤(15:20 권장 — 정규장 마감 전,
문제 발견 시 당일 대응 여지) 별도로 돈다.

⚠️ 발주 없음. 이 스크립트는 조회·비교만 한다 — submit_order 등 주문 코드를 절대
   추가하지 말 것. 불일치를 발견해도 재주문하지 않는다(재주문은 사람이 상황을 보고
   판단 — moderate_etf_parking_check.py 설계 논의에서 확정된 원칙, 부분체결을
   "문제"로 오판해 과다매수하는 리스크를 피하기 위함).

⚠️ 전체 금액(원가) 기준 정합성만 확인한다 — 종목별 정확 비교는 안 한다. 이유:
   파킹 로그(`{날짜}_live.json`)에 "실행 전 종목별 보유수량"이 없어서(실행 전 전체
   파킹원가 합계만 있음) 종목별로 "13주 주문했는데 몇 주 됐나"까지는 못 가린다.
   할 수 있는 건 "오늘 파킹 실행 전 원가 + 오늘 주문 예상액(성공한 주문만) ≈ 지금
   실제 파킹원가"라는 전체 합계 검산뿐이다. 종목별 비교를 하려면 파킹 스크립트
   로그 스키마에 종목별 pre_qty 필드를 먼저 추가해야 한다(별도 작업).

체결 반영 지연 참고(2026-09-01 실측): 469830/214980은 40초 내 완전 반영됐지만
157450은 반영에 더 걸렸다(정확한 시점 미특정, 늦어도 수 시간 내 완료 확인). 15:20이면
파킹(10:25) 대비 5시간 버퍼라 이런 지연은 대부분 흡수된다.

실행:
    KIS_MODE=vps uv run python mytrading/moderate_etf_parking_check.py            # dry-run, 출력만
    KIS_MODE=vps uv run python mytrading/moderate_etf_parking_check.py --notify   # 불일치 시에만 텔레그램 발송
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PARKING_LOG_DIR = Path.home() / "moderate_etf_parking_log"
LOG_DIR = Path.home() / "moderate_etf_parking_check_log"
TOLERANCE_PCT = 0.02  # ±2% — 오차 허용범위


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true",
                     help="불일치 발견 시에만 텔레그램 발송 (정상이면 발송 안 함)")
    args = ap.parse_args()

    # ① vps 강제 — 발주는 없지만 다른 스크립트와 동일하게 모드 일관성 확인
    from mytrading.common import resolve_mode
    mode = resolve_mode()
    if mode != "vps":
        print(f"🚫 파킹 체결 확인은 vps(모의) 전용입니다 — 현재 모드: {mode}. 즉시 중단.")
        sys.exit(1)

    from mytrading.common import init
    init(require_confirm=False)

    today = date.today()

    print("=" * 50)
    print(f"[moderate_etf_parking_check] {today}")
    print(f"  모드: {mode}")

    # ② 오늘 --live 파킹 로그 확인
    live_log_path = PARKING_LOG_DIR / f"{today}_live.json"
    if not live_log_path.exists():
        print(f"\n(오늘 --live 파킹 실행 기록 없음 — {live_log_path} 없음. 확인 대상 없음, 종료)")
        _write_log(today, {"had_live_log": False})
        return

    live_log = json.loads(live_log_path.read_text(encoding="utf-8"))
    pre_cost = float(live_log.get("parked_etf_cost_basis", 0.0))
    items = live_log.get("items", [])

    # ③ 기대 파킹 원가 = 실행 전 원가 + 오늘 성공한 주문분의 예상 매입액
    #    (실패했거나 시도 안 된 항목은 0으로 취급 — 실제로 안 나갔을 돈이므로)
    expected_new = 0.0
    for it in items:
        lr = it.get("live_result") or {}
        if lr.get("success") is True:
            expected_new += float(it.get("qty", 0)) * float(it.get("price", 0.0))
    expected_cost = pre_cost + expected_new

    print(f"  오늘 --live 파킹 로그: {live_log_path}")
    print(f"  실행 전 파킹원가: {pre_cost:,.0f}원")
    print(f"  오늘 성공 주문분 예상액: {expected_new:,.0f}원")
    print(f"  기대 파킹원가(합): {expected_cost:,.0f}원")

    # ④ 지금 실제 파킹원가 재조회 — 파킹 스크립트 자신의 함수를 그대로 재사용
    #    (같은 정의를 써야 정합성 비교가 의미 있음 — 독립 재구현하면 정의가 미묘하게
    #    갈릴 위험)
    from mytrading.moderate_etf_parking import _load_krw_etfs, _parked_etf_cost_basis
    from mytrading.common import get_brokerage
    from mytrading.account_snapshot import get_snapshot

    snap = get_snapshot(get_brokerage())
    etfs = _load_krw_etfs()
    etf_codes = {str(e.get("code", "")) for e in etfs}
    actual_cost = _parked_etf_cost_basis(snap, etf_codes)

    print(f"  현재 실제 파킹원가: {actual_cost:,.0f}원")

    # ⑤ 정합성 비교 — ±2% 허용
    diff = actual_cost - expected_cost
    tolerance = expected_cost * TOLERANCE_PCT if expected_cost > 0 else 0.0
    within_tolerance = abs(diff) <= tolerance

    print(f"  차이: {diff:,.0f}원 (허용범위 ±{tolerance:,.0f}원, {TOLERANCE_PCT*100:.0f}%)")
    print(f"  정합성: {'✅ 정상' if within_tolerance else '⚠️ 불일치 의심'}")

    notified = False
    if within_tolerance:
        print("\n(파킹 정상 체결 — 알림 생략)")
    else:
        msg = (
            f"⚠️ <b>파킹 미체결 의심 ({today})</b>\n"
            f"기대 파킹원가: {expected_cost:,.0f}원\n"
            f"실제 파킹원가: {actual_cost:,.0f}원\n"
            f"차이: {diff:,.0f}원 (허용범위 ±{TOLERANCE_PCT*100:.0f}%)\n\n"
            f"⚠️ 자동 재주문 안 함 — 직접 확인 후 판단해주세요."
        )
        print("\n--- 알림 문구 ---")
        print(msg.replace("<b>", "").replace("</b>", ""))
        if args.notify:
            try:
                from mytrading.telegram import notify
                notified = notify.send_message(msg)
                print(f"\n(텔레그램 발송: {'성공' if notified else '실패'})")
            except Exception as e:
                print(f"\n(발송 실패: {e})")
        else:
            print("\n(--notify 없음 — 발송 생략)")

    _write_log(today, {
        "had_live_log": True,
        "pre_cost": pre_cost,
        "expected_new": expected_new,
        "expected_cost": expected_cost,
        "actual_cost": actual_cost,
        "diff": diff,
        "tolerance": tolerance,
        "within_tolerance": within_tolerance,
        "notified": notified,
    })


def _write_log(today, data: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{today}.json"
    log_data = {"date": str(today), **data}
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n로그 기록: {log_path}")


if __name__ == "__main__":
    main()
