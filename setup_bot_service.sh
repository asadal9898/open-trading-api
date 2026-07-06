#!/usr/bin/env bash
#
# KIS 텔레그램 봇 서비스 등록 스크립트
# - reboot sudoers 등록 (/reboot 명령이 비번 없이 재부팅하도록)
# - systemd 서비스 등록 (상시 실행 + 부팅 자동시작 + 자동 재시작)
#
# 사용법:
#   cd ~/workspace/open-trading-api
#   bash setup_bot_service.sh
#
# 전제:
#   - setup.sh 로 개발 환경(uv, 의존성)이 먼저 준비돼 있어야 합니다.
#   - ~/KIS/config/kis_devlp.yaml 에 bot_token, reboot_password 가 설정돼 있어야 합니다.
#   - 여러 번 실행해도 안전합니다(기존 등록을 갱신).

set -e

info()  { echo -e "\033[1;34m[INFO]\033[0m  $1"; }
ok()    { echo -e "\033[1;32m[ OK ]\033[0m  $1"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $1"; }
err()   { echo -e "\033[1;31m[FAIL]\033[0m  $1"; }

SERVICE_NAME="kis-telegram-bot"
PROJECT_DIR="$HOME/workspace/open-trading-api"
UV_BIN="$HOME/.local/bin/uv"
KIS_CONFIG="$HOME/KIS/config/kis_devlp.yaml"

# ----- 0. 사전 확인 -----
if [ ! -d "$PROJECT_DIR" ]; then
    err "프로젝트 폴더가 없습니다: $PROJECT_DIR"
    exit 1
fi
if [ ! -x "$UV_BIN" ]; then
    err "uv 를 찾을 수 없습니다: $UV_BIN"
    err "  먼저 setup.sh 를 실행해 개발 환경을 준비하세요."
    exit 1
fi
info "프로젝트: $PROJECT_DIR"
info "실행 사용자: $USER"

# kis_devlp.yaml 의 필수 키 확인 (없어도 진행은 하되 경고)
if [ -f "$KIS_CONFIG" ]; then
    if grep -q "^bot_token:" "$KIS_CONFIG"; then
        ok "bot_token 설정 확인됨"
    else
        warn "kis_devlp.yaml 에 bot_token 이 없습니다. 봇이 명령을 못 받습니다."
    fi
    if grep -q "^reboot_password:" "$KIS_CONFIG"; then
        ok "reboot_password 설정 확인됨"
    else
        warn "kis_devlp.yaml 에 reboot_password 가 없습니다. /reboot 매매시간 비번이 동작 안 합니다."
        warn "  추가: echo 'reboot_password: \"원하는비번\"' >> $KIS_CONFIG"
    fi
else
    warn "KIS 설정 파일이 없습니다: $KIS_CONFIG"
    warn "  먼저 setup.sh 로 kis_devlp.yaml 을 준비하고 키를 입력하세요."
fi

# ----- 1. reboot sudoers 등록 -----
info "reboot 권한(sudoers) 등록..."
SUDOERS_FILE="/etc/sudoers.d/reboot-bot"
# reboot 실제 경로 탐지 (배포판마다 다를 수 있음)
REBOOT_PATHS=""
for p in /sbin/reboot /usr/sbin/reboot /bin/systemctl /usr/bin/systemctl; do
    [ -e "$p" ] && REBOOT_PATHS="$REBOOT_PATHS $p,"
done
REBOOT_PATHS="${REBOOT_PATHS%,}"   # 마지막 콤마 제거
REBOOT_PATHS="$(echo "$REBOOT_PATHS" | sed 's/^ *//')"
if [ -z "$REBOOT_PATHS" ]; then
    warn "reboot 실행 파일을 못 찾았습니다. sudoers 등록을 건너뜁니다."
else
    echo "$USER ALL=(root) NOPASSWD: $REBOOT_PATHS" | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    # 문법 검증 (sudoers 는 문법 틀리면 sudo 자체가 망가지므로 필수)
    if sudo visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
        ok "reboot sudoers 등록됨: $REBOOT_PATHS"
    else
        err "sudoers 문법 오류 → 파일 제거 (안전)"
        sudo rm -f "$SUDOERS_FILE"
        exit 1
    fi
fi

# ----- 2. systemd 서비스 파일 생성 -----
info "systemd 서비스 파일 생성..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=KIS Trading Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_DIR
Environment=KIS_MODE=vps      # 기본: 모의투자. 실전은 prod로 변경 (아래 가이드 참고)
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=$UV_BIN run python -m mytrading.telegram_bot
SuccessExitStatus=143 SIGTERM
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
ok "서비스 파일 생성됨: $SERVICE_FILE"

# ----- 3. 등록 + 시작 -----
info "systemd 리로드..."
sudo systemctl daemon-reload

info "부팅 자동시작 등록(enable)..."
sudo systemctl enable "$SERVICE_NAME"

info "봇 시작(restart: 이미 떠 있으면 새 코드로 재시작)..."
sudo systemctl restart "$SERVICE_NAME"

sleep 3

# ----- 4. 상태 확인 -----
echo
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "봇 서비스 실행 중 (active)"
else
    err "봇 서비스가 실행되지 않았습니다. 로그를 확인하세요:"
    err "  journalctl -u $SERVICE_NAME -n 30 --no-pager"
    exit 1
fi

echo
ok "====================================="
ok " 봇 서비스 등록 완료!"
ok "====================================="
echo
info "관리 명령:"
echo "  상태:     sudo systemctl status $SERVICE_NAME"
echo "  재시작:   sudo systemctl restart $SERVICE_NAME   (코드 수정 후 필수)"
echo "  중지:     sudo systemctl stop $SERVICE_NAME"
echo "  로그:     journalctl -u $SERVICE_NAME -f"
echo
warn "코드를 수정하면 반드시 'restart' 해야 반영됩니다."
warn "수동으로 'uv run ...' 로 봇을 또 켜면 systemd 봇과 충돌하니 주의하세요."

echo
info "====================================="
info " 실전 <-> 모의 전환 가이드"
info "====================================="
echo "  이 봇은 기본 '모의투자(vps)' 로 실행됩니다 (실제 주문 없음)."
echo
echo "  [실전투자(prod)로 전환]"
echo "    1) sudo nano $SERVICE_FILE"
echo "    2) Environment=KIS_MODE=vps  ->  KIS_MODE=prod 로 수정"
echo "    3) sudo systemctl daemon-reload"
echo "    4) sudo systemctl restart $SERVICE_NAME"
echo "    5) 텔레그램에서 /상태 -> '실전 투자' 확인"
echo
echo "  [모의투자(vps)로 복귀]"
echo "    위에서 KIS_MODE=prod -> vps 로 바꾸고 3~5 반복"
echo
warn "  실전 전환 시 실제 돈으로 주문이 나갑니다. /상태 로 모드를 꼭 확인하세요."
warn "  모의/실전 봇을 동시에 켜지 마세요 (같은 토큰 충돌)."