# 텔레그램 봇 배포/운영 가이드

KIS 자동매매 텔레그램 봇을 systemd 서비스로 상시 운영하기 위한 문서입니다.

## 개요

| 항목 | 내용 |
|------|------|
| 서비스명 | `kis-telegram-bot` |
| 실행 | `uv run python -m mytrading.telegram_bot` (상시 폴링 루프) |
| 등록 스크립트 | `setup_bot_service.sh` |
| 서비스 파일 | `/etc/systemd/system/kis-telegram-bot.service` |
| reboot 권한 | `/etc/sudoers.d/reboot-bot` |
| 로그 | `journalctl -u kis-telegram-bot` |

## 전제 조건

1. `setup.sh` 로 개발 환경(uv, 의존성)이 준비돼 있어야 합니다.
2. `~/KIS/config/kis_devlp.yaml` (저장소 밖)에 다음이 설정돼 있어야 합니다:
   - `bot_token` — 텔레그램 봇 토큰 (BotFather 발급)
   - `reboot_password` — `/reboot` 매매시간 재부팅용 비밀번호
   - `users` — 화이트리스트 (telegram_chat_id, role 등)

## 설치

```bash
cd ~/workspace/open-trading-api
bash setup_bot_service.sh
```

이 스크립트가 하는 일:
1. `kis_devlp.yaml` 의 필수 키(bot_token, reboot_password) 확인
2. reboot sudoers 등록 — `/reboot` 명령이 비번 없이 재부팅하도록 (문법 검증 포함)
3. systemd 서비스 파일 생성 (사용자·경로는 실행 환경에 맞게 동적 설정)
4. `daemon-reload` + `enable`(부팅 자동시작) + `start`
5. 실행 상태 확인

## 관리 명령

| 목적 | 명령 |
|------|------|
| 상태 확인 | `sudo systemctl status kis-telegram-bot` |
| 재시작 (코드 수정 후 필수) | `sudo systemctl restart kis-telegram-bot` |
| 중지 | `sudo systemctl stop kis-telegram-bot` |
| 시작 | `sudo systemctl start kis-telegram-bot` |
| 자동시작 끄기 | `sudo systemctl disable kis-telegram-bot` |
| 실시간 로그 | `journalctl -u kis-telegram-bot -f` |
| 최근 로그 | `journalctl -u kis-telegram-bot -n 50 --no-pager` |

## 중요 주의사항

- **코드를 수정하면 반드시 `restart` 해야 반영됩니다.** systemd 가 관리하므로,
  파일만 고치고 재시작하지 않으면 봇은 예전 코드로 계속 돕니다.
- **수동 실행 금지.** `uv run python -m mytrading.telegram_bot` 로 봇을 또 켜면
  systemd 봇과 텔레그램 업데이트를 서로 뺏어가 충돌합니다. 반드시 systemd 로만 운영하세요.
- **설정 변경 후 재시작.** `kis_devlp.yaml` 의 값(비번, 토큰 등)을 바꾸면 봇을
  `restart` 해야 새 값을 읽습니다 (설정은 시작 시 1회 로드 후 캐시).

## 동작 특성

- `Restart=always` + `RestartSec=10`: 봇이 죽으면 10초 후 자동 재시작.
  KIS 서버 점검(주말·심야)으로 일시 오류가 나도 스스로 복구합니다.
- `After=network-online.target`: 부팅 시 네트워크가 올라온 뒤 봇을 시작합니다.
- `enable` 상태: 재부팅하면 봇이 자동으로 다시 켜집니다.
  (`/reboot` 명령으로 재부팅해도 봇이 자동 복귀)

## /reboot 명령

`/reboot`(`/재부팅`)은 봇에서 미니PC를 재부팅하는 명령입니다.

- **owner 역할만** 실행 가능
- **매매시간(개장일 09:00~15:30)**: `reboot_password` 입력을 요구
  (장중 실수 재부팅 방지)
- **장외 시간**: `[재부팅][취소]` 확인 버튼
- 실제 실행: `sudo /usr/sbin/reboot` (sudoers NOPASSWD 로 비번 없이)
