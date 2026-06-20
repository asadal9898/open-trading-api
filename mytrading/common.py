"""
mytrading 공통 모듈
- 모드 결정: 환경변수 KIS_MODE 우선 (없으면 vps=모의). 실전(prod)은 환경변수로만 가능
- KIS 인증 + 실전 안전 가드(YES 타이핑 확인)
- mytrading_config.yaml 로드 (모드 외 일반 설정)

사용 예 (러너에서):
    from mytrading.common import init, CONFIG
    ka = init()                       # 인증까지 끝난 kis_auth 모듈 반환
    symbols = CONFIG["trading"]["symbols"]

실행:
    uv run python mytrading/runners/check_auth.py            # 모의(vps)
    KIS_MODE=prod uv run python mytrading/runners/check_auth.py   # 실전(확인 필요)
"""
import os
import sys
from pathlib import Path

import yaml
import kis_auth as ka  # backtester/kis_auth.py (editable 설치로 import 가능)

# ----- 설정 파일 경로 -----
_THIS_DIR = Path(__file__).resolve().parent          # .../mytrading
CONFIG_PATH = _THIS_DIR / "mytrading_config.yaml"

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

    # 환경변수 없음 → YAML 확인 (단 prod 차단)
    yaml_mode = (CONFIG.get("kis_mode") or "").strip().lower()
    if yaml_mode == "prod":
        print("[FAIL] 실전(prod)은 설정 파일로 지정할 수 없습니다.")
        print("       실전은 환경변수로만 가능: KIS_MODE=prod uv run ...")
        sys.exit(1)
    if yaml_mode == "vps":
        return "vps"

    # 아무것도 없으면 안전 기본값
    return "vps"


def init(require_confirm: bool = True):
    """
    모드 결정 → 인증 → 실전 가드 → 모드 출력.
    인증이 끝난 kis_auth 모듈(ka)을 반환.
    """
    mode = resolve_mode()
    label = _MODE_LABEL[mode]

    # 인증 (svr 항상 명시 — 기본값 prod에 절대 의존하지 않음)
    ka.auth(svr=mode)

    # 인증 결과로 실제 모드 재확인 (이중 체크)
    actual_paper = ka.isPaperTrading()
    actual_label = "모의투자" if actual_paper else "실전투자"

    print("=" * 50)
    if actual_paper:
        print(f"  현재 모드: ✅ {actual_label} (vps)")
    else:
        print(f"  현재 모드: 🚨 {actual_label} (prod) — 실제 주문이 체결됩니다")
    print("=" * 50)

    # 실전이면 사용자 확인 강제
    if not actual_paper and require_confirm:
        ans = input("🚨 실전투자 모드입니다. 진짜 돈으로 주문이 나갑니다.\n"
                    "   계속하려면 'YES' 를 정확히 입력하세요: ")
        if ans.strip() != "YES":
            print("실전 주문 취소됨. 종료합니다.")
            sys.exit(0)

    return ka


if __name__ == "__main__":
    # 모듈 단독 실행 시: 설정/모드만 점검 (인증 없이)
    print(f"설정 파일: {CONFIG_PATH} ({'있음' if CONFIG_PATH.exists() else '없음'})")
    print(f"결정된 모드: {resolve_mode()}")
    print(f"CONFIG keys: {list(CONFIG.keys())}")
