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
    free: float = 0.0          # 자유 투자 (개인별 재량 종목)
    # 개인별 자유 종목 [{code, name}] — free 비중으로 살 종목 (사람마다 다름)
    free_symbols: List[dict] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.cash + self.aggressive + self.moderate + self.safe + self.free

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

    def tradable_symbols(self, category: str = None) -> List[str]:
        """confirm == "Approval" 인 종목 코드만 (실제 매매 대상).
        confirm 없으면 "Waiting" 취급 → 제외 (안전: 명시적 승인만 매매)."""
        cats = [category] if category else _CATEGORIES
        out = []
        for cat in cats:
            for s in self.universe.get(cat, []):
                if s.get("confirm", "Waiting") == "Approval":
                    out.append(s["code"])
        return out

    def paused_symbols(self, category: str = None) -> List[str]:
        """confirm == "Paused" 인 종목 코드 (보유 유지, 신규매매 중단)."""
        cats = [category] if category else _CATEGORIES
        out = []
        for cat in cats:
            for s in self.universe.get(cat, []):
                if s.get("confirm") == "Paused":
                    out.append(s["code"])
        return out


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
            # free_symbols(개인별 자유 종목) 파싱
            free_syms = []
            for it in (vals.get("free_symbols") or []):
                if isinstance(it, dict) and str(it.get("code", "")).strip():
                    free_syms.append({"code": str(it["code"]).strip(),
                                      "name": str(it.get("name", "")).strip()})
            al = Allocation(
                cash=float(vals.get("cash", 0) or 0),
                aggressive=float(vals.get("aggressive", 0) or 0),
                moderate=float(vals.get("moderate", 0) or 0),
                safe=float(vals.get("safe", 0) or 0),
                free=float(vals.get("free", 0) or 0),
                free_symbols=free_syms,
            )
            if not al.is_valid:
                warnings.append(
                    f"{ukey}/{acc_name}: 비중 합 {al.total:.0f}% (100 아님) → 확인 필요")
            # free 비중이 있는데 자유 종목이 없으면 안내
            if al.free > 0 and not free_syms:
                warnings.append(
                    f"{ukey}/{acc_name}: free 비중 {al.free:.0f}%인데 free_symbols 없음")
            allocations.setdefault(ukey, {})[acc_name] = al

    # --- 종목풀 ---
    uraw = _load_yaml(uni_path)
    universe: Dict[str, List[dict]] = {}
    for cat in _CATEGORIES:
        items = uraw.get(cat) or []
        clean = []
        for it in items:
            if isinstance(it, dict) and str(it.get("code", "")).strip():
                entry = {"code": str(it["code"]).strip(),
                         "name": str(it.get("name", "")).strip()}
                # 선택 필드 보존 (style/note/cadence/slice — 1-b, confirm/added_by — 종목 상태)
                for k in ("style", "note", "cadence", "slice",
                          "added_by", "confirm", "added_date", "dividend", "sector"):
                    if it.get(k) is not None:
                        entry[k] = it[k]
                clean.append(entry)
        universe[cat] = clean

    return Portfolio(allocations=allocations, universe=universe, warnings=warnings)

def get_watch_symbols(config: dict = None) -> list:
    """
    조회/주문 대상 종목 코드 리스트를 반환.
    우선순위: universe.yaml 종목풀 → (비면) mytrading_config.yaml 의 trading.symbols → ["005930"]
    config: mytrading_config.yaml 로드 딕셔너리 (폴백용, 없으면 universe/기본값만)
    """
    pf = load_portfolio()
    syms = pf.symbols()  # universe 전체 (공격+보수+안전)
    if syms:
        return syms
    # 폴백: config 의 trading.symbols
    if config:
        cfg_syms = (config.get("trading", {}) or {}).get("symbols")
        if cfg_syms:
            return list(cfg_syms)
    return ["005930"]


def get_symbol_names() -> dict:
    """종목코드 → 이름 매핑 (universe 기준). 표시용."""
    pf = load_portfolio()
    out = {}
    for cat in _CATEGORIES:
        for s in pf.names(cat):
            out[s["code"]] = s["name"]
    return out


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
                  f"/ 보수 {al.moderate:.0f} / 안전 {al.safe:.0f} / 자유 {al.free:.0f} "
                  f"(합 {al.total:.0f})")
            if al.free_symbols:
                fs = ", ".join(f"{s['name']}({s['code']})" for s in al.free_symbols)
                print(f"      자유종목: {fs}")

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