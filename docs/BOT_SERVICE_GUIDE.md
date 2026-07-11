# KIS 텔레그램 봇 서비스 가이드 (설치·운영)

KIS 자동매매 텔레그램 봇을 systemd 서비스로 설치·운영하는 방법을 정리한 문서다.
봇 사용법(명령어·기능)은 `mytrading/docs/TELEGRAM_BOT_DESIGN.md` 참고.

## 개요

| 항목 | 내용 |
|------|------|
| 서비스명 | `kis-telegram-bot` |
| 실행 | `uv run python -m mytrading.telegram_bot` (상시 폴링 루프) |
| 등록 스크립트 | `setup_bot_service.sh` |
| 서비스 파일 | `/etc/systemd/system/kis-telegram-bot.service` |
| reboot 권한 | `/etc/sudoers.d/reboot-bot` |
| 로그 | `journalctl -u kis-telegram-bot` |

- 봇은 systemd 서비스로 24시간 실행된다.
- **기본 모드는 모의투자(vps)** 이며, 실제 주문이 나가지 않는다.
- 부팅 시 자동 시작되고, 비정상 종료 시 자동 재시작된다.
- 실전투자(prod)로 전환하려면 아래 가이드를 따라 명시적으로 변경한다.

## 전제 조건

1. `setup.sh` 로 개발 환경(uv, 의존성)이 준비돼 있어야 한다.
2. `~/KIS/config/kis_devlp.yaml` (저장소 밖)에 다음이 설정돼 있어야 한다:
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
3. systemd 서비스 파일 생성 (사용자·경로는 실행 환경에 맞게 동적 설정, 모의 기본·failed 방지 설정 포함)
4. `daemon-reload` + `enable`(부팅 자동시작) + `start`
5. 실행 상태 확인 및 전환 가이드 출력

미니PC 재설치나 서비스 재등록 시에도 같은 스크립트를 다시 실행하면 된다.

## 현재 모드 확인

- **텔레그램**: `/상태` (또는 `/status`) → "모의 투자" / "실전 투자" 표시
- **터미널**:

```bash
grep KIS_MODE /etc/systemd/system/kis-telegram-bot.service
systemctl is-active kis-telegram-bot
```

## 실전 <-> 모의 전환

모드는 서비스 파일의 `KIS_MODE` 값으로 결정된다. 전환하려면 값을 바꾸고 서비스를 재시작한다.

### 실전투자(prod)로 전환

```bash
sudo nano /etc/systemd/system/kis-telegram-bot.service
# Environment=KIS_MODE=vps  ->  KIS_MODE=prod 로 수정 후 저장

sudo systemctl daemon-reload
sudo systemctl restart kis-telegram-bot
```

전환 후 텔레그램 `/상태` 로 "실전 투자" 를 반드시 확인한다.

### 모의투자(vps)로 복귀

```bash
sudo nano /etc/systemd/system/kis-telegram-bot.service
# Environment=KIS_MODE=prod  ->  KIS_MODE=vps 로 수정 후 저장

sudo systemctl daemon-reload
sudo systemctl restart kis-telegram-bot
```

### 주의사항

- **실전 전환 시 실제 돈으로 주문이 나간다.** `/승인`·`/추가` 로 매수 계획을 저장하면 다음 매매 시점에 실제 체결될 수 있다.
- **모의 봇과 실전 봇을 동시에 켜지 말 것.** 같은 텔레그램 토큰으로 둘이 폴링하면 메시지를 서로 뺏어가 오작동한다. 항상 하나만 실행한다.
- 모드는 봇 시작 시 한 번 결정된다. 실행 중에는 바뀌지 않으므로 전환 시 반드시 재시작한다.

## 서비스 관리

```bash
sudo systemctl status kis-telegram-bot      # 상태 확인
sudo systemctl restart kis-telegram-bot     # 재시작 (코드 수정 후 필수)
sudo systemctl stop kis-telegram-bot        # 중지
sudo systemctl start kis-telegram-bot       # 시작
sudo systemctl disable kis-telegram-bot     # 자동시작 끄기
journalctl -u kis-telegram-bot -f           # 실시간 로그
journalctl -u kis-telegram-bot -n 50        # 최근 50줄 로그
```

## 코드 수정 후 배포

봇 코드(`mytrading/telegram_bot.py` 등)를 수정하면 반드시 재시작해야 반영된다.

```bash
sudo systemctl restart kis-telegram-bot
```

- **코드를 수정하면 반드시 `restart`.** systemd가 관리하므로, 파일만 고치고 재시작하지 않으면 봇은 예전 코드로 계속 돈다.
- **수동 실행 금지.** systemd 봇이 실행 중일 때 `uv run python -m mytrading.telegram_bot` 로 봇을 또 켜면 텔레그램 업데이트를 서로 뺏어가 충돌한다. 테스트로 수동 실행하려면 먼저 `sudo systemctl stop kis-telegram-bot` 후 진행한다.
- **설정 변경 후 재시작.** `kis_devlp.yaml` 의 값(비번, 토큰 등)을 바꾸면 봇을 `restart` 해야 새 값을 읽는다 (설정은 시작 시 1회 로드 후 캐시).

## 동작 특성

- `Restart=always` + `RestartSec=10`: 봇이 죽으면 10초 후 자동 재시작. KIS 서버 점검(주말·심야)으로 일시 오류가 나도 스스로 복구한다.
- `After=network-online.target`: 부팅 시 네트워크가 올라온 뒤 봇을 시작한다.
- `enable` 상태: 재부팅하면 봇이 자동으로 다시 켜진다. (`/reboot` 명령으로 재부팅해도 봇이 자동 복귀)
- `SuccessExitStatus=143 SIGTERM`: 정상 종료(stop)는 failed 대신 `inactive (dead)` 로 표시된다.

## 자주 겪는 문제

### 서비스 상태가 failed 로 뜬다

- `SuccessExitStatus=143 SIGTERM` 설정으로 정상 종료(stop)는 failed 대신 `inactive (dead)` 로 표시된다.
- 그래도 failed 가 뜬다면 진짜 크래시일 수 있으니 로그를 확인한다: `journalctl -u kis-telegram-bot -n 30`

### 봇이 명령에 반응하지 않는다

- 서비스가 떠 있는지 확인: `systemctl is-active kis-telegram-bot`
- `kis_devlp.yaml` 에 `bot_token` 이 설정됐는지 확인한다.
- 다른 봇 인스턴스가 수동 실행 중이 아닌지 확인한다 (토큰 충돌).

### 서비스가 계속 재시작 반복 (activating/failed)

- 로그에서 시작 시 예외 확인. KIS 서버 점검 중이면 인증 실패로 반복될 수 있다.
- (봇은 폴링만 먼저 시작하고 인증은 명령 시점에 하므로 대개 문제없음)

### 재부팅 후 봇이 안 켜진다

- `systemctl is-enabled kis-telegram-bot` 이 `enabled` 인지 확인. `disabled` 면 `sudo systemctl enable kis-telegram-bot`.

### 로그에 print 메시지가 안 보인다

- 서비스에 `Environment=PYTHONUNBUFFERED=1` 이 있는지 확인 (setup_bot_service.sh 는 이미 포함).

### allocations.yaml 에 테스트 종목이 섞인다

- 모의 테스트 중 `/추가`·`/승인` 하면 `allocations.yaml` 에 종목이 등록된다.
- 커밋 전 원상복구: `git checkout mytrading/allocations.yaml`
- 모의로 추가된 종목은 `industry: 모의추가` 마커가 있어 `grep 모의추가` 로 찾을 수 있다.

## 관련 문서

- 하드웨어 아이들 프리즈 트러블슈팅: `docs/TROUBLESHOOTING_IDLE_FREEZE.md`
- 봇 사용법·명령어·설계: `mytrading/docs/TELEGRAM_BOT_DESIGN.md`