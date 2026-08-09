"""
mytrading 공통 모듈
- 모드 결정: 환경변수 KIS_MODE 우선 (없으면 vps=모의). 실전(prod)은 환경변수로만 가능
- KIS 인증 + 실전 안전 가드(YES 타이핑 확인)
- mytrading_config.yaml 로드 (모드 외 일반 설정)
- get_data_provider(): 백테스트용 KISDataProvider 생성 (모드에 맞는 키 자동 선택)

실행:
    uv run python mytrading/runners/check_auth.py            # 모의(vps)
    KIS_MODE=prod uv run python mytrading/runners/check_auth.py   # 실전(확인 필요)
"""
import os
import sys
from pathlib import Path

import yaml
import kis_auth as ka  # backtester/kis_auth.py (editable 설치로 import 가능)

# ----- 경로 -----
_THIS_DIR = Path(__file__).resolve().parent          # .../mytrading
CONFIG_PATH = _THIS_DIR / "configs" / "mytrading_config.yaml"
KIS_DEVLP_PATH = Path(os.path.expanduser("~")) / "KIS" / "config" / "kis_devlp.yaml"

# 유효한 모드
_VALID_MODES = ("vps", "prod")
_MODE_LABEL = {"vps": "모의투자", "prod": "실전투자"}


def load_config() -> dict:
    """mytrading_config.yaml 로드. 없으면 빈 dict."""
    if not CONFIG_PATH.exists():
        print(f"[WARN] 설정 파일 없음: {CONFIG_PATH} (기본값으로 진행)")
        return {}
    with open(CONFIG_PATH, encoding="UTF-8") as f:
        return yaml.safe_load(f) or {}


CONFIG = load_config()


def _load_kis_devlp() -> dict:
    """~/KIS/config/kis_devlp.yaml 로드 (앱키/시크릿/계좌)."""
    if not KIS_DEVLP_PATH.exists():
        print(f"[FAIL] KIS 설정 파일 없음: {KIS_DEVLP_PATH}")
        print("       setup.sh 를 실행했는지, 키를 입력했는지 확인하세요.")
        sys.exit(1)
    with open(KIS_DEVLP_PATH, encoding="UTF-8") as f:
        return yaml.safe_load(f) or {}


# 계좌 정보를 한 실행에서 한 번만 출력하기 위한 플래그
_account_printed = None


def _print_account_once(acc: dict):
    """선택된 계좌를 한 번만 출력 (같은 계좌 중복 출력 방지)."""
    global _account_printed
    key = (acc["source"], acc["account_no"])
    if _account_printed != key:
        print(f"  [account] {acc['source']} (계좌 {acc['account_no']})")
        _account_printed = key


def _resolve_account(is_paper: bool, account_name: str = None) -> dict:
    """
    현재 사용할 계좌의 키/계좌번호를 결정.
    - kis_devlp.yaml 에 users 섹션이 있으면: 선택된 계좌(기본 Owner 첫 주문가능 계좌) 사용
    - 없으면: 기존 단일계정 키(my_app/paper_app 등) 사용 (하위호환)

    계좌 선택은 환경변수로 지정 가능:
      KIS_USER     : 사용자 키 (기본 Owner)
      KIS_ACCOUNT  : 계좌 이름 (기본 첫 주문가능 계좌)

    반환: {app_key, app_secret, account_no, source, can_order}
    """
    # 멀티계좌 시도
    try:
        from mytrading.accounts import load_accounts
        adata = load_accounts()
    except Exception:
        adata = None

    if adata and adata.enabled:
        user_key = os.environ.get("KIS_USER", "").strip()
        acct_name = (account_name or os.environ.get("KIS_ACCOUNT", "")).strip()

        user = adata.get_user(user_key) if user_key else (adata.owner or adata.users[0])
        if user is None:
            user = adata.users[0]

        acct = None
        if acct_name:
            acct = next((a for a in user.accounts if a.name == acct_name), None)
        if acct is None:
            acct = next((a for a in user.accounts if a.can_order), None) or user.accounts[0]

        # 모의 모드인데 그 계좌에 모의 키가 없으면 단일계정 폴백
        if is_paper and not acct.has_paper:
            print(f"  [account] {user.key}/{acct.name} 에 모의 키 없음 → 단일계정 폴백")
        else:
            return {
                "app_key": acct.paper_app if is_paper else acct.app_key,
                "app_secret": acct.paper_sec if is_paper else acct.app_secret,
                "account_no": acct.account_no(is_paper),
                "source": f"{user.key}/{acct.name}",
                "can_order": acct.can_order,
                "prod": acct.prod,
            }

    # 하위호환: 기존 단일계정 키
    cfg = _load_kis_devlp()
    if is_paper:
        return {"app_key": cfg["paper_app"], "app_secret": cfg["paper_sec"],
                "account_no": str(cfg["my_paper_stock"]),
                "source": "단일계정(모의)", "can_order": True, "prod": str(cfg.get("my_prod", "01"))}
    else:
        return {"app_key": cfg["my_app"], "app_secret": cfg["my_sec"],
                "account_no": str(cfg["my_acct_stock"]),
                "source": "단일계정(실전)", "can_order": True, "prod": str(cfg.get("my_prod", "01"))}


def _invalidate_token_if_mode_changed(is_paper: bool, account_name: str = None):
    """
    모의/실전 모드가 직전과 다르면 토큰 캐시를 비운다.
    kis_auth 는 모의/실전이 같은 날짜 파일(KIS{YYYYMMDD})을 공유하므로,
    모드 전환 시 이전 토큰을 그대로 쓰면 'EGW00123 만료/무효' 에러가 난다.
    → 모드가 바뀔 때만 비워서 강제 재발급 (같은 모드면 토큰 재사용 — 효율적).

    ※ 각 실행이 별도 프로세스라 마지막 모드를 파일(~/KIS/cache/.last_mode)에 기록해
       프로세스가 바뀌어도 모드 전환을 감지한다.
    """
    import kis_auth as ka
    mode = "vps" if is_paper else "prod"
    mode_file = Path.home() / "KIS" / "cache" / ".last_mode"
    mode_file.parent.mkdir(parents=True, exist_ok=True)

    last_mode = None
    try:
        if mode_file.exists():
            last_mode = mode_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass

    if last_mode is not None and last_mode != mode:
        try:
            if hasattr(ka, "token_tmp") and Path(ka.token_tmp).exists():
                Path(ka.token_tmp).unlink()
            # kis_auth 전역 인증도 리셋 (계좌 전환 시 이전 _TRENV 잔존 방지)
            try:
                ka._TRENV = tuple()
            except Exception:
                pass
        except Exception:
            pass

    try:
        mode_file.write_text(mode, encoding="utf-8")
    except Exception:
        pass


def _inject_auth_cfg(acc: dict, is_paper: bool):
    """
    원본 kis_auth 의 전역 _cfg 에 선택된 계좌 키를 주입한다.
    auth() 가 _cfg[my_app]/_cfg[paper_app] 등을 직접 읽으므로,
    auth() 호출 직전에 이 값을 선택된 계좌로 덮어써야 한다.
    (원본 kis_auth.py 는 수정하지 않고 전역만 덮어씀 — _smartSleep 패턴과 동일)
    """
    import kis_auth as ka
    # 모드 전환 시 토큰 무효화 (모의↔실전 충돌 방지)
    # 계좌별 토큰 파일 분리 — 같은 날짜 토큰을 계좌끼리 공유하지 않게
    try:
        import kis_auth as _ka
        from datetime import datetime as _dt
        _tag = (acc.get("source") or "_default").replace("/", "_")
        _m = "vps" if is_paper else "prod"
        _dirn = os.path.dirname(_ka.token_tmp)
        _ka.token_tmp = os.path.join(
            _dirn, f"KIS{_dt.today().strftime('%Y%m%d')}_{_m}_{_tag}")
    except Exception:
        pass
    _invalidate_token_if_mode_changed(is_paper, acc.get('source'))
    if is_paper:
        ka._cfg["paper_app"] = acc["app_key"]
        ka._cfg["paper_sec"] = acc["app_secret"]
        ka._cfg["my_paper_stock"] = acc["account_no"]
    else:
        ka._cfg["my_app"] = acc["app_key"]
        ka._cfg["my_sec"] = acc["app_secret"]
        ka._cfg["my_acct_stock"] = acc["account_no"]
    # 계좌 상품코드(prod)도 주입 (auth 의 product 기본값 및 계좌 조회에 사용)
    if acc.get("prod"):
        ka._cfg["my_prod"] = acc["prod"]


def resolve_mode() -> str:
    """
    모드 결정 우선순위:
      1) 환경변수 KIS_MODE  (prod는 여기서만 가능)
      2) mytrading_config.yaml 의 kis_mode  (단 vps만 허용 — 안전)
      3) 기본값 vps(모의)
    """
    env_mode = os.environ.get("KIS_MODE")

    if env_mode:
        env_mode = env_mode.strip().lower()
        if env_mode not in _VALID_MODES:
            print(f"[FAIL] KIS_MODE 값이 잘못됨: '{env_mode}' (vps 또는 prod만 가능)")
            sys.exit(1)
        return env_mode

    # 봇 /모드 파일 (.telegram_mode) — 봇에서 실전/모의 전환 (prod 허용)
    try:
        _mode_file = Path.home() / "KIS" / "config" / ".telegram_mode"
        if _mode_file.exists():
            _bm = _mode_file.read_text(encoding="utf-8").strip().lower()
            if _bm in _VALID_MODES:
                return _bm
    except Exception:
        pass

    yaml_mode = (CONFIG.get("kis_mode") or "").strip().lower()
    if yaml_mode == "prod":
        print("[FAIL] 실전(prod)은 설정 파일로 지정할 수 없습니다.")
        print("       실전은 환경변수로만 가능: KIS_MODE=prod uv run ...")
        sys.exit(1)
    if yaml_mode == "vps":
        return "vps"

    return "vps"


def init(require_confirm: bool = True):
    """
    모드 결정 → 인증 → 실전 가드 → 모드 출력.
    인증이 끝난 kis_auth 모듈(ka)을 반환.
    """
    mode = resolve_mode()

    # 선택된 계좌 키를 kis_auth 전역에 주입 후 인증
    is_paper = (mode == "vps")
    acc = _resolve_account(is_paper)
    _print_account_once(acc)
    _inject_auth_cfg(acc, is_paper)

    # 인증 (svr 항상 명시 — 기본값 prod에 절대 의존하지 않음)
    ka.auth(svr=mode)

    actual_paper = ka.isPaperTrading()
    actual_label = "모의투자" if actual_paper else "실전투자"

    print("=" * 50)
    if actual_paper:
        print(f"  현재 모드: ✅ {actual_label} (vps)")
    else:
        print(f"  현재 모드: 🚨 {actual_label} (prod) — 실제 주문이 체결됩니다")
    print("=" * 50)

    if not actual_paper and require_confirm:
        ans = input("🚨 실전투자 모드입니다. 진짜 돈으로 주문이 나갑니다.\n"
                    "   계속하려면 'YES' 를 정확히 입력하세요: ")
        if ans.strip() != "YES":
            print("실전 주문 취소됨. 종료합니다.")
            sys.exit(0)

    return ka


def resolve_date(value: str) -> str:
    """
    config 의 날짜 문자열을 실제 날짜(YYYY-MM-DD)로 변환.
      - "today" / "now" / 빈값 → 오늘 날짜
      - "YYYY-MM-DD"            → 그대로
    """
    from datetime import datetime
    if value is None or str(value).strip().lower() in ("today", "now", ""):
        return datetime.now().strftime("%Y-%m-%d")
    return str(value).strip()


def get_backtest_period() -> tuple:
    """config backtest 의 (start_date, end_date) 를 실제 날짜로 반환. end 는 today 지원."""
    bt = CONFIG.get("backtest", {})
    start = resolve_date(bt.get("start_date", "2024-01-01"))
    end = resolve_date(bt.get("end_date", "today"))
    return start, end


# 유효한 국면 값
_VALID_REGIMES = ("bull", "bear", "sideways", "toppish")


def get_regime(market: str = "domestic") -> str:
    """
    시장 국면 반환 (Owner 가 config 에 설정한 값).
    market: "domestic"(국내/코스피) / "overseas"(해외/미국 추종)
    반환: "bull" / "bear" / "sideways"  (미설정/오류 시 "sideways" 안전 기본)

    ※ 자동 판단이 아니라 사람이 config.market_regime 에 적어둔 상태값.
    """
    mr = CONFIG.get("market_regime", {}) or {}
    val = str(mr.get(market, "")).strip().lower()
    if val in _VALID_REGIMES:
        return val
    # 미설정/오타 시 중립(sideways) — 분할 결정에서 가장 보수적
    return "sideways"


def regime_for_symbol(symbol: str, overseas_symbols=None) -> str:
    """
    종목에 적용할 국면을 반환.
    해외 추종 종목(예: KODEX 미국S&P500)은 overseas 국면, 나머지는 domestic.
    overseas_symbols: 해외 추종으로 볼 종목코드 집합 (없으면 전부 domestic).
    """
    overseas_symbols = set(overseas_symbols or [])
    market = "overseas" if symbol in overseas_symbols else "domestic"
    return get_regime(market)


def regime_info() -> dict:
    """국면 설정 전체 (메타 포함) 반환. 출력/확인용."""
    return dict(CONFIG.get("market_regime", {}) or {})


def get_data_provider():
    """
    백테스트용 KISDataProvider 생성.
    현재 모드(vps/prod)에 맞는 계좌(멀티계좌 users 또는 단일계정)를 자동 선택.

    주의: prod 선택 시에도 백테스트는 과거 데이터 조회일 뿐 실제 주문은 아님.
          단, 실전 키의 호출 제한/요금 정책이 적용될 수 있음.
    """
    from kis_backtest.providers.kis import KISAuth, KISDataProvider

    mode = resolve_mode()
    is_paper = (mode == "vps")
    acc = _resolve_account(is_paper)
    _print_account_once(acc)

    import kis_auth as ka
    # KISAuth 생성자가 내부에서 ka.auth 를 호출하므로, 그 전에 선택된 계좌 키를
    # 전역 _cfg 에 주입해야 한다. (안 그러면 ka.auth 가 _cfg["paper_app"] 등을
    # 못 찾아 KeyError. _inject 가 모드전환 토큰무효화도 함께 처리)
    _inject_auth_cfg(acc, is_paper)

    auth = KISAuth(
        app_key=acc["app_key"],
        app_secret=acc["app_secret"],
        account_no=acc["account_no"],
        is_paper=is_paper,
    )

    # --- 레이트리밋(EGW00201) 대응 ---
    # data.py 의 조회 호출은 kis_auth.smart_sleep() → time.sleep(_smartSleep) 를 사용한다.
    # 그런데 changeTREnv 가 _smartSleep 을 모드별로 갱신하지 못해(전역 반영 안 됨)
    # 기본 0.1초로 남아 모의 계좌 초당 호출 제한에 걸린다.
    # → 여기서 kis_auth 를 인증하고, config 값으로 _smartSleep 을 명시적으로 설정한다.
    #   (원본 코드는 수정하지 않고, 전역 변수만 덮어쓴다)
    ka.auth(svr=mode)  # 토큰 발급 + 전역 환경 설정

    # config 의 rate_limit 값으로 호출 간격 지정 (없으면 모드별 안전 기본값)
    rate_cfg = CONFIG.get("rate_limit", {})
    default_sleep = 1.0 if is_paper else 0.1   # 모의는 넉넉하게, 실전은 빠르게
    sleep_sec = float(rate_cfg.get(f"{mode}_sleep", default_sleep))
    ka._smartSleep = sleep_sec
    print(f"  [rate_limit] 조회 간격 {sleep_sec}s (mode={mode})")

    return KISDataProvider(auth)


def get_brokerage(account_name: str = None):
    """
    주문/잔고용 KISBrokerageProvider 생성.
    현재 모드(vps/prod)에 맞는 계좌(멀티계좌 users 또는 단일계정)를 자동 선택.

    ⚠️ 주의: 이 provider 로 submit_order 하면 실제 주문이 들어갑니다.
            vps(모의)면 모의 계좌, prod(실전)면 실제 계좌. 호출 전 반드시 init() 으로
            모드를 확인하세요.
    """
    from kis_backtest.providers.kis import KISAuth, KISBrokerageProvider

    mode = resolve_mode()
    is_paper = (mode == "vps")
    acc = _resolve_account(is_paper, account_name)
    _print_account_once(acc)
    if not acc.get("can_order", True):
        print(f"  ⚠️ [account] {acc['source']} 는 주문 불가 계좌(IRP 등)입니다. 조회만 가능.")

    import kis_auth as ka
    # KISAuth 생성자가 ka.auth 를 호출하므로, 그 전에 키를 _cfg 에 주입해야 한다.
    # (get_data_provider 와 동일한 이유 — paper_app KeyError 방지)
    _inject_auth_cfg(acc, is_paper)

    auth = KISAuth(
        app_key=acc["app_key"],
        app_secret=acc["app_secret"],
        account_no=acc["account_no"],
        is_paper=is_paper,
    )
    # 조회 호출 간격도 적용 (레이트리밋 대응)
    ka.auth(svr=mode)
    rate_cfg = CONFIG.get("rate_limit", {})
    ka._smartSleep = float(rate_cfg.get(f"{mode}_sleep", 1.0 if is_paper else 0.1))

    return KISBrokerageProvider.from_auth(auth)


def current_account_info() -> dict:
    """
    현재 모드에서 선택된 계좌 정보를 반환 (주문/조회 전 확인용).
    반환: {source, account_no, can_order}
    """
    mode = resolve_mode()
    is_paper = (mode == "vps")
    acc = _resolve_account(is_paper)
    return {"source": acc["source"], "account_no": acc["account_no"],
            "can_order": acc.get("can_order", True)}


def assert_can_order() -> bool:
    """
    현재 선택된 계좌가 주문 가능한지 확인.
    주문 불가(IRP 등)면 안내 출력 후 False 반환 → 러너는 주문 중단.
    """
    info = current_account_info()
    if not info["can_order"]:
        print(f"  🚫 {info['source']} 는 주문 불가 계좌입니다 (IRP 등 조회 전용).")
        print("     주문하려면 KIS_ACCOUNT 로 주문 가능한 계좌를 선택하세요.")
        return False
    return True


if __name__ == "__main__":
    print(f"설정 파일: {CONFIG_PATH} ({'있음' if CONFIG_PATH.exists() else '없음'})")
    print(f"결정된 모드: {resolve_mode()}")
    print(f"CONFIG keys: {list(CONFIG.keys())}")