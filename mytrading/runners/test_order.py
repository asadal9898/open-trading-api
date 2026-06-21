"""
모의 주문 러너 — 호가 제시 → 사용자 선택 → 1주 매수 → 체결 확인
실제로 계좌에 주문이 들어갑니다(vps=모의, prod=실전). init() 모드 가드 필수.

실행:
    uv run python mytrading/runners/test_order.py            # 모의(vps)
    uv run python mytrading/runners/test_order.py 005930     # 종목 지정

안전장치:
    - init() 으로 모드 확인 (실전이면 YES 가드)
    - 주문 직전 최종 확인 (y/n)
    - 1주만 주문
    - 언제든 취소 가능
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kis_backtest.models.enums import OrderSide, OrderType, OrderStatus
from mytrading.common import init, get_brokerage, get_data_provider, CONFIG

QTY = 1  # 테스트는 1주만


def main():
    # 종목 결정
    symbol = sys.argv[1] if len(sys.argv) > 1 else \
        CONFIG.get("trading", {}).get("symbols", ["005930"])[0]

    # 1. 모드 확인 + 실전 가드
    init()

    data = get_data_provider()
    brokerage = get_brokerage()

    # 2. 호가 조회
    print(f"\n[{symbol} 현재 호가]")
    q = data.get_quote(symbol)
    bid = q.bid_price   # 매수호가 (이 가격에 사겠다는 최고가)
    ask = q.ask_price   # 매도호가 (이 가격에 팔겠다는 최저가)
    print(f"  매수호가(bid): {bid:,.0f}  (잔량 {q.bid_size:,})")
    print(f"  매도호가(ask): {ask:,.0f}  (잔량 {q.ask_size:,})")
    print(f"  등락: {q.change_pct:+.2f}%")
    print()
    print("  참고: 지정가로 매수호가에 걸면 더 싸게 살 수 있으나 체결이 늦거나 안 될 수 있고,")
    print("        시장가/매도호가는 거의 즉시 체결됩니다.")

    # 3. 주문 방식 선택
    print(f"\n[{symbol} {QTY}주 매수 — 주문 방식 선택]")
    print("  1) 시장가 (현재가로 즉시 체결)")
    print(f"  2) 지정가 (가격 직접 입력, 예: 매수호가 {bid:,.0f})")
    print("  3) 취소")
    choice = input("선택 (1/2/3): ").strip()

    if choice == "3" or choice == "":
        print("취소했습니다.")
        return

    if choice == "1":
        order_type = OrderType.MARKET
        price = None
        price_desc = "시장가"
    elif choice == "2":
        raw = input(f"지정가 입력 (원, 기본 {bid:,.0f}): ").strip()
        price = float(raw) if raw else bid
        order_type = OrderType.LIMIT
        price_desc = f"지정가 {price:,.0f}원"
    else:
        print("잘못된 선택. 취소합니다.")
        return

    # 4. 주문 직전 최종 확인
    print(f"\n  >>> {symbol} {QTY}주 매수 / {price_desc}")
    confirm = input("  진행하시겠습니까? (y/n): ").strip().lower()
    if confirm != "y":
        print("취소했습니다.")
        return

    # 5. 주문 제출
    print("\n주문 제출 중...")
    order = brokerage.submit_order(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=QTY,
        order_type=order_type,
        price=price,
    )
    print(f"  주문 접수됨 — 주문번호: {order.id}, 상태: {order.status}")

    # 6. 체결 확인 (잠시 대기 후 주문내역 조회)
    print("\n체결 확인 중... (3초 대기)")
    time.sleep(3)
    orders = brokerage.get_orders()
    target = next((o for o in orders if o.id == order.id), None)
    if target is None:
        print(f"  주문 {order.id} 을 주문내역에서 찾지 못했습니다. get_orders 전체:")
        for o in orders[:5]:
            print(f"    {o.id} {o.symbol} {o.side} {o.quantity} {o.status}")
    else:
        print(f"  주문번호 {target.id}")
        print(f"  상태      : {target.status}")
        print(f"  체결수량  : {target.filled_quantity}/{target.quantity}")
        if target.average_price:
            print(f"  체결가    : {target.average_price:,.0f} 원")
        if target.status == OrderStatus.FILLED:
            print("  ✅ 체결 완료")
        elif target.status in (OrderStatus.SUBMITTED, OrderStatus.PENDING):
            print("  ⏳ 미체결 (지정가가 시장과 안 맞으면 대기 상태일 수 있음)")
        elif target.status == OrderStatus.REJECTED:
            print("  ❌ 거부됨 — 사유를 KIS 에서 확인하세요")

    print("\n주문 테스트 완료. 잔고/보유는 check_balance.py 로 확인하세요.")


if __name__ == "__main__":
    main()