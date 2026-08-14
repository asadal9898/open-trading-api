# 텔레그램 봇 가이드

작성: 2026-07-01 (설계) · 2026-08 (계좌·현금 UI 작업) · 통합/재검증: 2026-08-14
목적: 텔레그램으로 종목 추가·승인·매매, 계좌·자금·현금 운용, 시스템 관리

봇 설치·서비스 운영은 `docs/BOT_SERVICE_GUIDE.md`(레포 루트) 참고.

> 이 문서는 `TELEGRAM_BOT_DESIGN.md`(명령 설계)와 `CASH_PLAN_AND_ACCOUNTS.md`(계좌·현금 운용)를
> 통합한 것이다. 코드(`mytrading/telegram/telegram_bot.py`, 2026-08-14 기준)와 전면 대조해 재작성했다.

---

## 0. 명령어 레퍼런스

| 명령 | 한글 별칭 | 권한 | 설명 | 일괄(모두/전체/전부) |
|---|---|---|---|---|
| `/add` | `/추가` | 화이트리스트만 | 자유종목 추가 (Waiting) | – |
| `/approve` | `/승인` | 화이트리스트만 | 승인만 (Approval). 매수는 `/buy`·`/splitbuy`로 별도 | ✅ |
| `/reject` | `/거절` | 화이트리스트만 | 거절 (Rejected) | ✅ |
| `/pause` | `/멈춤` | 화이트리스트만 | 매매 중단 (Paused), 기존 보유는 유지 | ✅ |
| `/buy` | `/매수` | 화이트리스트만 | 승인 종목 일시매수 — 즉시 주문 실행 | – |
| `/splitbuy` | `/분할매수` | 화이트리스트만 | 승인 종목 분할매수 — buy_plan 저장(즉시 주문 아님, §2-4 참고) | – |
| `/sell` | `/매도` | 화이트리스트만 | 보유 종목 매도 — 즉시 주문 실행 | – |
| `/list` | `/목록`, `/종목` | 화이트리스트만 | 내 종목 목록 | – |
| `/status` | `/상태` | 화이트리스트만 | 모드·계좌별 총평가금액·보수 투자 시작/중지 | – |
| `/account` | `/계좌` | 화이트리스트만 | 계좌 상세(예산·보유종목) | – |
| `/mode` | `/모드` | 화이트리스트만 | 실전/모의 조회모드 전환 | – |
| `/alloc` | `/비중` | 화이트리스트만 | 자금배분 · `현금` 서브커맨드로 현금운용 | – |
| `/cashbuy` | `/현금매수` | 화이트리스트만 | cash_plan 기반 원화 단기채 ETF 매수 | – |
| `/reboot` | `/재부팅` | **owner만** | 미니PC 재부팅 (매매시간엔 비밀번호 요구) | – |
| `/start`, `/help` | `/시작`, `/도움` | 화이트리스트만 | 명령어 안내 출력 | – |

**권한 확인 방법**: `telegram_bot.py`의 `role` 사용처를 전수 검색(`grep -n role`)한 결과,
`role == "owner"` 체크가 있는 곳은 `_cmd_reboot`(1099행)와 그 버튼 확인 콜백 `reboot_yes`(2549행) 두 곳뿐이다.
나머지 14개 명령은 코드에 권한 분기가 없다 — `owner`든 `trader`든 화이트리스트(`kis_devlp.yaml`의 `users`)에
등록만 되어 있으면 동일하게 실행된다. "trader+" 같은 계층형 표기는 실제와 다르므로 쓰지 않는다.

일괄 처리("모두"/"전체"/"전부"/"all")는 `/approve`·`/reject`·`/pause` 세 명령에서만 동작한다
(`_bulk_set_state`, telegram_bot.py:909, 호출부 1481·1534행). `/buy`·`/splitbuy`·`/sell`엔 없다.

---

## 1. 개요·화이트리스트

자유투자(free_holdings) 종목과 보수(배당) 종목풀(universe_ko.yaml)을 텔레그램으로 관리한다.

- kis_devlp.yaml의 `users`에 `telegram_chat_id`로 chat_id ↔ 사용자를 연결. 필드: `name`,
  `role`(owner/trader — 위 §0 참고, `/reboot` 외엔 구분 없음), `telegram_chat_id`
- chat_id로 발신자 확인 → 화이트리스트에 없는 chat_id는 거부
- 사용자별 격리: 각자 자기 free_holdings만 접근
- 매매는 단계적: 추가(Waiting) ≠ 승인(Approval) ≠ 매수 실행. 한 번에 안 됨.
- 한글 명령도 그대로 동작(`_KO` 매핑)

관련 코드: telegram_bot.py의 `_load_users`

---

## 2. 종목 관리 흐름

### 2-1. `/add {종목명}` — 종목 추가

1. 화이트리스트 확인
2. 이름으로 검색 → 여러 개면 번호 선택 UI
3. 분석:
   - **실전(prod) 모드**: KIS 재무 API로 부채/ROE/영업이익 흑자여부/매출·영업이익 증가율 표시 후 `[예][아니요]`
   - **모의(vps) 모드**: `_analyze()`가 `_is_paper()`일 때 하드코딩으로 재무분석을 건너뛰고 매수 UI로 바로 감(`telegram_bot.py:1225`) — "API 미지원"이 아니라 개발자가 미리 우회하게 짜둔 것. 실측 결과 vps에서도 `get_financial_summary`(부채율·ROE·배당률 등)가 정상 반환됨(예: 동국홀딩스 debt_ratio 79.77, 2026-08-14 확인) — 실전과 다른 흐름이므로 실사용 시 주의
4. 예 → free_holdings에 추가 (confirm: Waiting, 매수 안 함)

관련 코드: telegram_bot.py의 `_cmd_add` (모의 흐름은 `_analyze_paper`)

### 2-2. `/approve {종목명}` — 승인만 (매수와 분리됨)

**⚠️ 설계 변경(2026-08 리팩터, 커밋 `56b03b3` 계열)**: 예전엔 `/approve`가 매수 방식(수량/분할)을
입력받아 AI로 파싱→재확인 후 buy_plan까지 저장했다. **지금은 confirm을 Waiting→Approval로 바꾸기만
한다**. 매수 방식 입력·AI 파싱 재확인 절차는 삭제됐고, 실제 매수는 `/buy`·`/splitbuy`로 완전히 분리됐다.

- `모두`/`전체`/`전부`/`all` → 일괄 승인 (Waiting → Approval)
- free_holdings에 없으면 universe_ko.yaml(보수 종목풀)에서 승인 시도
- 승인 후 응답에 "이제 `/매수` 또는 `/분할매수`로 매수할 수 있어요" 안내

관련 코드: telegram_bot.py의 `_cmd_approve`

### 2-3. `/buy {종목명}` — 일시매수 (즉시 실행)

- Approval 상태 종목만 가능
- 수량 조정 UI(±1/±5/±10/±100/±1000, "반/전액" 프리셋 버튼)
- `[✅ 매수]` → 즉시 주문 실행. buy_plan 저장 없음.

관련 코드: telegram_bot.py의 `_cmd_buy`

### 2-4. `/splitbuy {종목명}` — 분할매수 (buy_plan 저장, 즉시 주문 아님)

- 수량 조정 UI까지는 `/buy`와 동일하나, `[✅ 분할매수 설정]` 누르면 바로 주문하지 않고
  분할 수량·주기(매일/매주/격주/매월) 설정 화면으로 이동
- 저장 → free_holdings 항목에 `buy_plan: {onetime, split:{every, qty}}` 기록, confirm을 Approval로 재확정
- **⚠️ 실행은 별도 수동 러너 `mytrading/order_runner.py`가 담당** — 텔레그램 봇 자체는 buy_plan을
  저장만 하고 주문을 내지 않는다. `order_runner.py --execute`를 사람이 직접 돌려야 실제 매수가 나간다
  (기본은 `--execute` 없이 dry-run). **cron에 연결돼 있지 않다.**
- **⚠️ 주기(every)가 실제로 강제되지 않음**: `order_runner.py`의 대상 수집 로직은
  `split.qty > 0`이면 매번 무조건 매수 대상에 넣는다 — "매주"로 설정해도 실행할 때마다(매일 돌리면
  매일) 다시 잡힌다. UI의 주기 선택은 현재 표시·의도 기록용일 뿐, 코드가 날짜를 검사해 걸러주지 않는다.
  `onetime`은 1회 소진 후 필드가 지워져 정상 동작한다.

관련 코드: telegram_bot.py의 `_cmd_splitbuy`, `_save_buy_plan` · 실행은 `mytrading/order_runner.py`

### 2-5. `/reject`, `/pause` — 거절·중단 (+ 일괄)

- `/reject {종목명}` → confirm: Rejected (AI가 다음 스캔에서 재추천 안 함)
- `/pause {종목명}` → confirm: Paused (신규 매수/매도만 중단, 기존 보유는 유지)
- 둘 다 `모두`/`전체`/`전부`/`all` 일괄 지원
- **옛 설계에 있던 `/remove`는 구현되지 않았다** — `/reject`·`/pause`가 사실상 그 역할을 대체 (커밋 `56b03b3` "종목 상태 전이를 버튼→명령어로")

관련 코드: telegram_bot.py의 `_cmd_set_state`

### 2-6. `/sell {종목명}` — 매도 (즉시 실행)

- 자유 종목 중 보유분만 대상, 승인 상태 체크 없음
- 수량 UI → `[💰 매도]` → 즉시 주문 실행

관련 코드: telegram_bot.py의 `_cmd_sell`

### 2-7. `/list` — 내 종목 목록

옛 설계 문서엔 "나중에 추가" 항목이었으나 이미 구현되어 있다.

관련 코드: telegram_bot.py의 `_cmd_list`

---

## 3. 계좌·모드

### 3-1. `/mode {실전|모의}` — 조회·주문 모드 전환

- `.telegram_mode` 파일(`~/KIS/config/`)에 `prod`/`vps` 저장, 조회 시 이 파일을 우선 확인
- **systemd 환경변수 주의**: `kis-telegram-bot.service`의 `Environment=KIS_MODE=vps`를 제거해야
  `/모드 실전`이 먹는다. 이 환경변수가 남아있으면 봇이 항상 vps로 고정된다.
  이 안전장치를 없앤 대신, **실전 전환의 유일한 방어선은 `/모드` 명령 자체**다. 평소엔 모의로 두고
  실전이 필요할 때만 전환할 것.

관련 코드: telegram_bot.py의 `_cmd_mode`

### 3-2. `/account {전체|일반|연금|IRP|ISA}` — 계좌 상세

- 연금/ISA/일반 판별. ISA는 이름 기반 판별(prod 계좌번호가 01로 겹쳐서)
- **IRP는 거래·조회 모두 불가**(앱키 발급 자체가 안 됨) — "주문만 불가"가 아니라 완전 조회 불가
- 모의 모드 = 모의계좌만, 실전 모드 = 일반·ISA 각각 실전 잔고 (연금/IRP는 실전에서도 조회 불가라 제외)
- 계좌별로 KIS 인증 토큰이 분리 저장된다(`KIS{날짜}_{모드}_{유저}_{계좌}`) — 예전엔 토큰 파일이 계좌 공용이라
  ISA 조회 시 일반증권 잔고가 잘못 나오는 버그가 있었고, 이 분리로 해결됨

관련 코드: telegram_bot.py의 `_cmd_account`

### 3-3. `/status` — 모드 + 계좌별 요약 + 보수 투자 시작/중지 스위치

- 모드(모의/실전), 계좌별 총평가금액, **계좌별 보수(배당) 자동매매 활성 여부**를 한 화면에 표시
- `[계좌명 시작]`/`[계좌명 중지]` 버튼 → `allocations.yaml`의
  `users.{user}.accounts.{계좌}.trading_active`를 토글
- **실제 주문 게이트로 동작한다**: `trading_active`가 false인 계좌는 `/buy`·`/splitbuy`·`/sell`
  주문 자체가 차단된다 — "먼저 /상태에서 시작을 눌러야 매매 가능"
- 이 기능은 두 원본 문서 어디에도 없던 내용이다(커밋 `75bd158` "/상태 개편 + 계좌별 보수 투자 시작/중지 스위치").

관련 코드: telegram_bot.py의 `_cmd_status`, `_trading_active`/`_set_trading`

---

## 4. 자금 배분 (`/alloc`)

### allocations.yaml 구조

`계좌 > 모드(vps/prod) > {moderate, free}` 3단 구조.

```yaml
users:
  Owner:
    accounts:
      일반증권:
        vps: {moderate: 5000000, free: 3000000}   # 모의
        prod: {moderate: 0, free: 0}              # 실전
      ISA:
        prod: {moderate: 0, free: 0}              # ISA는 실전만
```

- `/비중 [일반/ISA]` — 모의는 `/비중`만(계좌 하나), 실전은 계좌 지정 필요
- 총자산도 계좌별 조회라 모드·계좌를 혼동해 잘못된 잔고가 섞이는 문제가 해소됨
- 금액 설정은 모드×계좌별로 각각 저장

> **주의**: `_rt_dump(data, path)` 인자 순서 — 첫 인자가 data. 반대로 쓰면 CommentedMap 에러.

관련 코드: telegram_bot.py의 `_cmd_alloc`

---

## 5. 현금 운용

### 5-1. 설계

cash 몫(총자산 − moderate − free, 자동 계산)을 **직접 현금**과 **원화 단기채 ETF**로 나눈다.
목적: 쉬는 현금을 놀리지 않되 value_range 매수 기회가 오면 즉시 현금으로 복귀. ETF는 예금자보호 안 되고 D+2 현금화.

원화 단기채 ETF 3종(운용사 분산):

| 종목 | 코드 | 운용사 | 비중 | 특징 |
|------|------|--------|------|------|
| TIGER 단기통안채 | 157450 | 미래에셋 | 40% | 통안채, 신용위험 최소, 보수 최저 |
| SOL 초단기채권액티브 | 469830 | 신한 | 30% | 잔존만기 3개월 이내 |
| KODEX 단기채권PLUS | 214980 | 삼성 | 30% | 잔존만기 1년 미만 |

미국달러 단기채(TIGER 미국달러단기채권액티브, 329750)는 **조건부** 편입:
① 환율 순차 상승(6개월<3개월<현재 월평균) ② 미국 FFR > 한국 국고채 3년 ③ 사용자 승인.
셋 다 충족해야 하고, 자동 매수가 아니라 알림 후 사람이 승인한다.

### 5-2. cash_plan 저장 구조 (allocations.yaml 최상위, users 밖 — 전 사용자·계좌 공통)

```yaml
cash_plan:
  krw_ratio: 80
  krw_etfs:
    - {code: "469830", name: "SOL 초단기채권액티브", weight: 30}
    - {code: "157450", name: "TIGER 단기통안채", weight: 40}
    - {code: "214980", name: "KODEX 단기채권PLUS", weight: 30}
  usd_bond:
    code: "329750"
    name: "TIGER 미국달러단기채권액티브"
    enabled: false
```

ETF 목록·비율은 공통, **금액만 계좌별로 다르다**.

관련 코드: telegram_bot.py의 `_load_cash_plan`

### 5-3. `/alloc 현금` (`/비중 현금`) — 안내·조정

- moderate·free 미설정이면 먼저 설정하라고 안내
- 현금 `(100−krw_ratio)%` = 직접 채권 매수용, `krw_ratio%` = 원화 ETF(비중 순 정렬, 종목별 금액 = 원화ETF총액×weight/합계)
- 달러 조건 충족 시 `[달러 편입 승인]` 버튼, 미충족 시 이유 표시
- 끝에 `[−5%][+5%][✅ 저장]` 스테퍼로 `krw_ratio` 즉시 조정

관련 코드: telegram_bot.py의 `_cmd_cash`

### 5-4. `/cashbuy` (`/현금매수`) — cash_plan 실제 매수

1. 계좌별 여유현금 × krw_ratio로 ETF별 금액·수량 계산, 보유 수량도 함께 조회
2. 계획 표시 + `[✅ 전체 매수 승인][❌ 취소]` 버튼
3. 승인 → 종목별로 즉시 시장가 주문
4. 안전장치: 정규장(평일 09:00~15:30)만, IRP 등 주문 불가 계좌 차단, 모드 표시, 버튼 승인 게이트
5. 달러 ETF는 조건 충족+enabled여도 **정보만 표시** — 실제 매수는 원화 몫만(금액 계산식이 명시적으로
   `* 0.0`로 곱해져 있어 배분 규칙 미확정임을 코드 자체가 보여줌)

관련 코드: telegram_bot.py의 `_cmd_cash_buy`, `_cash_buy_plan`

---

## 6. 시스템 관리 — `/reboot` (`/재부팅`, owner 전용)

- **owner 역할만** 실행 가능(§0 권한 검증 결과)
- **매매시간(개장일 09:00~15:30)**: `reboot_password`(kis_devlp.yaml) 입력 요구
- **장외 시간**: `[재부팅][취소]` 확인 버튼
- 실행: `sudo /usr/sbin/reboot` (sudoers NOPASSWD)
- 재부팅 후 봇은 systemd `enable` 설정으로 자동 복귀

관련 코드: telegram_bot.py의 `_cmd_reboot`

---

## 7. 안전장치 요약

1. 화이트리스트: 등록된 chat_id만 (토큰 노출돼도 미등록 ID는 거부)
2. 사용자 격리: 각자 자기 free_holdings만
3. 단계적 승인: 추가(Waiting) → 승인(Approval) → 매수(/buy·/splitbuy) — 한 번에 안 됨
4. **계좌별 매매 on/off 스위치**(`/status`, §3-3) — `trading_active=false`면 매수·매도 자체가 차단됨
5. reboot 보호: owner 전용 + 매매시간 비밀번호
6. `/cashbuy` 등 실주문 경로는 정규장 시간에만, 버튼 승인 게이트 필수

---

## 8. 저장 구조

- 쓰기는 전부 `ruamel.yaml` 라운드트립을 사용 — 주석·따옴표·필드 순서 보존.
  (옛 설계 문서의 "`yaml.safe_dump(..., sort_keys=False)`" 서술은 현재 코드와 다르다 — 그 방식은 더 안 씀)
- **free_holdings confirm 상태**: `Waiting`(추가만) / `Approval`(승인, buy_plan 있을 수 있음) /
  `Rejected` / `Paused` / `Bought`·`Sold`·`SellRequested`(주문 실행 후 order_runner.py가 매기는 상태)
- **universe_ko.yaml confirm 상태**: `Approval`/`Paused`/`Waiting`/`Rejected` 4단계만
  (DIVIDEND_FILTER_DESIGN.md §5b 참고 — free_holdings보다 상태 종류가 적음, 서로 다른 스키마이니 혼동 주의)
- **free_holdings는 계좌 단위로 저장되며 모드 구분이 없다** — `free_holdings > {유저} > {계좌명} > [목록]`
  구조로 계좌명만으로 인덱싱하고 vps/prod 키가 없다. 같은 계좌면 모의·실전 양쪽에서 같은 free_holdings를
  공유한다(반면 allocations의 moderate/free 금액은 `계좌 > 모드`로 나뉜다 — 서로 다른 구조이니 혼동 주의).
- sector·industry는 업종전망 기반 주의 알림용으로 저장

---

## 9. 남은 작업 / 미검증 (2026-08-14 재검증)

CASH_PLAN_AND_ACCOUNTS.md의 옛 TODO를 그대로 옮기지 않고 코드로 재확인했다 — 3건 모두 여전히 미해결이다.

- **cash_plan 매수 검증** — 모의 `/현금매수` 실제 테스트 아직 없음(문서 커밋 이후 관련 후속 커밋 없음, 코드에 검증 완료 표시 없음). 정규장에만 주문 나가는 제약 때문에 테스트 기회가 제한적. → 이후 실전.
- **달러 ETF 배분 규칙 확정** — 여전히 미구현. 코드가 `* 0.0`로 명시적으로 배정을 0으로 고정해둠.
- **중복 매수 방지** — 여전히 미구현. 계획 생성 시 보유 수량(`held`)을 표시는 하지만, 매수 수량 계산에서 차감하지 않는다.
- **(신규 발견) `/splitbuy`의 주기(every) 미강제** — §2-4 참고. `order_runner.py`가 cadence를 검사하지 않아, 설정한 주기와 무관하게 실행할 때마다 매수 대상에 다시 잡힌다. cron에도 안 걸려 있어 지금은 사람이 수동으로 돌려야 하니 실질적 위험은 낮지만, 자동화(cron 연결)를 하기 전에 반드시 고쳐야 한다.

### 방침

실제 매수는 실전(prod)에서 진짜 돈이 나가므로, **모의에서 먼저 구현·검증한 뒤 실전 코드로 넘어간다.**
소액 테스트 + 승인 게이트를 신중히 유지한다.
