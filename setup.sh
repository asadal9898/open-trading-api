#!/usr/bin/env bash
#
# open-trading-api 개발 환경 셋업 스크립트
# - curl, git, uv 설치 확인 및 자동 설치
# - Docker 설치 확인 (백테스트 Lean 엔진용) + 권한 안내
# - 가상환경 생성 + kis_backtest editable 설치
# - kis_devlp.yaml 을 ~/KIS/config/ 로 복사
# - Lean 백테스트 데이터 초기화 + symbol csv 중복 키 제거
# - Claude Code 설치 확인 + kis-quant-plugin 설치
#
# 사용법:
#   cd ~/workspace/open-trading-api
#   bash setup.sh
#
# 주의:
#   - 이 스크립트는 ~/workspace/open-trading-api 안에서 실행해야 합니다.
#   - Docker 를 처음 설치하면 권한 적용을 위해 "로그아웃 후 재로그인"이 필요합니다.
#     그 경우 재로그인 후 setup.sh 를 한 번 더 실행하면 나머지가 이어서 진행됩니다.
#   - 여러 번 실행해도 안전합니다(이미 된 단계는 건너뜀).

set -e

info()  { echo -e "\033[1;34m[INFO]\033[0m  $1"; }
ok()    { echo -e "\033[1;32m[ OK ]\033[0m  $1"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $1"; }
err()   { echo -e "\033[1;31m[FAIL]\033[0m  $1"; }

# ----- 0. 실행 위치 확인 -----
PROJECT_DIR="$HOME/workspace/open-trading-api"
if [ ! -f "./backtester/pyproject.toml" ]; then
    err "이 스크립트는 open-trading-api 루트에서 실행해야 합니다."
    err "  cd ~/workspace/open-trading-api && bash setup.sh"
    exit 1
fi
info "프로젝트 루트 확인됨: $(pwd)"

# ----- 1. apt 업데이트 + curl / git -----
info "apt 패키지 목록 업데이트..."
sudo apt update

if command -v curl >/dev/null 2>&1; then
    ok "curl 이미 설치됨"
else
    warn "curl 미설치 → 설치 진행"
    sudo apt install -y curl
    ok "curl 설치 완료"
fi

if command -v git >/dev/null 2>&1; then
    ok "git 이미 설치됨: $(git --version)"
else
    warn "git 미설치 → 설치 진행"
    sudo apt install -y git
    ok "git 설치 완료: $(git --version)"
fi

# ----- 2. uv -----
if command -v uv >/dev/null 2>&1; then
    ok "uv 이미 설치됨: $(uv --version)"
else
    warn "uv 미설치 → 설치 진행"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        ok "uv 설치 완료: $(uv --version)"
    else
        err "uv 설치 후에도 명령을 찾을 수 없습니다. 터미널을 새로 열고 다시 실행하세요."
        exit 1
    fi
fi

# ----- 3. Docker (백테스트 Lean 엔진용) -----
DOCKER_READY=0
if command -v docker >/dev/null 2>&1; then
    ok "docker 명령 존재"
else
    warn "Docker 미설치 → 설치 진행 (백테스트에 필요)"
    sudo apt install -y docker.io
    ok "docker.io 설치 완료"
fi

if docker info >/dev/null 2>&1; then
    ok "Docker 정상 동작 (권한 OK)"
    DOCKER_READY=1
else
    warn "Docker 데몬에 접근할 수 없습니다 (권한 또는 미실행)."
    if ! id -nG "$USER" | grep -qw docker; then
        warn "사용자를 docker 그룹에 추가합니다: $USER"
        sudo usermod -aG docker "$USER"
        echo
        err "════════════════════════════════════════════════════════"
        err " Docker 그룹 권한이 방금 추가되었습니다."
        err " 적용하려면 [로그아웃 후 재로그인] (또는 재부팅) 하세요."
        err " 재로그인 후 'bash setup.sh' 를 다시 실행하면 이어서 진행됩니다."
        err "════════════════════════════════════════════════════════"
        echo
        warn "Lean 데이터 초기화 단계는 건너뛰고, 나머지는 계속 진행합니다."
    else
        warn "docker 그룹에는 속해 있으나 데몬 접근 실패 → Docker 서비스 시작 시도"
        sudo systemctl enable --now docker 2>/dev/null || true
        if docker info >/dev/null 2>&1; then
            ok "Docker 서비스 시작됨 (권한 OK)"
            DOCKER_READY=1
        else
            warn "Docker 가 아직 준비되지 않았습니다. 재로그인 후 setup.sh 재실행 권장."
        fi
    fi
fi

# ----- 3.5 Tesseract OCR (한국투자증권 점검 이미지 OCR용, 선택) -----
info "Tesseract OCR 설치 확인..."
if command -v tesseract >/dev/null 2>&1; then
    ok "Tesseract 이미 설치됨 ($(tesseract --version 2>&1 | head -1))"
else
    warn "Tesseract 미설치 → 설치 진행 (점검 메일 이미지 OCR 에 필요)"
    sudo apt install -y tesseract-ocr tesseract-ocr-kor
fi
# 한글팩 확인
if tesseract --list-langs 2>&1 | grep -q "kor"; then
    ok "Tesseract 한글팩(kor) 확인됨"
else
    warn "Tesseract 한글팩 없음 → 설치 시도"
    sudo apt install -y tesseract-ocr-kor
fi

# ----- 4. 가상환경 + kis_backtest editable -----
info "가상환경 생성 (이미 있으면 재사용)..."
uv venv

info "backtester 를 editable(-e) 로 설치..."
uv pip install -e ./backtester

info "루트 프로젝트 의존성 설치 (pandas-market-calendars, ruamel.yaml 등)..."
uv pip install pandas-market-calendars ruamel.yaml pdfplumber

info "kis_backtest import 테스트..."
if uv run python -c "from kis_backtest import LeanClient, RuleBuilder, SMA, RSI; print('kis_backtest import 성공')"; then
    ok "kis_backtest import 정상"
else
    err "kis_backtest import 실패 — 위 오류 메시지를 확인하세요."
    exit 1
fi

# ----- 5. kis_devlp.yaml 복사 -----
info "KIS 설정 폴더 준비..."
mkdir -p "$HOME/KIS/config"
if [ -f "$HOME/KIS/config/kis_devlp.yaml" ]; then
    warn "~/KIS/config/kis_devlp.yaml 이미 존재 → 덮어쓰지 않음 (기존 키 보호)"
else
    if [ -f "./kis_devlp.yaml" ]; then
        cp ./kis_devlp.yaml "$HOME/KIS/config/"
        ok "kis_devlp.yaml 복사 완료 → ~/KIS/config/"
        warn "복사된 파일을 열어 App Key / Secret / 계좌번호를 입력하세요:"
        warn "  nano ~/KIS/config/kis_devlp.yaml"
    else
        warn "루트에 kis_devlp.yaml 이 없습니다. 수동 확인 필요."
    fi
fi

# ----- 5.5 DART API 키 확인 (선택 — 재무 교차검증·업종 조회용) -----
# OpenDartReader 패키지는 pyproject.toml 로 자동 설치됨. 여기선 인증키만 확인.
DART_CFG="$HOME/KIS/config/kis_devlp.yaml"
if [ -f "$DART_CFG" ] && grep -q "my_DART_APIkey" "$DART_CFG" 2>/dev/null; then
    _dart_key=$(grep "my_DART_APIkey" "$DART_CFG" | head -1 | sed "s/.*://; s/[[:space:]]//g")
    if [ -n "$_dart_key" ] && [ "$_dart_key" != "\"\"" ]; then
        ok "DART 인증키 확인됨 → 재무 교차검증·업종 조회 사용 가능"
    else
        warn "my_DART_APIkey 항목은 있으나 값이 비어있음."
        warn "  DART(전자공시)를 쓰려면 https://opendart.fss.or.kr 에서 무료 발급 후 입력하세요."
    fi
else
    warn "DART 인증키(my_DART_APIkey) 미설정 — 선택 기능이라 없어도 봇은 동작합니다."
    warn "  재무 교차검증·업종 자동조회를 쓰려면:"
    warn "    1) https://opendart.fss.or.kr 에서 무료 발급 (개인회원 즉시)"
    warn "    2) ~/KIS/config/kis_devlp.yaml 에 'my_DART_APIkey: 발급키' 추가"
fi

# ----- 5.6 Gmail 연동 확인 (선택 — 뉴스레터·점검공지 감시용) -----
# IMAP 읽기 + SMTP 발송. 앱 비밀번호 방식.
GMAIL_CFG="$HOME/KIS/config/kis_devlp.yaml"
if [ -f "$GMAIL_CFG" ] && grep -q "my_GMAIL_app_password" "$GMAIL_CFG" 2>/dev/null; then
    _gmail_pw=$(grep "my_GMAIL_app_password" "$GMAIL_CFG" | head -1 | sed "s/.*://; s/[[:space:]]//g")
    if [ -n "$_gmail_pw" ] && [ "$_gmail_pw" != '""' ]; then
        ok "Gmail 인증 확인됨 → 뉴스레터·점검공지 감시 사용 가능"
    else
        warn "my_GMAIL_app_password 항목은 있으나 값이 비어있음."
        warn "  Gmail 앱 비밀번호(16자리)를 발급받아 입력하세요."
    fi
else
    warn "Gmail 인증(my_GMAIL_address/app_password) 미설정 — 선택 기능."
    warn "  뉴스레터 만료·KCIF·한투 점검공지 감시를 쓰려면:"
    warn "    1) Gmail 2단계인증 켜고 https://myaccount.google.com/apppasswords 에서"
    warn "       앱 비밀번호(16자리) 발급"
    warn "    2) ~/KIS/config/kis_devlp.yaml 에 추가:"
    warn "         my_GMAIL_address: \"you@gmail.com\""
    warn "         my_GMAIL_app_password: \"앱비밀번호16자리\""
fi

# ----- 6. Lean 데이터 초기화 + csv 중복 키 제거 -----
LEAN_CSV="./backtester/.lean-workspace/data/symbol-properties/symbol-properties-database.csv"
if [ "$DOCKER_READY" = "1" ]; then
    if [ -f "$LEAN_CSV" ]; then
        ok "Lean 데이터 이미 초기화됨 (symbol-properties csv 존재)"
    else
        info "Lean 백테스트 데이터 초기화 (setup_lean_data.sh)..."
        bash backtester/scripts/setup_lean_data.sh
        ok "Lean 데이터 초기화 완료"
    fi
    if [ -f "$LEAN_CSV" ]; then
        DUP=$(awk -F',' '/^krx,/ {print $2}' "$LEAN_CSV" | sort | uniq -d | head -1)
        if [ -n "$DUP" ]; then
            warn "symbol csv 중복 키 발견 → 제거 (첫 등장만 유지)"
            cp "$LEAN_CSV" "$LEAN_CSV.bak"
            awk -F',' '
              /^krx,/ { if (seen[$2]++) next }
              { print }
            ' "$LEAN_CSV.bak" > "$LEAN_CSV"
            ok "중복 키 제거 완료 (백업: $LEAN_CSV.bak)"
        else
            ok "symbol csv 중복 키 없음"
        fi
    fi
else
    warn "Docker 미준비 → Lean 데이터 초기화 건너뜀."
    warn "재로그인 후 'bash setup.sh' 재실행 시 이 단계가 진행됩니다."
fi

# ----- 7. Claude Code + 플러그인 -----
if command -v claude >/dev/null 2>&1; then
    ok "Claude Code 이미 설치됨: $(claude --version 2>/dev/null || echo '버전 확인 불가')"
else
    warn "Claude Code 미설치 → 네이티브 설치 진행"
    curl -fsSL https://claude.ai/install.sh | bash
    export PATH="$HOME/.local/bin:$PATH"
    if command -v claude >/dev/null 2>&1; then
        ok "Claude Code 설치 완료"
    else
        warn "claude 명령을 찾을 수 없습니다. 새 터미널에서 확인하세요."
    fi
fi

if [ -d "./.claude/skills" ]; then
    ok "kis-quant-plugin 이미 설치됨 (.claude/skills 존재)"
else
    warn "kis-quant-plugin 미설치 → 설치 진행"
    if command -v npx >/dev/null 2>&1; then
        npx @koreainvestment/kis-quant-plugin init --agent claude
        ok "kis-quant-plugin 설치 완료"
    else
        warn "npx(Node.js) 가 없어 플러그인 설치를 건너뜁니다."
        warn "Node.js 설치 후 수동 실행: npx @koreainvestment/kis-quant-plugin init --agent claude"
    fi
fi

# ----- 완료 -----
echo
ok "====================================="
ok " 셋업 완료!"
ok "====================================="
echo
info "다음 단계:"
echo "  1) ~/KIS/config/kis_devlp.yaml 에 본인 App Key/Secret/계좌 입력"
if [ "$DOCKER_READY" != "1" ]; then
    echo "  2) [중요] 로그아웃 후 재로그인 → 'bash setup.sh' 재실행 (Docker 권한 적용)"
fi
echo "  3) 백테스트 동작 확인:"
echo "       uv run python mytrading/runners/check_auth.py"
echo "       uv run python mytrading/runners/run_backtest.py"
echo "  4) Claude Code 실행:"
echo "       claude"
echo "  5) (선택) 봇을 서비스로 등록해 상시 실행: bash setup_bot_service.sh"
echo "  6) (선택) DART 재무 교차검증: kis_devlp.yaml 에 my_DART_APIkey 추가"
echo "       https://opendart.fss.or.kr (개인회원 무료·즉시 발급)"
echo "  7) (선택) Gmail 감시(뉴스레터·점검공지): my_GMAIL_address/app_password 추가"
echo "       앱 비밀번호: https://myaccount.google.com/apppasswords"
