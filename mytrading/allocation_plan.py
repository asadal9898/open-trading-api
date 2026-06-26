"""
배분 계산 (1-a) — 비중대로 종목별 목표금액을 계산하고, 현재 보유와의 차이를 낸다.

⚠️ 계산/출력만 한다. 실제 주문은 하지 않는다 (분할 주문 1-b 는 백테스트 검증 후).

흐름:
  스냅샷(총평가/보유)  +  비중(allocations)  +  종목풀(universe/free)
     → 분류별 목표금액 (총평가 × 비중%)
     → 분류 안에서 종목별 균등 분배 (분류금액 ÷ 종목수)
     → 종목별: 목표금액 vs 현재평가액 → 차액(+사야 / −팔아야)

기준: 총 평가금액(현금 + 보유). 균등 분할(종목별 가중치는 추후).

사용:
    from mytrading.allocation_plan import build_plan, print_plan
    plan = build_plan(snapshot, portfolio, user_key="Owner", account_name="일반증권1")
    print_plan(plan)

CLI:
    uv run python mytrading/allocation_plan.py                 # Owner 기본
    uv run python mytrading/allocation_plan.py --user Owner --account 일반증권1
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# 비중을 가진 분류 (cash 는 투자 안 하므로 종목 배분 대상 아님)
_INVEST_CATEGORIES = ("aggressive", "moderate", "safe", "free")
_CAT_LABEL = {"aggressive": "공격", "moderate": "보수", "safe": "안전",
              "free": "자유", "cash": "현금"}


@dataclass
class TargetLine:
    """종목 한 개의 목표/현재/차액."""
    symbol: str
    name: str
    category: str          # aggressive/moderate/safe/free
    target_value: float    # 목표 평가금액
    current_value: float   # 현재 평가금액 (보유 × 현재가)
    current_qty: int       # 현재 보유 수량

    @property
    def diff_value(self) -> float:
        """목표 − 현재. 양수=더 사야, 음수=팔아야."""
        return self.target_value - self.current_value

    @property
    def action(self) -> str:
        if self.diff_value > 0:
            return "BUY"
        if self.diff_value < 0:
            return "SELL"
        return "HOLD"


@dataclass
class AllocationPlan:
    """배분 계산 결과 한 장."""
    user_key: str
    account_name: str
    total_equity: float                      # 기준 총평가금액
    category_targets: Dict[str, float] = field(default_factory=dict)  # 분류별 목표금액
    cash_target: float = 0.0                 # 현금 목표(투자 안 함)
    lines: List[TargetLine] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # 현금 분석 (매도대금 고려 + 현금목표 존중)
    current_cash: float = 0.0                # 현재 주문가능 현금
    total_buy_needed: float = 0.0            # 총 매수 필요액 (BUY 차액 합)
    total_sell_proceeds: float = 0.0         # 매도 예정 대금 (SELL 차액 합, 양수)
    available_for_buy: float = 0.0           # 매수 가용 = 현금 + 매도대금 − 현금목표
    cash_sufficient: bool = True             # 가용 ≥ 매수필요?
    shortfall: float = 0.0                   # 부족분 (부족할 때만 > 0)


def build_plan(snapshot, portfolio, user_key: str,
               account_name: str) -> Optional[AllocationPlan]:
    """
    스냅샷 + 포트폴리오로 배분 계획 계산.
    snapshot: account_snapshot.AccountSnapshot
    portfolio: portfolio.Portfolio
    """
    al = portfolio.allocation_for(user_key, account_name)
    if al is None:
        return None

    total = float(snapshot.total_equity)
    plan = AllocationPlan(user_key=user_key, account_name=account_name,
                          total_equity=total)

    # 1) 분류별 목표금액 = 총평가 × 비중%
    plan.cash_target = total * (al.cash / 100.0)
    cat_pct = {
        "aggressive": al.aggressive,
        "moderate": al.moderate,
        "safe": al.safe,
        "free": al.free,
    }
    for cat, pct in cat_pct.items():
        plan.category_targets[cat] = total * (pct / 100.0)

    # 2) 분류 안에서 종목별 균등 분배 + 3) 현재 보유와 차이
    for cat in _INVEST_CATEGORIES:
        cat_amount = plan.category_targets.get(cat, 0.0)

        # 분류별 종목 목록: free 는 개인별(free_symbols), 나머지는 universe
        if cat == "free":
            items = al.free_symbols  # [{code, name}]
        else:
            items = portfolio.names(cat)  # [{code, name}]

        if cat_amount <= 0:
            continue
        if not items:
            plan.warnings.append(
                f"{_CAT_LABEL[cat]} 비중 {cat_amount:,.0f}원인데 종목이 없음")
            continue

        per_symbol = cat_amount / len(items)  # 균등 분배
        for it in items:
            sym = it["code"]
            nm = it.get("name", sym)
            cur_val = snapshot.holding_of(sym).market_value if snapshot.holding_of(sym) else 0.0
            cur_qty = snapshot.quantity_of(sym)
            plan.lines.append(TargetLine(
                symbol=sym, name=nm, category=cat,
                target_value=per_symbol,
                current_value=cur_val,
                current_qty=cur_qty,
            ))

    # ---- 현금 분석 (매도대금 고려 + 현금목표 존중) ----
    plan.current_cash = float(snapshot.available_cash)
    # BUY 차액 합(매수 필요), SELL 차액 합(매도로 들어올 대금)
    plan.total_buy_needed = sum(
        ln.diff_value for ln in plan.lines if ln.diff_value > 0)
    plan.total_sell_proceeds = sum(
        -ln.diff_value for ln in plan.lines if ln.diff_value < 0)
    # 매수 가용 = 현재 현금 + 매도 예정 대금 − 현금 목표(비중상 남길 현금)
    plan.available_for_buy = (plan.current_cash
                              + plan.total_sell_proceeds
                              - plan.cash_target)
    plan.cash_sufficient = plan.available_for_buy >= plan.total_buy_needed
    if not plan.cash_sufficient:
        plan.shortfall = plan.total_buy_needed - plan.available_for_buy

    return plan


def print_plan(plan: AllocationPlan) -> None:
    if plan is None:
        print("배분 계획 없음 (해당 사용자/계좌의 비중 설정을 찾지 못함)")
        return

    print(f"=== 배분 계획: {plan.user_key} / {plan.account_name} ===")
    print(f"  기준 총평가금액: {plan.total_equity:,.0f}원")
    print(f"\n  [분류별 목표금액]")
    print(f"    현금: {plan.cash_target:,.0f}원 (투자 안 함)")
    for cat in _INVEST_CATEGORIES:
        amt = plan.category_targets.get(cat, 0.0)
        print(f"    {_CAT_LABEL[cat]}: {amt:,.0f}원")

    print(f"\n  [종목별 목표 vs 현재]")
    if not plan.lines:
        print("    (배분 대상 종목 없음)")
    else:
        for ln in plan.lines:
            arrow = {"BUY": "▲ 매수", "SELL": "▼ 매도", "HOLD": "= 유지"}[ln.action]
            print(f"    [{_CAT_LABEL[ln.category]}] {ln.name}({ln.symbol})")
            print(f"        목표 {ln.target_value:,.0f} / 현재 {ln.current_value:,.0f} "
                  f"({ln.current_qty}주) → {arrow} {abs(ln.diff_value):,.0f}원")

    # 현금 분석 (매도대금 고려 + 현금목표 존중)
    print(f"\n  [현금 분석]")
    print(f"    현재 주문가능현금 : {plan.current_cash:,.0f}원")
    print(f"    매도 예정 대금(+)  : {plan.total_sell_proceeds:,.0f}원")
    print(f"    현금 목표(−)       : {plan.cash_target:,.0f}원 (비중상 남길 현금)")
    print(f"    → 매수 가용현금     : {plan.available_for_buy:,.0f}원")
    print(f"    매수 필요액        : {plan.total_buy_needed:,.0f}원")
    if plan.cash_sufficient:
        surplus = plan.available_for_buy - plan.total_buy_needed
        print(f"    ✅ 충분 (여유 {surplus:,.0f}원)")
    else:
        ratio = (plan.available_for_buy / plan.total_buy_needed * 100
                 if plan.total_buy_needed > 0 else 0)
        print(f"    ⚠️ 부족 {plan.shortfall:,.0f}원 "
              f"(가용으로는 목표의 약 {ratio:.0f}%까지 매수 가능)")
        print(f"       → 1-b 에서 비중 비율 유지하며 가용 한도로 축소 매수 예정")

    if plan.warnings:
        print(f"\n  [참고]")
        for w in plan.warnings:
            print(f"    ⚠️ {w}")

    print("\n  ※ 이 계획은 계산/참고용입니다. 실제 주문은 아직 실행하지 않습니다.")
    print("    (분할 주문은 백테스트 검증 후 별도 구현 예정 — 1-b)")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    from mytrading.common import init, get_brokerage
    from mytrading.account_snapshot import get_snapshot
    from mytrading.portfolio import load_portfolio

    args = sys.argv[1:]
    user = "Owner"
    account = "일반증권1"
    if "--user" in args:
        user = args[args.index("--user") + 1]
    if "--account" in args:
        account = args[args.index("--account") + 1]

    init()
    brokerage = get_brokerage()
    snap = get_snapshot(brokerage)
    pf = load_portfolio()
    plan = build_plan(snap, pf, user_key=user, account_name=account)
    print()
    print_plan(plan)