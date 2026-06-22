"""
계좌 조회 러너 — 잔고 / 보유종목 / 현재가 조회 (주문 안 함, 가장 안전)
brokerage 연결과 인증이 잘 되는지 확인하는 용도.

실행:
    uv run python mytrading/runners/check_balance.py              # 모의(vps)
    uv run python mytrading/runners/check_balance.py --telegram   # 잔고를 폰으로도 전송
    KIS_MODE=prod uv run python mytrading/runners/check_balance.py  # 실전(YES 확인)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mytrading.common import init, get_brokerage, get_data_provider, CONFIG
from mytrading.notify import notify_balance


def main():
    # --telegram 플래그: 잔고를 텔레그램으로도 전송
    to_telegram = "--telegram" in sys.argv

    # 모드 확인 + 실전 가드 (조회만 해도 init 으로 모드 명확히)
    init()

    brokerage = get_brokerage()

    # 1. 잔고
    print("\n[잔고]")
    bal = brokerage.get_balance()
    print(f"  총 현금     : {bal.total_cash:,.0f} 원")
    print(f"  주문가능현금: {bal.available_cash:,.0f} 원")
    print(f"  총 평가금액 : {bal.total_equity:,.0f} 원")
    print(f"  총 손익     : {bal.total_pnl:,.0f} 원 ({bal.total_pnl_percent:.2f}%)")

    # 2. 보유 종목
    print("\n[보유 종목]")
    positions = brokerage.get_positions()
    if not positions:
        print("  (보유 종목 없음)")
    else:
        for p in positions:
            print(f"  {p.symbol} {p.name}: {p.quantity}주 "
                  f"@ 평단 {p.average_price:,.0f} / 현재 {p.current_price:,.0f} "
                  f"({p.unrealized_pnl_percent:+.2f}%)")

    # 3. 관심 종목 현재가 (config symbols)
    print("\n[현재가]")
    data = get_data_provider()
    symbols = CONFIG.get("trading", {}).get("symbols", ["005930"])
    for sym in symbols:
        try:
            q = data.get_quote(sym)
            print(f"  {sym}: {q}")
        except Exception as e:
            print(f"  {sym}: 조회 실패 - {e}")

    # 4. 텔레그램 전송 (옵션)
    if to_telegram:
        ok = notify_balance(bal.total_cash, bal.total_equity,
                            bal.total_pnl, bal.total_pnl_percent)
        print("\n[텔레그램]", "전송됨 ✅" if ok else "전송 실패 ❌")

    print("\n조회 완료 — brokerage 연결 정상")


if __name__ == "__main__":
    main()