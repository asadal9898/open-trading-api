#!/usr/bin/env bash
#
# open-trading-api 개발 환경 셋업 스크립트
# - uv, git 설치 확인 및 자동 설치
# - 가상환경 생성 + kis_backtest editable 설치
# - kis_devlp.yaml 을 ~/KIS/config/ 로 복사
# - Claude Code 설치 확인 + kis-quant-plugin 설치
#
# 사용법:
#   cd ~/workspace/open-trading-api
#   bash setup.sh
#
# 주의: 이 스크립트는 ~/workspace/open-trading-api 안에서 실행해야 합니다.

set -e  # 오류 발생 시 즉시 중단

# ----- 색상/로그 헬퍼 -----
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

# ----- 1. apt 업데이트 + curl / git 설치 확인 -----
info "apt 패키지 목록 업데이트..."
sudo apt update

# curl: uv / Claude Code 설치에 필요하므로 가장 먼저 확인
if command -v curl >/dev/null 2>&1; then
    ok "curl 이미 설치됨: $(curl --version | head -n1)"
else
    warn "curl 미설치 → 설치 진행 (uv/Claude Code 설치에 필요)"
    sudo apt install -y curl
    ok "curl 설치 완료: $(curl --version | head -n1)"
fi

# git
if command -v git >/dev/null 2>&1; then
    ok "git 이미 설치됨: $(git --version)"
else
    warn "git 미설치 → 설치 진행"
    sudo apt install -y git
    ok "git 설치 완료: $(git --version)"
fi

# ----- 2. uv 설치 확인 -----
if command -v uv >/dev/null 2>&1; then
    ok "uv 이미 설치됨: $(uv --version)"
else
    warn "uv 미설치 → 설치 진행"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 설치 직후 현재 셸 PATH 에 반영 (보통 ~/.local/bin)
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        ok "uv 설치 완료: $(uv --version)"
    else
        err "uv 설치 후에도 명령을 찾을 수 없습니다. 터미널을 새로 열고 다시 실행하세요."
        exit 1
    fi
fi

# ----- 3. 가상환경 + kis_backtest editable 설치 -----
info "가상환경 생성 (이미 있으면 재사용)..."
uv venv

info "backtester 를 editable(-e) 로 설치..."
uv pip install -e ./backtester

info "kis_backtest import 테스트..."
if uv run python -c "from kis_backtest import LeanClient, RuleBuilder, SMA, RSI; print('kis_backtest import 성공')"; then
    ok "kis_backtest import 정상"
else
    err "kis_backtest import 실패 — 위 오류 메시지를 확인하세요."
    exit 1
fi

# ----- 4. kis_devlp.yaml 을 ~/KIS/config/ 로 복사 -----
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

# ----- 5. Claude Code 설치 확인 + 플러그인 -----
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

# kis-quant-plugin 설치 (.claude 디렉터리 유무로 판단)
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
echo "  2) 백테스트 MCP 서버 실행 (별도 터미널):"
echo "       bash backtester/scripts/start_mcp.sh"
echo "  3) Claude Code 실행 후 /mcp 로 연결 확인:"
echo "       claude"
