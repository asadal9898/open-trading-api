"""
인증 및 모드 확인용 러너 (가장 단순한 첫 실행 파일)
common.py 의 안전장치가 제대로 동작하는지 검증합니다.

실행:
    uv run python mytrading/runners/check_auth.py            # 모의(vps)
    KIS_MODE=prod uv run python mytrading/runners/check_auth.py   # 실전(YES 확인)
"""
import sys
from pathlib import Path

# 프로젝트 루트를 path 에 추가 (mytrading 패키지 import 위해)
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mytrading.common import init, CONFIG


def main():
    # 인증 + 모드 확인 + 실전 가드
    ka = init()

    # 인증 환경 정보 출력
    env = ka.getTREnv()
    print(f"계좌번호: {env.my_acct}")
    print(f"서버 URL: {env.my_url}")
    print(f"설정된 종목: {CONFIG.get('trading', {}).get('symbols', '없음')}")
    print("인증 정상 — 준비 완료")


if __name__ == "__main__":
    main()
