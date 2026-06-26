"""
모의 매도 러너 — 보유 종목 확인 → 매도 수량 선택 → 매도 주문 → 체결 확인
실제로 계좌에 매도 주문이 들어갑니다(vps=모의, prod=실전). init() 모드 가드 필수.

실행:
    uv run python mytrading/runners/test_sell.py            # config 첫 종목
    uv run python mytrading/runners/test_sell.py 005930     # 종목 지정

안전장치:
    - init() 으로 모드 확인 (실전이면 YES 가드)
    - 보유 수량 확인 (없으면 매도 불가)
    - 매도 직전 최종 확인 (y/n)
    - 보유 수량 초과 매도 차단
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kis_backtest.models.enums import OrderSide, OrderType, OrderStatus
from mytrading.common import init, get_brokerage, get_data_provider, CONFIG, assert_can_order
from mytrading.notify import (
    notify_order_submitted, notify_order_filled, notify_error,
)
from mytrading.trade_log import record_trade


def _select_held(positions) -> str:
    """
    매도할 종목을 보유 목록에서 선택.
    - 명령행 인자(sys.argv[1])로 주면 그대로 사용
    - 없으면 보유 종목 메뉴에서 선택 (보유 1개면 자동)
    반환: 종목코드 (취소/없음 시 빈 문자열)
    """
    held_list = [p for p in positions if p.quantity > 0]

    if len(sys.argv) > 1:
        return sys.argv[1].strip()

    if not held_list:
        return ""
    if len(held_list) == 1:
        return held_list[0].symbol

    print("\n[매도할 종목 선택 — 보유 종목]")
    for i, p in enumerate(held_list, 1):
        nm = p.name or p.symbol
        print(f"  {i}) {nm}({p.symbol}) {p.quantity}주 "
              f"@ 평단 {p.average_price:,.0f} ({p.unrealized_pnl_percent:+.2f}%)")
    print("  0) 취소")
    raw = input(f"선택 (0~{len(held_list)}): ").strip()

    if raw == "0" or raw == "":
        return ""
    try:
        idx = int(raw)
        if 1 <= idx <= len(held_list):
            return held_list[idx - 1].symbol
    except ValueError:
        pass
    print("잘못된 선택.")
    return ""


def main():
    # 1. 모드 확인 + 실전 가드
    init()

    # 주문 가능 계좌인지 확인 (IRP 등 조회전용이면 중단)
    if not assert_can_order():
        return

    data = get_data_provider()
    brokerage = get_brokerage()

    # 2. 보유 종목 조회 후 매도할 종목 선택
    try:
        positions = brokerage.get_positions()
    except Exception as e:
        print(f"  보유 조회 실패: {e}")
        notify_error("매도 실패", f"보유 조회 실패: {e}")
        return

    if not [p for p in positions if p.quantity > 0] and len(sys.argv) <= 1:
        print("\n보유 종목이 없습니다. 매도할 수 없습니다.")
        print("  (먼저 test_order.py 로 매수하거나, check_balance.py 로 보유를 확인하세요.)")
        return

    symbol = _select_held(positions)
    if not symbol:
        print("취소했습니다.")
        return

    # 3. 선택한 종목 보유 확인
    print(f"\n[{symbol} 보유 확인]")
    held = next((p for p in positions if p.symbol == symbol), None)
    if held is None or held.quantity <= 0:
        print(f"  {symbol} 보유 수량이 없습니다. 매도할 수 없습니다.")
        print("  (check_balance.py 로 보유를 확인하세요.)")
        return

    name = held.name or symbol
    print(f"  보유: {name}({symbol}) {held.quantity}주 "
          f"@ 평단 {held.average_price:,.0f} / 현재 {held.current_price:,.0f} "
          f"({held.unrealized_pnl_percent:+.2f}%)")

    # 3. 호가 조회
    print(f"\n[{symbol} 현재 호가]")
    q = data.get_quote(symbol)
    bid = q.bid_price
    ask = q.ask_price
    print(f"  매수호가(bid): {bid:,.0f}  (잔량 {q.bid_size:,})")
    print(f"  매도호가(ask): {ask:,.0f}  (잔량 {q.ask_size:,})")
    print("  참고: 시장가/매수호가로 팔면 거의 즉시 체결, 지정가는 가격 맞아야 체결.")

    # 4. 매도 수량 선택
    max_qty = held.quantity
    print(f"\n[{name} 매도 — 수량 입력 (보유 {max_qty}주)]")
    raw_qty = input(f"매도 수량 (1~{max_qty}, 기본 1): ").strip()
    qty = int(raw_qty) if raw_qty else 1
    if qty < 1 or qty > max_qty:
        print(f"  잘못된 수량. 보유 {max_qty}주 이내여야 합니다. 취소합니다.")
        return

    # 5. 주문 방식 선택
    print(f"\n[매도 방식 선택]")
    print("  1) 시장가 (즉시 체결)")
    print(f"  2) 지정가 (가격 입력, 예: 매수호가 {bid:,.0f})")
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

    # 6. 최종 확인
    print(f"\n  >>> {name}({symbol}) {qty}주 매도 / {price_desc}")
    confirm = input("  진행하시겠습니까? (y/n): ").strip().lower()
    if confirm != "y":
        print("취소했습니다.")
        return

    # 7. 매도 주문 제출
    print("\n매도 주문 제출 중...")
    try:
        order = brokerage.submit_order(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=qty,
            order_type=order_type,
            price=price,
        )
    except Exception as e:
        msg = str(e)
        print(f"\n  ⚠️ 매도가 접수되지 않았습니다: {msg}")
        if "영업일" in msg:
            print("     (오늘은 거래일이 아닙니다. 평일 09:00~15:30 에 다시 시도하세요.)")
        elif "시간" in msg or "장" in msg:
            print("     (장 운영 시간이 아닐 수 있습니다. 평일 09:00~15:30 확인.)")
        notify_error("매도 실패", msg)
        return

    print(f"  주문 접수됨 — 주문번호: {order.id}, 상태: {order.status}")
    notify_order_submitted(symbol, "SELL", qty, price_desc)

    # 거래 로그 기록 (접수 시점)
    record_trade(symbol=symbol, name=name, side="SELL", quantity=qty,
                 price=(price if price is not None else bid),
                 order_id=str(order.id), status="submitted")

    # 8. 체결 확인
    print("\n체결 확인 중... (3초 대기)")
    time.sleep(3)
    try:
        orders = brokerage.get_orders()
    except Exception as e:
        print(f"  주문내역 조회 실패: {e}")
        print("  (주문은 접수됐을 수 있으니 check_balance.py 로 확인하세요.)")
        notify_error("체결 확인 실패", str(e))
        return

    target = next((o for o in orders if o.id == order.id), None)
    if target is None:
        print(f"  주문 {order.id} 을 주문내역에서 찾지 못했습니다.")
    else:
        print(f"  상태      : {target.status}")
        print(f"  체결수량  : {target.filled_quantity}/{target.quantity}")
        if target.average_price:
            print(f"  체결가    : {target.average_price:,.0f} 원")
        if target.status == OrderStatus.FILLED:
            print("  ✅ 매도 체결 완료")
            fill_price = target.average_price or price or bid
            notify_order_filled(symbol, name, "SELL",
                                target.filled_quantity or qty, fill_price)
        elif target.status in (OrderStatus.SUBMITTED, OrderStatus.PENDING):
            print("  ⏳ 미체결 (지정가가 시장과 안 맞으면 대기)")
        elif target.status == OrderStatus.REJECTED:
            print("  ❌ 거부됨 — 사유를 KIS 에서 확인하세요")
            notify_error("매도 거부됨", f"{symbol} 매도가 거부되었습니다")

    print("\n매도 테스트 완료. 잔고/보유는 check_balance.py 로 확인하세요.")


if __name__ == "__main__":
    main()