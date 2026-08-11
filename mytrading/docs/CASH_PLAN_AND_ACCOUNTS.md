# 계좌·비중·현금 운용 UI (2026-08 주간 작업)

봇의 계좌 조회·자금 배분·현금 운용을 **모의/실전 × 일반/ISA** 체계로 정리하고,
현금(cash) 몫을 단기채 ETF로 운용하는 흐름을 구현한 기록.

---

## 1. 모드 전환 (`/모드`)

봇에서 조회·주문 모드를 모의(vps)/실전(prod)으로 전환한다.

- `/모드 실전` · `/모드 모의` — `_cmd_mode` (별칭 `/모드` → `/mode`)
- `.telegram_mode` 파일(`~/KIS/config/`)에 `prod`/`vps` 저장 (`_bot_mode_load` / `_bot_mode_save`)
- `_is_paper()`가 이 파일을 우선 확인
- `common.resolve_mode()`도 이 파일을 환경변수 다음(2순위, prod 허용)으로 읽음

### ★ systemd 환경변수 제거 (중요)

`kis-telegram-bot.service`에 있던 `Environment=KIS_MODE=vps`를 제거했다.
이 환경변수가 `resolve_mode`의 1순위라, 봇 프로세스가 항상 vps로 고정되어
`/모드 실전`이 먹지 않는 근본 원인이었다.

- 제거 후 봇이 `.telegram_mode` 파일(`/모드` 명령)을 따른다.
- **이로써 "봇은 무조건 모의"라는 환경변수 안전장치가 사라졌다.**
  이제 실전 안전장치는 `/모드` 뿐이다.
- 평소 `/모드 모의`로 두고, 실전 매매가 필요할 때만 전환할 것.

---

## 2. 계좌 유형 조회 (`/계좌`)

`account_type(acc)` → 연금 / ISA / 일반 판별 (accounts.py)

- 연금 = prod 코드 22/29, 또는 이름에 IRP/연금/퇴직
- ISA = 이름에 ISA/종합자산/개인종합 (prod 01이라 이름 기반 판별)
- 일반 = 기본

### IRP 정정

IRP는 **거래·조회 모두 불가**하다. 앱키 발급 자체가 안 되어 KIS API 조회도 불가능하다.
기존의 "주문 불가·조회만 가능"은 오류였다.

### 조회 동작

`/계좌 [전체/일반/연금/IRP/ISA]` 유형별 조회.

- 모의 모드 = 모의계좌만 (일반증권 모의 잔고)
- 실전 모드 = 일반·ISA 각각 실전 잔고
- 연금/IRP는 조회 불가라 실전에서도 제외
- 헬퍼: `_acct_balance_lines(user, account_name, is_paper)`

---

## 3. 계좌별 조회 (`get_brokerage`)

`get_brokerage(account_name)` 인자 추가 (common.py, 기본 None = 기존 동작).
`_resolve_account(is_paper, account_name)`도 계좌 지정을 받는다.
KIS_ACCOUNT 환경변수 대신 인자로 계좌를 지정해, 실전 일반·ISA를 각 앱키로 각각 조회한다.

### ★★ 계좌별 토큰 파일 분리 (핵심 버그 수정)

**증상:** ISA를 조회해도 일반증권 잔고가 나왔다.

**원인:** kis_auth의 `token_tmp`가 날짜 파일 하나(`KIS{YYYYMMDD}`)라
계좌끼리 토큰을 공유했다. 첫 계좌(일반증권)의 토큰을 ISA도 재사용했다.

**해결:** `_inject_auth_cfg`에서 `token_tmp`를
`KIS{날짜}_{모드}_{유저}_{계좌}`로 분리했다.
`source = "{유저}/{계좌}"`를 태그로 쓴다.
유저를 추가하면 자동으로 유저별로도 갈린다.

**검증:** 일반 23,180원 · ISA 108,560,772원(10종목) 각각 정확히 조회됨.

---

## 4. 자금 배분 모드×계좌 구조 (`/비중`)

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

- portfolio.py 로드가 `"계좌|모드"` 키로 파싱 (모드 계층 판별, 평면 구조는 vps 폴백)
- `allocation_for(user, account, mode)` — 모드 인자
- 헬퍼: `_cur_mode()`, `_resolve_alloc_account()` (일반/ISA 별칭)

### 명령

- `/비중 [일반/ISA]` — 모의는 `/비중`만 (계좌 하나), 실전은 계좌 지정
- `_alloc_load(user, account)`, `_free_budget`, `_alloc_snapshot_equity(account)`
  모두 현재 모드 + 계좌를 반영
- 총자산도 계좌별 조회(`get_brokerage(account_name)`)라,
  실전 마이너스(모의 금액을 실전에 적용하던 문제) 해소

### 금액 설정 저장 (모드×계좌별)

- ALLOCUI 마커·콜백에 계좌·모드를 실음:
  `ALLOCUI:{계좌}:{모드}`, 콜백 `alloc_set:{field}:{계좌}:{모드}`
- pending: `[[field, 계좌, 모드]]`
- `_alloc_set_amount`가 pending에서 꺼내 3단 경로(`users > 계좌 > 모드 > field`)에 저장
- 검증: 실전 ISA moderate 1천만 → `ISA > prod`에 저장, 일반증권 미영향

> **주의:** `_rt_dump(data, path)` 인자 순서 — 첫 인자가 data.
> 반대로 쓰면 CommentedMap 에러.

---

## 5. free_holdings 키 정리

free_holdings의 계좌 키가 `일반증권1`이라 accounts의 `일반증권`과 불일치했다.
`_fh_for` 매칭이 실패해 free 종목(SK하이닉스·고려신용정보)이 Allocation에 붙지 않았다.

- `일반증권1` → `일반증권`으로 정리 (ruamel, uv run)
- 검증: `일반증권|vps`·`prod` 둘 다 free_symbols 2개 연결
- free_holdings는 계좌 단위라 vps·prod 양쪽에 다 붙는다 (모드 구분 없음 — 현재는 문제없음)

---

## 6. 현금(cash) 단기채 운용 설계

cash 몫 = 총자산 − moderate − free (자동 계산).
이 현금을 **현금(직접 채권 매수용)**과 **원화 단기채 ETF**로 나눈다.

목적: 쉬는 현금을 놀리지 않되, value_range 매수 기회가 오면 즉시 현금으로 복귀.
ETF는 예금자보호가 안 되고 매도 후 D+2 현금화(즉시 아님).

### 원화 단기채 ETF 3개

시가총액이 큰 것 = 리스크가 작다(유동성·호가 스프레드·괴리율)는 기준으로,
운용사를 분산해 3개 선정.

| 종목 | 코드 | 운용사 | 비중 | 특징 |
|------|------|--------|------|------|
| TIGER 단기통안채 | 157450 | 미래에셋 | 40% | 한국은행 발행(통안채), 신용위험 최소, 보수 0.09% 최저 |
| SOL 초단기채권액티브 | 469830 | 신한 | 30% | 잔존만기 3개월 이내, 가장 짧음 |
| KODEX 단기채권PLUS | 214980 | 삼성 | 30% | 잔존만기 1년 미만, 약간 김 |

- 셋 다 연 3% 안팎 수익률로 큰 차이 없음
- 통안채가 가장 안전해서 40%, 만기 구간이 다른 SOL·KODEX를 30%씩 분산

### 미국달러 단기채 (조건부)

TIGER 미국달러단기채권액티브 (329750, 미래에셋).
환율 변동이 원금을 흔들어(52주 ±9%) 무조건 배제가 아니라 **유리할 때만** 편입.

**편입 3조건 (셋 다 충족 + 승인):**

1. **환율 순차 상승** — 6개월 전 월평균 < 3개월 전 월평균 < 현재 월평균
   (방향 확인 — 오르는 흐름일 때 편입, "내리는 칼" 회피).
   위치(평균 대비)가 아니라 방향(오르는 중)을 본다.
   미래 예측이 아니라 과거 3점의 방향 확인이므로 "예측 금지" 원칙과 충돌하지 않는다.
2. **미국 단기금리 > 한국 단기금리** — 미국 FFR vs 한국 국고채 3년
   (단기채를 사니 단기끼리 비교).
3. **사용자 승인** — 자동 매수가 아니라 알림 후 확인.

뒤집어 말하면 **"한국 단기채가 높거나 환율이 내리고 있으면 매수하지 않는다."**

**현재 상태(작업 시점) — 세 조건 미충족이라 편입 안 함:**
- 환율이 3개월 전 고점 찍고 하락 중 (순차 상승 아님)
- 미국 FFR < 한국 국고채 3년 (한국 단기금리가 높음)

규칙이 "지금 달러로 갈 이유 없다"를 정확히 판정한다.

---

## 7. cash_plan 저장 구조

allocations.yaml **최상위**(users 밖, 공통)에 저장.

```yaml
cash_plan:
  krw_ratio: 80            # 원화 단기채 비중 (%), 설정 가능
  krw_etfs:
    - {code: "469830", name: "SOL 초단기채권액티브", weight: 30}
    - {code: "157450", name: "TIGER 단기통안채", weight: 40}
    - {code: "214980", name: "KODEX 단기채권PLUS", weight: 30}
  usd_bond:
    code: "329750"
    name: "TIGER 미국달러단기채권액티브"
    enabled: false         # 조건 충족 + 승인 시 true
```

- **ETF 목록·비율은 모든 유저·계좌 공통** (동생 유저가 추가돼도 같은 플랜).
  cash_plan이 users 밖이라 유저·계좌와 독립적이다.
- **금액만 각 계좌의 cash로 다르다** (일반증권 cash 200만 → 원화 160만, ISA cash 1억 → 8천만).
- `_load_cash_plan()` 헬퍼가 최상위에서 읽는다.

---

## 8. `/비중 현금` 안내 (`_cmd_cash`)

- moderate·free 미설정(cash 0)이면 "보수·자유 먼저 설정" 유도
- 설정됐으면 현금 분할 안내:
  - 현금 `(100 − krw_ratio)%` = 직접 채권 매수용
  - 원화 단기채 `krw_ratio%` — 저장된 krw_etfs 목록을 비중 순 정렬,
    종목별 금액 = 원화ETF총액 × weight/합계
- 달러 조건 판정(`_usd_bond_cond`):
  - 충족 시 → 달러 정보 + `[달러 편입 승인]` 버튼
    (`USDBONDAPPROVE` → `usdbond_approve` 콜백 → `cash_plan.usd_bond.enabled = true` 저장)
  - 미충족 시 → "달러 제외" + 이유 (한국 단기채 높음 / 환율 하락 중)

### 원화 비율 조정 UI

`/비중 현금` 끝에 `[− 5%] [+ 5%] [✅ 저장]` 스테퍼.

- `CASHRATIO` 마커, if/else 밖이라 달러 조건과 무관하게 항상 표시
- pstep과 같은 방식(콜백에 값 실어 증감 → `_edit_markup` 갱신)
- `cashratio_save`가 `cash_plan.krw_ratio` 저장 (0~100 범위 제한)

---

## 9. cash_plan 실제 매수 (`/현금매수`) — 구현 완료, 미검증

`/현금매수 [계좌]` (별칭 `/현금매수` → `/cashbuy`)

### 흐름

1. `_cash_buy_plan(user, account)` — 매수 계획 생성
   - 각 ETF 금액 = cash × krw_ratio/100 × weight/합계
   - 수량 = 금액 ÷ 현재가 (1주 단위 내림)
   - 보유 수량도 조회 (중복 매수 정보)
2. `_cmd_cash_buy` — 계획 표시 + `[✅ 전체 매수 승인] [❌ 취소]` 버튼
   - `CASHBUYAPPROVE` 마커
   - 승인 pending(`cashbuy:{key}`)에 `{acc, items:[{code, name, qty}]}` 저장
3. 콜백 `cashbuy_approve` → 각 종목 `_execute_order_now`(submit_order) 실행
   - `cashbuy_cancel`은 취소

### 안전장치 (`_execute_order_now`에 내장)

- 정규장만 (평일 09:00~15:30) — 장 시간 아니면 안내만
- `assert_can_order` (IRP 등 주문 불가 계좌 차단)
- 모드 표시 (모의 / 실전)
- 승인 게이트 (버튼 없이 자동 매수 안 함)

### 달러 ETF

usd_bond enabled + 조건 충족이어도 계획에 **정보만 표시**한다.
배분 규칙이 아직 확정되지 않아 실제 매수는 하지 않는다.
현재는 원화 몫(krw_ratio)만 실제 매수한다.

---

## 남은 작업

- **cash_plan 매수 검증** — 모의 `/현금매수` 실제 테스트 (정규장에만 주문이 나감) → 이후 실전
- **달러 ETF 배분 규칙 확정** — 지금은 원화 몫만 ETF 매수, 달러는 정보 표시만
- **중복 매수 방지** — 현재는 보유 수량 표시까지만 (자동 차감 미구현)

### 방침

실제 매수는 실전(prod)에서 진짜 돈이 나가므로,
**모의에서 먼저 구현·검증한 뒤 실전 코드로 넘어간다.**
소액 테스트 + 승인 게이트를 신중히 유지한다.
