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
ALLOCATIONS_PATH = _THIS_DIR / "configs" / "allocations.yaml"
UNIVERSE_PATH = _THIS_DIR / "configs" / "universe_ko.yaml"

_CATEGORIES = ("moderate",)
_CAT_LABEL = {"moderate": "보수"}


@dataclass
class Allocation:
    """계좌 자금 배분 (금액, 원). moderate·free 지정, cash 는 자동 계산."""
    moderate: float = 0.0
    free: float = 0.0
    free_symbols: List[dict] = field(default_factory=list)

    def cash(self, total_equity: float) -> float:
        """여유 현금 = 총자산 - moderate - free (자동)."""
        return float(total_equity) - self.moderate - self.free

    def is_valid(self, total_equity: float) -> bool:
        """moderate + free 가 총자산 이하이면 정상 (cash >= 0)."""
        return self.cash(total_equity) >= 0


@dataclass
class Portfolio:
    # allocations: {user_key: {account_name: Allocation}}
    allocations: Dict[str, Dict[str, Allocation]] = field(default_factory=dict)
    # universe: {category: [{code, name}]}
    universe: Dict[str, List[dict]] = field(default_factory=dict)
    # moderate confirm — 3단계-1: 계좌·모드별 3중 중첩으로 전환.
    # {user_key: {account_name: {mode: {종목코드: confirm 상태}}}}
    # allocations.yaml 의 users.{key}.accounts.{계좌}.{모드}.moderate_confirm 에서 옴
    # (배분액 moderate/free 와 같은 depth). tradable_symbols/paused_symbols 가
    # universe_ko.yaml 의 confirm 보다 먼저 이걸 본다.
    moderate_confirm: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def allocation_for(self, user_key: str, account_name: str,
                       mode: str = "vps") -> Optional[Allocation]:
        """특정 유저/계좌/모드의 비중. '계좌|모드' 키 우선, 평면 키 폴백. 없으면 None."""
        accts = self.allocations.get(user_key, {})
        return (accts.get(f"{account_name}|{mode}")
                or accts.get(account_name))

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

    # moderate confirm 임시 하드코딩 상수 — 3단계-2에서 tradable_symbols/paused_symbols
    # 에 account/mode 인자가 생겨서 이제 "아무도 안 넘겼을 때만" 쓰는 순수 폴백이 됨
    # (2단계-1 이후의 user_key 와 동일한 역할). D가 실제로 다루는 유일한 조합
    # (Owner/일반증권/vps)과 정확히 일치하므로 기존 호출부는 전부 회귀 0.
    _CONFIRM_OWNER = "Owner"
    _CONFIRM_ACCOUNT = "일반증권"
    _CONFIRM_MODE = "vps"

    def tradable_symbols(self, category: str = None, user_key: str = None,
                         account: str = None, mode: str = None) -> List[str]:
        """매매 가능 종목 코드만 (유저confirm/confirm/auto_confirm 합성 판정).

        user_key/account/mode 각각 생략(None)이면 _CONFIRM_OWNER/_CONFIRM_ACCOUNT/
        _CONFIRM_MODE 로 폴백 — 기존 호출부(build_plan 등)는 전부 이 경로라 동작이
        그대로다(회귀 0). 셋 다 명시하면 그 (유저,계좌,모드)의 moderate_confirm 을 본다.

        판정 우선순위:
          1) self.moderate_confirm[user][account][mode][code] (allocations.yaml,
             계좌·모드별 신규 저장소 — 배분액 moderate/free 와 같은 depth)
          2) universe_ko.yaml 의 confirm(사람) — 마이그레이션 후 더 이상 안 써지는
             동결된 값이지만, 폴백으로 계속 읽는다(신규 위치가 비어있을 때 안전망).
          3) auto_confirm(자동, score_dividend.py)
        1)/2) 어느 쪽이든 값이 있으면 그 값만으로 판정하고(Approval 만 통과), 3)은 1)/2)
        둘 다 없을 때만 본다. 둘 다 없으면 매매 불가(기존과 동일 — 명시적 승인만 매매).
        """
        cats = [category] if category else _CATEGORIES
        user_confirm = (self.moderate_confirm.get(user_key or self._CONFIRM_OWNER, {})
                            .get(account or self._CONFIRM_ACCOUNT, {})
                            .get(mode or self._CONFIRM_MODE, {}))
        out = []
        for cat in cats:
            for s in self.universe.get(cat, []):
                code = s["code"]
                confirm = user_confirm.get(str(code).zfill(6))
                if confirm is None:
                    confirm = s.get("confirm")
                if confirm is not None:
                    ok = (confirm == "Approval")
                else:
                    ok = (s.get("auto_confirm") == "Approval")
                if ok:
                    out.append(code)
        return out

    def paused_symbols(self, category: str = None, user_key: str = None,
                       account: str = None, mode: str = None) -> List[str]:
        """confirm == "Paused" 인 종목 코드 (보유 유지, 신규매매 중단).
        인자/판정 우선순위는 tradable_symbols 와 동일(생략 시 Owner/일반증권/vps
        폴백, 회귀 0)."""
        cats = [category] if category else _CATEGORIES
        user_confirm = (self.moderate_confirm.get(user_key or self._CONFIRM_OWNER, {})
                            .get(account or self._CONFIRM_ACCOUNT, {})
                            .get(mode or self._CONFIRM_MODE, {}))
        out = []
        for cat in cats:
            for s in self.universe.get(cat, []):
                code = s["code"]
                confirm = user_confirm.get(str(code).zfill(6))
                if confirm is None:
                    confirm = s.get("confirm")
                if confirm == "Paused":
                    out.append(code)
        return out


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 계좌체계 재설계 1단계(2026-09-02): allocations.yaml 은 이제 "계좌명 = 실전/모의"
# 인 평면 구조(accounts.{모의투자증권/일반투자증권/ISA증권}.{moderate,free,
# moderate_confirm})를 쓴다. 하지만 D 등 7개 스크립트·텔레그램 봇은 여전히 예전
# 구조("계좌|모드" 합성키, moderate_confirm[유저][계좌][모드])를 전제로 동작 —
# 이 매핑으로 새 YAML 을 읽되 내부적으로는 예전과 동일한 모양으로 재조립해서
# 그 쪽은 전부 무변경으로 둔다(회귀 0). 2단계(resolve_mode/게이트 재설계)에서
# 이 매핑을 걷어내고 호출부들이 새 계좌명을 직접 쓰게 바꿀 예정.
_ACCOUNT_MODE_MAP = {
    "모의투자증권": ("일반증권", "vps"),
    "일반투자증권": ("일반증권", "prod"),
    "ISA증권": ("ISA", "prod"),
}


def load_portfolio(alloc_path: Path = ALLOCATIONS_PATH,
                   uni_path: Path = UNIVERSE_PATH) -> Portfolio:
    """allocations.yaml + universe.yaml 로드 + 검증."""
    warnings: List[str] = []

    # --- 비중 ---
    araw = _load_yaml(alloc_path)
    allocations: Dict[str, Dict[str, Allocation]] = {}
    # free_holdings 섹션 (사용자>계좌>종목) — 오늘 구조. 전체 필드 보존.
    _fh_raw = araw.get("free_holdings", {}) or {}
    def _fh_for(ukey, acc_name):
        items = ((_fh_raw.get(ukey) or {}).get(acc_name)) or []
        out = []
        for it in items:
            if isinstance(it, dict) and str(it.get("code", "")).strip():
                entry = {"code": str(it["code"]).strip(),
                         "name": str(it.get("name", "")).strip()}
                for k in ("style", "note", "added_by", "confirm",
                          "added_date", "dividend", "sector", "industry"):
                    if it.get(k) is not None:
                        entry[k] = it[k]
                out.append(entry)
        return out

    # moderate confirm — 3단계-1: {user: {account: {mode: {code: state}}}}.
    # 계좌체계 재설계 1단계(2026-09-02)부터는 아래 _ACCOUNT_MODE_MAP 을 거쳐
    # 채운다(같은 depth) — 자세한 내용은 _ACCOUNT_MODE_MAP 주석 참고.
    moderate_confirm: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = {}

    for ukey, ublock in (araw.get("users", {}) or {}).items():
        accts = (ublock or {}).get("accounts", {}) or {}
        for acc_name, vals in accts.items():
            if not isinstance(vals, dict):
                continue
            if acc_name not in _ACCOUNT_MODE_MAP:
                # "일반증권" 처럼 trading_active 만 남은 잔재 블록 — raw YAML 로
                # 직접 읽는 D/ETF매도/봇 3곳을 위한 하위호환용이라 여기(portfolio.py
                # 의 Allocation/moderate_confirm 파싱) 대상이 아니다(2단계에서 정리).
                # moderate/free/vps/prod 키가 있는데 매핑에 없으면 설정 오류일
                # 가능성이 커서 경고만 남기고 무시한다(배분 유실 방지용 안전장치).
                if any(k in vals for k in ("moderate", "free", "vps", "prod")):
                    warnings.append(
                        f"{ukey}/{acc_name}: 미등록 계좌명 — 배분 무시됨 "
                        f"(_ACCOUNT_MODE_MAP 확인, 계좌체계 재설계 1단계)")
                continue
            legacy_name, mode = _ACCOUNT_MODE_MAP[acc_name]

            # 자유 종목: free_holdings 우선, 없으면 free_symbols 폴백
            # ⚠️ 조회 키는 루프변수(acc_name, 새 계좌명)가 아니라 legacy_name —
            # free_holdings.yaml 은 예전 계좌명("일반증권")으로 저장돼 있다.
            free_syms = _fh_for(ukey, legacy_name)
            if not free_syms:
                for it in (vals.get("free_symbols") or []):
                    if isinstance(it, dict) and str(it.get("code", "")).strip():
                        free_syms.append({"code": str(it["code"]).strip(),
                                          "name": str(it.get("name", "")).strip()})
            al = Allocation(
                moderate=float(vals.get("moderate", 0) or 0),
                free=float(vals.get("free", 0) or 0),
                free_symbols=free_syms,
            )
            if al.free > 0 and not free_syms:
                warnings.append(
                    f"{ukey}/{legacy_name}/{mode}: free {al.free:,.0f}원인데 free_symbols 없음")
            # 키: "계좌|모드" 로 저장 (allocation_for 에서 분해) — legacy_name 기준이라
            # 예전과 동일한 키("일반증권|vps" 등), 기존 호출부 전부 회귀 0.
            allocations.setdefault(ukey, {})[f"{legacy_name}|{mode}"] = al

            # moderate confirm(3단계-1) — 같은 vals(계좌 블록)에서 같이 뽑음
            mc = vals.get("moderate_confirm") or {}
            if mc:
                (moderate_confirm.setdefault(ukey, {})
                                .setdefault(legacy_name, {})
                                [mode]) = {str(k).zfill(6): v for k, v in mc.items()}

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
                # 선택 필드 보존 (style/note/cadence/slice — 1-b, confirm — 종목 상태,
                #   auto_confirm — 자동판정 상태(confirm/auto_confirm 합성 게이트),
                #   score/mcap_score/div_score/debt_score/scored_date — 배당 스코어링)
                for k in ("style", "note", "cadence", "slice",
                          "confirm", "auto_confirm", "added_date",
                          "dividend", "sector", "industry",
                          "score", "mcap_score", "div_score", "debt_score", "scored_date"):
                    if it.get(k) is not None:
                        entry[k] = it[k]
                clean.append(entry)
        universe[cat] = clean

    return Portfolio(allocations=allocations, universe=universe,
                     moderate_confirm=moderate_confirm, warnings=warnings)

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
            print(f"  - {name}: 보수 {al.moderate:,.0f}원 / 자유 {al.free:,.0f}원 "
                  f"(cash=총자산-이 둘, 자동)")
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