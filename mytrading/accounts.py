"""
멀티 계좌/사용자 로더
~/KIS/config/kis_devlp.yaml 의 users 섹션을 읽어 사용자·계좌 구조를 제공한다. (읽기 전용)

설계:
  - 앱키는 계좌 종류마다 따로 (일반/ISA/IRP 각각). KIS 정책.
  - IRP 는 주문 불가 → can_order=false (로더가 강제).
  - users 아래 사람 단위(Owner + 기타). 최대 max_users 명.
  - users 섹션이 없으면 → 멀티계좌 미사용 상태(빈 구조). 기존 kis_devlp.yaml 단일계정 흐름과 공존.

사용:
    from mytrading.accounts import load_accounts, list_accounts
    data = load_accounts()
    if data.enabled:
        for u in data.users: ...

CLI 확인:
    uv run python mytrading/accounts.py            # 계좌 목록 출력 (민감정보 가림)
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

ACCOUNTS_PATH = Path.home() / "KIS" / "config" / "kis_devlp.yaml"

# 필수 필드 (yaml 키 기준: kis_devlp 와 통일된 my_app/my_sec)
_ACCOUNT_REQUIRED = ("name", "my_app", "my_sec", "my_acct_stock", "prod")
# IRP 판별 키워드 (이름에 IRP/연금 들어가거나 prod 가 연금계열이면 주문 차단 권고)
_PENSION_PRODS = {"22", "29"}  # 22=개인연금, 29=퇴직연금


@dataclass
class Allocation:
    """계좌 자금 배분 (금액, 원). moderate·free 지정, cash 는 자동 계산."""
    moderate: float = 0.0
    free: float = 0.0

    def cash(self, total_equity: float) -> float:
        """여유 현금 = 총자산 - moderate - free."""
        return float(total_equity) - self.moderate - self.free

    def is_valid(self, total_equity: float) -> bool:
        """moderate + free 가 총자산 이하이면 정상 (cash >= 0)."""
        return self.cash(total_equity) >= 0


@dataclass
class Account:
    name: str
    app_key: str
    app_secret: str
    acct_stock: str               # 실전 증권계좌 8자리 (yaml: my_acct_stock)
    prod: str
    can_order: bool = True
    paper_app: Optional[str] = None
    paper_sec: Optional[str] = None
    paper_stock: Optional[str] = None  # 모의 증권계좌 8자리 (yaml: my_paper_stock)
    allocation: Optional[Allocation] = None   # 비중 설정 (없으면 None)
    # 종목 리스트: {"moderate": [{code,name}]}
    universe: dict = field(default_factory=dict)

    @property
    def has_paper(self) -> bool:
        # 모의 거래 가능하려면 모의 앱키 + 모의 계좌번호 둘 다 필요
        return bool(self.paper_app and self.paper_sec and self.paper_stock)

    def account_no(self, is_paper: bool) -> str:
        """모드에 맞는 계좌번호 반환."""
        return self.paper_stock if is_paper else self.acct_stock

    def symbols(self, category: str = None) -> list:
        """종목 코드 리스트. category 지정 시 그 분류만, 없으면 전체."""
        if category:
            return [s["code"] for s in self.universe.get(category, [])]
        out = []
        for cat in ("moderate",):
            out += [s["code"] for s in self.universe.get(cat, [])]
        return out


@dataclass
class User:
    key: str                      # users 아래 키 (Owner, brother 등)
    name: str
    role: str = "trader"          # owner / trader
    telegram_chat_id: str = ""
    notify_day: str = ""          # 주간 알림 요일 (토/일/월... 또는 sat/sun, 빈값=기본 토)
    notify_time: str = ""         # 주간 알림 시각 (HH:MM, 빈값=기본 15:00)
    accounts: List[Account] = field(default_factory=list)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner" or self.key == "Owner"


@dataclass
class AccountsData:
    enabled: bool                 # accounts.yaml 존재 여부
    users: List[User] = field(default_factory=list)
    max_users: int = 10
    warnings: List[str] = field(default_factory=list)

    def get_user(self, key: str) -> Optional[User]:
        return next((u for u in self.users if u.key == key), None)

    @property
    def owner(self) -> Optional[User]:
        return next((u for u in self.users if u.is_owner), None)


def load_accounts(path: Path = ACCOUNTS_PATH) -> AccountsData:
    """
    kis_devlp.yaml 의 users 섹션을 로드 + 검증.
    파일이 없거나 users 섹션이 없으면 enabled=False (기존 단일계정 흐름 유지).
    """
    if not path.exists():
        return AccountsData(enabled=False)

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # users 섹션이 없으면 단일계정 모드 (멀티계좌 미사용)
    if not raw.get("users"):
        return AccountsData(enabled=False)

    max_users = int(raw.get("max_users", 10))
    warnings: List[str] = []
    users: List[User] = []

    users_raw = raw.get("users", {}) or {}
    for ukey, ublock in users_raw.items():
        if not isinstance(ublock, dict):
            warnings.append(f"사용자 '{ukey}' 형식 오류 → 건너뜀")
            continue

        accounts: List[Account] = []
        for acc in ublock.get("accounts", []) or []:
            # 필수 필드 체크 (값이 비어있으면 미완성으로 보고 건너뜀)
            missing = [k for k in _ACCOUNT_REQUIRED if not str(acc.get(k, "")).strip()]
            if missing:
                warnings.append(
                    f"{ukey}/{acc.get('name','?')}: 필드 미입력 {missing} → 건너뜀")
                continue

            prod = str(acc["prod"]).strip()
            can_order = bool(acc.get("can_order", True))
            # IRP/연금 계열은 주문 불가 강제 (안전장치)
            name = str(acc["name"]).strip()
            if ("IRP" in name.upper() or prod in _PENSION_PRODS) and can_order:
                warnings.append(
                    f"{ukey}/{name}: 연금/IRP 계열 → can_order 를 false 로 강제 (주문 불가)")
                can_order = False

            # 비중(allocation) 파싱 (선택)
            alloc = None
            alloc_raw = acc.get("allocation")
            if isinstance(alloc_raw, dict):
                alloc = Allocation(
                    moderate=float(alloc_raw.get("moderate", 0) or 0),
                    free=float(alloc_raw.get("free", 0) or 0),
                )
                # cash 음수(moderate+free>총자산) 검증은 총자산 아는 사용처에서

            # 종목 리스트(universe) 파싱 (선택)
            universe = {}
            uni_raw = acc.get("universe")
            if isinstance(uni_raw, dict):
                for cat in ("moderate",):
                    items = uni_raw.get(cat) or []
                    clean = []
                    for it in items:
                        if isinstance(it, dict) and str(it.get("code", "")).strip():
                            clean.append({"code": str(it["code"]).strip(),
                                          "name": str(it.get("name", "")).strip()})
                    if clean:
                        universe[cat] = clean

            accounts.append(Account(
                name=name,
                app_key=str(acc["my_app"]).strip(),
                app_secret=str(acc["my_sec"]).strip(),
                acct_stock=str(acc["my_acct_stock"]).strip(),
                prod=prod,
                can_order=can_order,
                paper_app=(str(acc["paper_app"]).strip() if acc.get("paper_app") else None),
                paper_sec=(str(acc["paper_sec"]).strip() if acc.get("paper_sec") else None),
                paper_stock=(str(acc["my_paper_stock"]).strip() if acc.get("my_paper_stock") else None),
                allocation=alloc,
                universe=universe,
            ))

        if not accounts:
            warnings.append(f"사용자 '{ukey}': 유효한 계좌 없음 → 건너뜀")
            continue

        users.append(User(
            key=ukey,
            name=str(ublock.get("name", ukey)),
            role=str(ublock.get("role", "trader")),
            telegram_chat_id=str(ublock.get("telegram_chat_id", "")),
            notify_day=str(ublock.get("notify_day", "")).strip(),
            notify_time=str(ublock.get("notify_time", "")).strip(),
            accounts=accounts,
        ))

    # 최대 인원 제한
    if len(users) > max_users:
        warnings.append(f"사용자 {len(users)}명 > 최대 {max_users}명 → 초과분 잘림")
        users = users[:max_users]

    enabled = len(users) > 0
    return AccountsData(enabled=enabled, users=users,
                        max_users=max_users, warnings=warnings)


def list_accounts(data: Optional[AccountsData] = None) -> None:
    """계좌 목록 출력 (앱키는 가림)."""
    if data is None:
        data = load_accounts()
    if not data.enabled:
        print("users 섹션 없음 또는 유효 계좌 없음 → 단일계정 모드")
        return
    print(f"등록 사용자: {len(data.users)}명 (최대 {data.max_users})")
    for u in data.users:
        tag = "👑owner" if u.is_owner else f"role={u.role}"
        print(f"\n[{u.key}] {u.name} ({tag})")
        for a in u.accounts:
            order = "주문O" if a.can_order else "조회만"
            paper = "+모의" if a.has_paper else ""
            print(f"  - {a.name}: {a.acct_stock}/{a.prod} [{order}]{paper}")
            if a.allocation:
                al = a.allocation
                print(f"      배분: 보수 {al.moderate:,.0f}원 / 자유 {al.free:,.0f}원 "
                      f"(cash=총자산-이 둘)")
            if a.universe:
                for cat, label in (("moderate","보수"),):
                    items = a.universe.get(cat, [])
                    if items:
                        names = ", ".join(f"{s['name']}({s['code']})" for s in items)
                        print(f"      {label}: {names}")
    if data.warnings:
        print("\n⚠️ 경고:")
        for w in data.warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    list_accounts()