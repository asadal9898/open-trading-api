"""
매수 금액 산정 — 예산 → 슬롯 → 종목당 금액 → 주문 수량.

설계 근거 (2026-07 백테스트 검증):
  1. 예산은 allocations.yaml 의 카테고리 배분 금액에서 나온다.
       moderate 예산 = 계좌 총평가금액 × moderate 비율(%)
     총자산이 변해도 비율이 자동 조정되므로 별도 관리가 필요 없다.

  2. 현금 하한을 먼저 뗀다.
       투자가능액 = 예산 × (1 - cash_floor%)
     검증: 현금을 0까지 소진하면 하락장에서 물타기·저점매수를 못 해
     성과가 크게 나빠졌다 (75종목 풀 기준 CAGR +5.7% vs 하한 적용 +8~9%).
     ※ cash_floor 의 '정확한 값'은 최적화된 것이 아니다. 10~20% 범위에서
       어느 값을 써도 비슷했고, 그 안의 차이는 노이즈였다.
       견고한 결론은 "현금을 0까지 소진하지 않는다" 하나뿐이다.

  3. 슬롯 수 = clamp(투자가능액 ÷ min_unit, 1, max_slots)   (내림)
     종목당 = 투자가능액 ÷ 슬롯수
     슬롯당 최소 min_unit(기본 100만원)을 보장하면서, 자산이 커지면
     max_slots 까지 분산을 늘리고 그 이상에서는 종목당 금액을 키운다.
     검증: 1,000만원과 2,000만원에서 CAGR 동등(+8.7%) — 규모 무관하게 작동.

  4. 주문 수량은 1주 단위 내림. 결과 금액이 min_order_amount 미만이면 건너뛴다
     (수수료 비효율 + 1주 단위 오차가 커짐).

⚠️ 계산만 한다. 주문·파일 수정 없음.

사용:
    from mytrading.position_sizing import plan_buy
    p = plan_buy("moderate", price=12000, total_equity=20_000_000)
    # {'slots': 17, 'per_symbol': 1_000_000, 'qty': 83, 'amount': 996_000, ...}

단독 실행 (현재 계좌 기준 표 출력):
    KIS_MODE=prod uv run python mytrading/position_sizing.py
"""
import math
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DEFAULTS = {
    "min_unit": 1_000_000,      # 슬롯당 최소 금액
    "max_slots": 20,            # 슬롯 상한
    "min_slots": 1,
    "cash_floor": 15.0,         # 현금 하한 % (최적값 아님 — 위 주석 참고)
    "min_order_amount": 300_000,  # 이 금액 미만이면 매수 건너뜀
    "allow_below_min_unit": True,   # 투자가능액 < min_unit 이어도 1종목 매수
}


def load_cfg() -> dict:
    """mytrading_config.yaml 의 position_sizing 블록 (없으면 기본값)."""
    cfg = dict(_DEFAULTS)
    try:
        from mytrading.common import CONFIG
        user = (CONFIG.get("position_sizing") or {})
    except Exception:
        try:
            import yaml
            raw = yaml.safe_load(
                (_ROOT / "mytrading" / "mytrading_config.yaml").read_text(
                    encoding="utf-8")) or {}
            user = raw.get("position_sizing") or {}
        except Exception:
            user = {}
    for k, v in (user or {}).items():
        if k in cfg and v is not None:
            cfg[k] = v
    return cfg


def category_amount(category: str, user: str = None,
                   account: str = None) -> Optional[float]:
    """allocations.yaml 에서 카테고리 배분 금액(원) 조회.

    user/account 를 생략하면 첫 번째 사용자·계좌를 쓴다.
    """
    try:
        import yaml
        data = yaml.safe_load(
            (_ROOT / "mytrading" / "configs" / "allocations.yaml").read_text(
                encoding="utf-8")) or {}
    except Exception as e:
        print(f"[position_sizing] allocations.yaml 읽기 실패: {e}")
        return None

    users = data.get("users") or {}
    if not users:
        return None
    ukey = user if user in users else next(iter(users))
    accounts = (users.get(ukey) or {}).get("accounts") or {}
    if not accounts:
        return None
    akey = account if account in accounts else next(iter(accounts))
    alloc = accounts.get(akey) or {}
    v = alloc.get(category)
    return float(v) if v is not None else None


def compute_slots(investable: float, cfg: dict = None):
    """투자가능액 → (슬롯수, 종목당 금액).

    슬롯당 최소 min_unit 을 보장. max_slots 도달 후에는 종목당 금액이 커진다.
    """
    cfg = cfg or load_cfg()
    min_unit = float(cfg["min_unit"])
    max_slots = int(cfg["max_slots"])
    min_slots = int(cfg.get("min_slots", 1))

    if investable <= 0:
        return 0, 0.0
    slots = int(investable // min_unit)
    if slots < 1 and not cfg.get("allow_below_min_unit", True):
        return 0, 0.0          # 슬롯당 최소금액 미달 → 자금 모일 때까지 대기
    slots = max(min_slots, min(slots, max_slots))
    return slots, investable / slots


def order_quantity(per_symbol: float, price: float, cfg: dict = None):
    """종목당 금액 + 현재가 → (수량, 실제 금액). 1주 단위 내림."""
    cfg = cfg or load_cfg()
    if price <= 0 or per_symbol <= 0:
        return 0, 0.0
    qty = int(math.floor(per_symbol / price))
    amount = qty * price
    if qty <= 0 or amount < float(cfg["min_order_amount"]):
        return 0, amount
    return qty, amount


def plan_buy(category: str, price: float, total_equity: float,
             user: str = None, account: str = None,
             held_count: int = 0, cfg: dict = None) -> dict:
    """매수 1건의 금액·수량 계산.

    category    : moderate / free
    price       : 현재가
    total_equity: 계좌 총평가금액
    held_count  : 해당 카테고리에서 이미 보유 중인 종목 수 (슬롯 여유 판단용)

    반환: {ratio, budget, cash_floor, investable, slots, slots_left,
           per_symbol, qty, amount, ok, reason}
    """
    cfg = cfg or load_cfg()
    out = {"category": category, "price": price, "total_equity": total_equity}

    ratio = category_amount(category, user, account)
    if ratio is None:
        out.update(ok=False, reason=f"allocations.yaml 에 {category} 배분금액 없음")
        return out

    budget = float(ratio)  # 금액 직접 (비율 환산 없음)
    floor_pct = float(cfg["cash_floor"])
    investable = budget * (1.0 - floor_pct / 100.0)
    slots, per_symbol = compute_slots(investable, cfg)
    slots_left = max(0, slots - held_count)

    out.update(amount=amount, budget=budget, cash_floor=floor_pct,
               investable=investable, slots=slots, slots_left=slots_left,
               per_symbol=per_symbol)

    if slots <= 0:
        out.update(ok=False, qty=0, amount=0.0, reason="투자가능액 부족")
        return out
    if slots_left <= 0:
        out.update(ok=False, qty=0, amount=0.0,
                   reason=f"슬롯 소진 (보유 {held_count}/{slots})")
        return out

    qty, amount = order_quantity(per_symbol, price, cfg)
    if qty <= 0:
        out.update(ok=False, qty=0, amount=amount,
                   reason=f"주문금액 {amount:,.0f}원 < 최소 "
                          f"{float(cfg['min_order_amount']):,.0f}원")
        return out

    out.update(ok=True, qty=qty, amount=amount, reason="")
    return out


def _demo():
    cfg = load_cfg()
    print("=== position_sizing 설정 ===")
    for k, v in cfg.items():
        print(f"  {k}: {v:,}" if isinstance(v, (int, float)) else f"  {k}: {v}")

    ratio = category_amount("moderate")
    print(f"\n  moderate 배분금액: {ratio:,.0f}원")

    total = None
    try:
        from mytrading.common import init, get_brokerage
        init(require_confirm=False)
        from mytrading.account_snapshot import get_snapshot
        snap = get_snapshot(get_brokerage())
        total = snap.total_equity
        print(f"  계좌 총평가금액: {total:,.0f}원  "
              f"(주문가능현금 {snap.available_cash:,.0f}원)")
    except Exception as e:
        print(f"  계좌 조회 생략: {str(e)[:60]}")

    print("\n=== 자산 규모별 슬롯·종목당 금액 ===")
    print(f"{'총평가':>14}{'예산':>13}{'투자가능':>13}{'슬롯':>5}{'종목당':>12}")
    print("-" * 58)
    samples = [5_000_000, 10_000_000, 20_000_000, 50_000_000, 100_000_000]
    if total:
        samples.append(int(total))
    for eq in sorted(set(samples)):
        r = plan_buy("moderate", price=10_000, total_equity=eq, cfg=cfg)
        mark = "  ← 현재" if total and eq == int(total) else ""
        print(f"{eq:>14,}{r.get('budget',0):>13,.0f}"
              f"{r.get('investable',0):>13,.0f}"
              f"{r.get('slots',0):>5}{r.get('per_symbol',0):>12,.0f}{mark}")

    if total:
        print("\n=== 현재 계좌 기준 매수 예시 (가격대별) ===")
        for px in (5_000, 12_000, 50_000, 150_000):
            r = plan_buy("moderate", price=px, total_equity=total, cfg=cfg)
            if r["ok"]:
                print(f"  {px:>8,}원 → {r['qty']:>4}주  "
                      f"{r['amount']:>11,.0f}원")
            else:
                print(f"  {px:>8,}원 → 매수 안 함 ({r['reason']})")


if __name__ == "__main__":
    _demo()