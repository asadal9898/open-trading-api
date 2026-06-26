"""
계좌 현황 스냅샷 — 비중 배분/분할의 기초 데이터.

brokerage 의 잔고/보유를 한 번에 가져와, 프로그램이 쓸 수 있는 형태로 정리한다.
실제 주문은 하지 않는다 (조회만).

비중 배분의 기준은 '총 평가금액'(현금 + 보유 평가액).

사용:
    from mytrading.account_snapshot import get_snapshot
    snap = get_snapshot(brokerage)
    print(snap.total_equity)        # 총 평가금액
    print(snap.available_cash)      # 주문가능 현금
    for h in snap.holdings:
        print(h.symbol, h.quantity, h.market_value)

CLI:
    uv run python mytrading/account_snapshot.py   # 현재 계좌 스냅샷 출력
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class Holding:
    """보유 종목 한 건."""
    symbol: str
    name: str
    quantity: int
    average_price: float       # 평단가
    current_price: float       # 현재가
    pnl_percent: float         # 평가손익률 (%)

    @property
    def market_value(self) -> float:
        """현재 평가액 = 수량 × 현재가."""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        """매입원가 = 수량 × 평단가."""
        return self.quantity * self.average_price


@dataclass
class AccountSnapshot:
    """계좌 현황 한 장 (배분 계산의 입력)."""
    total_cash: float          # 총 현금
    available_cash: float      # 주문가능 현금
    total_equity: float        # 총 평가금액 (현금 + 보유 평가액) — 비중 배분 기준
    total_pnl: float           # 총 손익
    total_pnl_percent: float   # 총 손익률
    holdings: List[Holding] = field(default_factory=list)

    @property
    def holdings_value(self) -> float:
        """보유 종목 평가액 합계."""
        return sum(h.market_value for h in self.holdings)

    def holding_of(self, symbol: str):
        """특정 종목 보유 정보. 없으면 None."""
        return next((h for h in self.holdings if h.symbol == symbol), None)

    def quantity_of(self, symbol: str) -> int:
        """특정 종목 보유 수량 (없으면 0)."""
        h = self.holding_of(symbol)
        return h.quantity if h else 0


def get_snapshot(brokerage) -> AccountSnapshot:
    """
    brokerage 로 현재 계좌 잔고/보유를 조회해 스냅샷 생성.
    brokerage 는 common.get_brokerage() 로 얻은 것.
    """
    bal = brokerage.get_balance()
    positions = brokerage.get_positions()

    holdings = []
    for p in positions:
        if getattr(p, "quantity", 0) and p.quantity > 0:
            holdings.append(Holding(
                symbol=p.symbol,
                name=getattr(p, "name", "") or p.symbol,
                quantity=int(p.quantity),
                average_price=float(getattr(p, "average_price", 0) or 0),
                current_price=float(getattr(p, "current_price", 0) or 0),
                pnl_percent=float(getattr(p, "unrealized_pnl_percent", 0) or 0),
            ))

    return AccountSnapshot(
        total_cash=float(bal.total_cash),
        available_cash=float(bal.available_cash),
        total_equity=float(bal.total_equity),
        total_pnl=float(bal.total_pnl),
        total_pnl_percent=float(bal.total_pnl_percent),
        holdings=holdings,
    )


def print_snapshot(snap: AccountSnapshot) -> None:
    """스냅샷 보기 좋게 출력."""
    print("=== 계좌 현황 스냅샷 ===")
    print(f"  총 평가금액 : {snap.total_equity:,.0f} 원  (비중 배분 기준)")
    print(f"  총 현금     : {snap.total_cash:,.0f} 원")
    print(f"  주문가능현금: {snap.available_cash:,.0f} 원")
    print(f"  보유 평가액 : {snap.holdings_value:,.0f} 원")
    print(f"  총 손익     : {snap.total_pnl:,.0f} 원 ({snap.total_pnl_percent:+.2f}%)")

    print("\n  [보유 종목]")
    if not snap.holdings:
        print("    (없음)")
    else:
        for h in snap.holdings:
            print(f"    {h.name}({h.symbol}): {h.quantity}주 "
                  f"× {h.current_price:,.0f} = {h.market_value:,.0f}원 "
                  f"(평단 {h.average_price:,.0f}, {h.pnl_percent:+.2f}%)")

    # 검증: 현금 + 보유평가액 ≈ 총평가금액
    calc = snap.total_cash + snap.holdings_value
    if abs(calc - snap.total_equity) > snap.total_equity * 0.02:  # 2% 오차 허용
        print(f"\n  ⚠️ 참고: 현금+보유({calc:,.0f}) vs 총평가({snap.total_equity:,.0f}) "
              f"차이 — 미체결/수수료/평가방식 차이일 수 있음")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    from mytrading.common import init, get_brokerage

    init()
    brokerage = get_brokerage()
    snap = get_snapshot(brokerage)
    print()
    print_snapshot(snap)