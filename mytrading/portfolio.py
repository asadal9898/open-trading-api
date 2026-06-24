"""
포트폴리오 설정 로더
- allocations.yaml : 유저/계좌별 비중 (현금/공격/보수/안전 %)
- universe.yaml    : 공용 종목풀 (안전/공격/보수 분류)

두 파일은 mytrading/ 에 위치 (민감정보 없음 → 저장소 포함 가능).
계좌 연결은 이름 기반: allocations 의 user/계좌이름 == kis_devlp.yaml users 의 계좌 name.

사용:
    from mytrading.portfolio import load_portfolio
    pf = load_portfolio()
    alloc = pf.allocation_for("Owner", "일반증권1")   # Allocation 또는 None
    codes = pf.symbols("aggressive")                  # 공격 종목 코드 리스트

CLI:
    uv run python mytrading/portfolio.py     # 비중/종목풀 요약 출력
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

_THIS_DIR = Path(__file__).resolve().parent
ALLOCATIONS_PATH = _THIS_DIR / "allocations.yaml"
UNIVERSE_PATH = _THIS_DIR / "universe.yaml"

_CATEGORIES = ("aggressive", "moderate", "safe")
_CAT_LABEL = {"aggressive": "공격", "moderate": "보수", "safe": "안전"}


@dataclass
class Allocation:
    """계좌 자금 비중 (%). 합 100 이어야 정상."""
    cash: float = 0.0
    aggressive: float = 0.0
    moderate: float = 0.0
    safe: float = 0.0

    @property
    def total(self) -> float:
        return self.cash + self.aggressive + self.moderate + self.safe

    @property
    def is_valid(self) -> bool:
        return abs(self.total - 100.0) < 0.1


@dataclass
class Portfolio:
    # allocations: {user_key: {account_name: Allocation}}
    allocations: Dict[str, Dict[str, Allocation]] = field(default_factory=dict)
    # universe: {category: [{code, name}]}
    universe: Dict[str, List[dict]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def allocation_for(self, user_key: str, account_name: str) -> Optional[Allocation]:
        """특정 유저/계좌의 비중. 없으면 None."""
        return self.allocations.get(user_key, {}).get(account_name)

    def symbols(self, category: str = None) -> List[str]:
        """종목 코드 리스트. category 지정 시 그 분류만, 없으면 전체."""
        if category:
            return [s["code"] for s in self.universe.get(category, [])]
        out = []
        for cat in _CATEGORIES:
            out += [s["code"] for s in self.universe.get(cat, [])]
        return out

    def names(self, category: str) -> List[dict]:
        """분류별 종목 [{code, name}] 리스트."""
        return self.universe.get(category, [])


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_portfolio(alloc_path: Path = ALLOCATIONS_PATH,
                   uni_path: Path = UNIVERSE_PATH) -> Portfolio:
    """allocations.yaml + universe.yaml 로드 + 검증."""
    warnings: List[str] = []

    # --- 비중 ---
    araw = _load_yaml(alloc_path)
    allocations: Dict[str, Dict[str, Allocation]] = {}
    for ukey, ublock in (araw.get("users", {}) or {}).items():
        accts = (ublock or {}).get("accounts", {}) or {}
        for acc_name, vals in accts.items():
            if not isinstance(vals, dict):
                continue
            al = Allocation(
                cash=float(vals.get("cash", 0) or 0),
                aggressive=float(vals.get("aggressive", 0) or 0),
                moderate=float(vals.get("moderate", 0) or 0),
                safe=float(vals.get("safe", 0) or 0),
            )
            if not al.is_valid:
                warnings.append(
                    f"{ukey}/{acc_name}: 비중 합 {al.total:.0f}% (100 아님) → 확인 필요")
            allocations.setdefault(ukey, {})[acc_name] = al

    # --- 종목풀 ---
    uraw = _load_yaml(uni_path)
    universe: Dict[str, List[dict]] = {}
    for cat in _CATEGORIES:
        items = uraw.get(cat) or []
        clean = []
        for it in items:
            if isinstance(it, dict) and str(it.get("code", "")).strip():
                clean.append({"code": str(it["code"]).strip(),
                              "name": str(it.get("name", "")).strip()})
        universe[cat] = clean

    return Portfolio(allocations=allocations, universe=universe, warnings=warnings)


def print_portfolio(pf: Optional[Portfolio] = None) -> None:
    if pf is None:
        pf = load_portfolio()

    print("=== 비중 (allocations) ===")
    if not pf.allocations:
        print("  (설정 없음)")
    for ukey, accts in pf.allocations.items():
        print(f"[{ukey}]")
        for name, al in accts.items():
            print(f"  - {name}: 현금 {al.cash:.0f} / 공격 {al.aggressive:.0f} "
                  f"/ 보수 {al.moderate:.0f} / 안전 {al.safe:.0f} (합 {al.total:.0f})")

    print("\n=== 종목풀 (universe) ===")
    for cat in _CATEGORIES:
        items = pf.names(cat)
        label = _CAT_LABEL[cat]
        if items:
            names = ", ".join(f"{s['name']}({s['code']})" for s in items)
            print(f"  {label}: {names}")
        else:
            print(f"  {label}: (없음)")

    if pf.warnings:
        print("\n⚠️ 경고:")
        for w in pf.warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    print_portfolio()