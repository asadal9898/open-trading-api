"""
주문 취소 러너 — 미체결 주문을 주문번호로 취소.

실제로 주문 취소가 들어갑니다(vps=모의, prod=실전). init() 모드 가드 필수.

사용:
    uv run python mytrading/runners/test_cancel.py 0000044082
    (주문번호는 test_order.py 지정가 주문 시 출력된 번호)

흐름:
    - init() 으로 모드 확인 (실전이면 YES 가드)
    - 주문 가능 계좌인지 확인 (can_order)
    - cancel_order(주문번호) 호출
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mytrading.common import init, get_brokerage, assert_can_order
from mytrading.notify import send_message


def main():
    args = sys.argv[1:]
    if not args:
        print("사용법: uv run python mytrading/runners/test_cancel.py <주문번호>")
        print("  주문번호는 test_order.py 지정가 주문 시 출력된 번호입니다.")
        return

    order_id = args[0].strip()

    # 1. 모드 확인 + 실전 가드
    init()

    # 주문 가능 계좌인지 확인
    if not assert_can_order():
        return

    brokerage = get_brokerage()

    print(f"\n[주문 취소] 주문번호: {order_id}")
    confirm = input("  이 주문을 취소하시겠습니까? (y/n): ")
    if confirm.strip().lower() != "y":
        print("취소 안 함. 종료.")
        return

    print("취소 요청 중...")
    try:
        ok = brokerage.cancel_order(order_id)
        if ok:
            print(f"  ✅ 주문 {order_id} 취소 완료")
            try:
                send_message(f"🚫 <b>주문 취소</b>\n주문번호: {order_id}")
            except Exception:
                pass
        else:
            print(f"  ⚠️ 취소 실패 (이미 체결됐거나 없는 주문일 수 있음)")
    except Exception as e:
        print(f"  ⚠️ 취소 중 오류: {e}")
        print("     (이미 체결됐거나, 주문번호가 틀렸을 수 있습니다)")

    print("\n취소 테스트 완료. 잔고/주문은 check_balance.py 로 확인하세요.")


if __name__ == "__main__":
    main()