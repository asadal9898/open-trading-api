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
CONFIG_PATH = _THIS_DIR / "mytrading_config.yaml"
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


def get_data_provider():
    """
    백테스트용 KISDataProvider 생성.
    현재 모드(vps/prod)에 맞는 앱키/시크릿/계좌를 kis_devlp.yaml 에서 선택.
    - vps  → paper_app / paper_sec / my_paper_stock  (is_paper=True)
    - prod → my_app / my_sec / my_acct_stock         (is_paper=False)

    주의: prod 선택 시에도 백테스트는 과거 데이터 조회일 뿐 실제 주문은 아님.
          단, 실전 키의 호출 제한/요금 정책이 적용될 수 있음.
    """
    from kis_backtest.providers.kis import KISAuth, KISDataProvider

    mode = resolve_mode()
    cfg = _load_kis_devlp()
    is_paper = (mode == "vps")

    if is_paper:
        app_key = cfg["paper_app"]
        app_secret = cfg["paper_sec"]
        account_no = str(cfg["my_paper_stock"])
    else:
        app_key = cfg["my_app"]
        app_secret = cfg["my_sec"]
        account_no = str(cfg["my_acct_stock"])

    auth = KISAuth(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_paper=is_paper,
    )

    # --- 레이트리밋(EGW00201) 대응 ---
    # data.py 의 조회 호출은 kis_auth.smart_sleep() → time.sleep(_smartSleep) 를 사용한다.
    # 그런데 changeTREnv 가 _smartSleep 을 모드별로 갱신하지 못해(전역 반영 안 됨)
    # 기본 0.1초로 남아 모의 계좌 초당 호출 제한에 걸린다.
    # → 여기서 kis_auth 를 인증하고, config 값으로 _smartSleep 을 명시적으로 설정한다.
    #   (원본 코드는 수정하지 않고, 전역 변수만 덮어쓴다)
    import kis_auth as ka

    ka.auth(svr=mode)  # 토큰 발급 + 전역 환경 설정

    # config 의 rate_limit 값으로 호출 간격 지정 (없으면 모드별 안전 기본값)
    rate_cfg = CONFIG.get("rate_limit", {})
    default_sleep = 1.0 if is_paper else 0.1   # 모의는 넉넉하게, 실전은 빠르게
    sleep_sec = float(rate_cfg.get(f"{mode}_sleep", default_sleep))
    ka._smartSleep = sleep_sec
    print(f"  [rate_limit] 조회 간격 {sleep_sec}s (mode={mode})")

    return KISDataProvider(auth)


if __name__ == "__main__":
    print(f"설정 파일: {CONFIG_PATH} ({'있음' if CONFIG_PATH.exists() else '없음'})")
    print(f"결정된 모드: {resolve_mode()}")
    print(f"CONFIG keys: {list(CONFIG.keys())}")