# KIS 텔레그램 봇 서비스 가이드

KIS 자동매매 텔레그램 봇을 systemd 서비스로 운영하는 방법을 정리한 문서다.

## 개요
- 봇은 systemd 서비스(`kis-telegram-bot`)로 24시간 실행된다.
- **기본 모드는 모의투자(vps)** 이며, 실제 주문이 나가지 않는다.
- 부팅 시 자동 시작되고, 비정상 종료 시 자동 재시작된다.
- 실전투자(prod)로 전환하려면 아래 가이드를 따라 명시적으로 변경한다.

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
journalctl -u kis-telegram-bot -f           # 실시간 로그
journalctl -u kis-telegram-bot -n 50        # 최근 50줄 로그
```

## 코드 수정 후 배포
봇 코드(`mytrading/telegram_bot.py` 등)를 수정하면 반드시 재시작해야 반영된다.
```bash
sudo systemctl restart kis-telegram-bot
```
- systemd 봇이 실행 중일 때 `uv run ...` 으로 봇을 또 켜면 충돌하니 주의한다.
- 테스트로 수동 실행하려면 먼저 `sudo systemctl stop kis-telegram-bot` 후 진행한다.

## 자주 겪는 문제

### 서비스 상태가 failed 로 뜬다
- `SuccessExitStatus=143 SIGTERM` 설정으로 정상 종료(stop)는 failed 대신 `inactive (dead)` 로 표시된다.
- 그래도 failed 가 뜬다면 진짜 크래시일 수 있으니 로그를 확인한다: `journalctl -u kis-telegram-bot -n 30`

### allocations.yaml 에 테스트 종목이 섞인다
- 모의 테스트 중 `/추가`·`/승인` 하면 `allocations.yaml` 에 종목이 등록된다.
- 커밋 전 원상복구: `git checkout mytrading/allocations.yaml`
- 모의로 추가된 종목은 `industry: 모의추가` 마커가 있어 `grep 모의추가` 로 찾을 수 있다.

### 봇이 명령에 반응하지 않는다
- 서비스가 떠 있는지 확인: `systemctl is-active kis-telegram-bot`
- `kis_devlp.yaml` 에 `bot_token` 이 설정됐는지 확인한다.

## 재설치 / 재등록
미니PC 재설치나 서비스 재등록 시:
```bash
cd ~/workspace/open-trading-api
bash setup_bot_service.sh
```
이 스크립트가 서비스 파일을 위 설정(모의 기본, failed 방지)으로 생성하고, 실행 끝에 전환 가이드를 출력한다.
