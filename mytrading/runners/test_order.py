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
from mytrading.common import init, get_brokerage, get_data_provider, CONFIG, assert_can_order
from mytrading.notify import (
    notify_order_submitted, notify_order_filled, notify_error,
)
from mytrading.portfolio import get_watch_symbols, get_symbol_names
from mytrading.trade_log import record_trade

QTY = 1  # 테스트는 1주만


def _select_symbol() -> str:
    """
    매수할 종목 선택.
    - 명령행 인자(sys.argv[1])로 주면 그대로 사용
    - 없으면 universe 종목풀 메뉴에서 선택 (비면 config 폴백)
    반환: 종목코드 (취소 시 빈 문자열)
    """
    if len(sys.argv) > 1:
        return sys.argv[1].strip()

    symbols = get_watch_symbols(CONFIG)
    names = get_symbol_names()

    if len(symbols) == 1:
        return symbols[0]

    print("\n[매수할 종목 선택]")
    for i, sym in enumerate(symbols, 1):
        label = f"{names.get(sym, '')}({sym})" if names.get(sym) else sym
        print(f"  {i}) {label}")
    print("  0) 취소")
    raw = input(f"선택 (0~{len(symbols)}): ").strip()

    if raw == "0" or raw == "":
        return ""
    try:
        idx = int(raw)
        if 1 <= idx <= len(symbols):
            return symbols[idx - 1]
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

    # 종목 선택 (인자 또는 universe 메뉴)
    symbol = _select_symbol()
    if not symbol:
        print("취소했습니다.")
        return

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

    # 5. 주문 제출 (영업일 아님/장외 등은 깔끔히 처리)
    print("\n주문 제출 중...")
    try:
        order = brokerage.submit_order(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=QTY,
            order_type=order_type,
            price=price,
        )
    except Exception as e:
        # 모의투자 영업일 아님, 장 시간 외, 잔고 부족 등
        msg = str(e)
        print(f"\n  ⚠️ 주문이 접수되지 않았습니다: {msg}")
        if "영업일" in msg:
            print("     (오늘은 거래일이 아닙니다. 평일 09:00~15:30 에 다시 시도하세요.)")
        elif "시간" in msg or "장" in msg:
            print("     (장 운영 시간이 아닐 수 있습니다. 평일 09:00~15:30 확인.)")
        notify_error("주문 실패", msg)
        return

    print(f"  주문 접수됨 — 주문번호: {order.id}, 상태: {order.status}")
    notify_order_submitted(symbol, "BUY", QTY, price_desc)

    # 거래 로그 기록 (접수 시점 — 체결가는 지정가/현재가로 근사)
    sym_name = get_symbol_names().get(symbol, symbol)
    record_trade(symbol=symbol, name=sym_name, side="BUY", quantity=QTY,
                 price=(price if price is not None else ask),
                 order_id=str(order.id), status="submitted")

    # 6. 체결 확인 (잠시 대기 후 주문내역 조회)
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
            # 체결가가 없으면 주문가/현재가로 대체
            fill_price = target.average_price or price or ask
            notify_order_filled(symbol, symbol, "BUY",
                                target.filled_quantity or QTY, fill_price)
        elif target.status in (OrderStatus.SUBMITTED, OrderStatus.PENDING):
            print("  ⏳ 미체결 (지정가가 시장과 안 맞으면 대기 상태일 수 있음)")
        elif target.status == OrderStatus.REJECTED:
            print("  ❌ 거부됨 — 사유를 KIS 에서 확인하세요")
            notify_error("주문 거부됨", f"{symbol} 주문이 거부되었습니다")

    print("\n주문 테스트 완료. 잔고/보유는 check_balance.py 로 확인하세요.")


if __name__ == "__main__":
    main()