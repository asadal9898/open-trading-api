# Google Drive 백업 (rclone)

미니PC(우분투 24.04) 코드·문서·리포트를 Google Drive에 자동 백업하는 설정.

> **방향: 미니PC → Drive 단방향.** Drive는 **백업·열람 전용**이며,
> Drive에서 수정한 내용을 미니PC로 되돌리지 않는다 (git과 충돌 방지).
> 수정은 항상 미니PC에서, 버전 관리는 git(GitHub)이 담당한다.

## 개요

| 항목 | 내용 |
|------|------|
| 도구 | rclone (Ubuntu 패키지) |
| remote 이름 | `gdrive` |
| Drive 대상 폴더 | `gdrive:open-trading-api` |
| 필터 파일 | `~/rclone-filter.txt` |
| 자동 실행 | 크론 매일 **07:30** |
| 로그 | `/tmp/rclone_backup.log` |
| 백업 규모 | 약 103개 파일 / 69MB (PDF 원본 포함) |

## 왜 `copy` 인가 (`sync` 아님)

- `rclone copy` — 로컬 파일을 Drive로 복사. **Drive에만 있는 파일은 지우지 않음.**
- `rclone sync` — Drive를 로컬과 똑같이 만듦. **로컬에 없는 Drive 파일을 삭제.** (위험)

백업 용도이므로 `copy` 를 쓴다. 실수로 파일이 사라지는 사고를 막는다.

## 백업 대상 (필터)

`~/rclone-filter.txt` — 위에서부터 순서대로 적용된다.

```
- .git/**
- .venv/**
- **/__pycache__/**
+ /docs/**
+ /mytrading/**
+ /setup.sh
+ /setup_bot_service.sh
+ /pyproject.toml
+ /requirements.txt
+ /uv.lock
- *
```

**포함**: 직접 작성한 코드·문서·설치 스크립트 + 의존성 정의(pyproject/uv.lock).
`mytrading/reports/` 의 KCIF·한국은행 PDF 원본(약 68MB)과 파싱 결과 YAML도 포함.

**제외**:

- `.git/**` — rclone이 건드리면 저장소가 깨질 수 있음 (**반드시 제외**)
- `.venv/**`, `__pycache__/**` — 재생성 가능, 용량만 큼
- `backtester/`, `strategy_builder/`, `MCP/`, `examples_llm/` — 원본 저장소 코드 (수정 안 함)

> ※ 경로 앞의 `/` 는 **최상위 고정**을 뜻한다. `/pyproject.toml` 로 써야
> 하위 폴더(backtester 등)의 같은 이름 파일이 딸려오지 않는다.

## 설치 및 최초 설정

```bash
# 1. rclone 설치
sudo apt update && sudo apt install rclone -y
rclone version

# 2. Google Drive 연결
rclone config
```

`rclone config` 대화형 입력 순서:

1. `n` — New remote
2. **name>** `gdrive`
3. **Storage>** `drive` (Google Drive)
4. **client_id>** (엔터, 빈칸)
5. **client_secret>** (엔터, 빈칸)
6. **scope>** `1` (Full access)
7. **service_account_file>** (엔터)
8. **Edit advanced config?** `n`
9. **Use auto config?** `y` — 브라우저가 열리면 구글 로그인 + 권한 허용
   (GUI 없는 서버라면 `n` → 다른 PC에서 `rclone authorize "drive"` 실행 후 토큰 붙여넣기)
10. **Configure as Shared Drive?** `n`
11. **Keep this remote?** `y`
12. `q` — 종료

연결 확인:

```bash
rclone listremotes          # gdrive: 가 나오면 성공
rclone lsd gdrive:          # Drive 폴더 목록
```

## 수동 백업

```bash
cd ~/workspace/open-trading-api
rclone copy . gdrive:open-trading-api --filter-from ~/rclone-filter.txt --progress
```

먼저 확인만 하려면 `--dry-run` 을 붙인다 (실제 복사 안 함).

## 자동 백업 (크론)

```
30 7 * * * cd $HOME/workspace/open-trading-api && /usr/bin/rclone copy . gdrive:open-trading-api --filter-from $HOME/rclone-filter.txt >> /tmp/rclone_backup.log 2>&1
```

- **07:30** — `daily_update.py`(07:00, 평일 데이터 갱신) 이후. 미국장 마감(06:00) 뒤,
  한국 장전 시간외(08:00)·본장(09:00) 전이라 시스템이 한가한 구간.
- 변경된 파일만 전송하므로 대개 수십 초 내 완료 (rclone이 크기·시각 비교).
- 필터 파일은 **홈(`~/`)에 둔다.** `/tmp` 는 재부팅 시 삭제되어 크론이 못 찾는다.

## 확인 명령

```bash
rclone size gdrive:open-trading-api      # 백업된 파일 수·용량
rclone lsd gdrive:open-trading-api       # 폴더 목록
tail -20 /tmp/rclone_backup.log          # 최근 백업 로그
```

## 주의사항

- **인증 정보는 백업 대상이 아니다.** `~/KIS/config/kis_devlp.yaml` (API 키·봇 토큰·
  reboot 비밀번호·계좌번호)은 저장소 밖에 있으므로 애초에 필터에 안 걸린다.
  **절대 Drive에 올리지 말 것.**
- **Drive에서 파일을 고치지 말 것.** 미니PC로 되돌리지 않으므로 수정은 유실된다.
  코드 수정은 미니PC에서, 이력 관리는 git으로.
- rclone은 **실시간 동기화가 아니다.** 명령을 실행할 때만 복사된다
  (크론이 하루 1회 처리).

## 관련 문서

- 크론 스케줄 전체: `mytrading/docs/CRON_SCHEDULE.md`
- 봇 서비스 설치·운영: `docs/BOT_SERVICE_GUIDE.md`