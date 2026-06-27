# 국면별 분할주문 (1-b) 설계

> 상태: **설계 확정, 구현 대기** (토큰 여유 있을 때 구현)
> 작성: 2026-06-28 세션
> 핵심: 종목별 "매수/매도 리듬(속도)" × 시장 국면 → 천천히 실행. 큰 판단은 사람(국면 설정), 시스템은 그 상태를 속도 조절로 실행.

---

## 1. 큰 그림

```
사람(Owner)이 국면 설정 (config market_regime)
        │
        ▼
allocation_plan: 목표 비중 대비 차액 계산 (뭘 얼마 사고/팔지)
        │
        ▼
국면별 동작 결정 ────────────────────────────────┐
  bull     : 종목별 속도대로 매수 (목표까지 채움)      │
  toppish  : 신규 매수 0 + 위험·안전자산 모두 분할 매도  │  ← 차액을 한 번에 안 하고
  bear     : 분할 매수 (떨어질 때 나눠 담기)            │     cadence/slice 로 "천천히"
  sideways : 종목 성격대로                             │
        │                                            │
        ▼ ◄──────────────────────────────────────────┘
종목별 cadence(빈도) × slice(한 번에 %) 적용
        │
        ▼
submit_order 실주문 (모의 먼저 검증 → 실전)
```

핵심 아이디어: **종목마다 "리듬(속도)"을 부여**하고, 거기에 **국면이 방향·여부를 곱한다.**
- 같은 리듬(cadence/slice)을 매수·매도에 대칭으로 사용.
- 예: 삼성전자 `biweekly_even, 25%` → bull이면 격주로 부족분 25%씩 **매수**, toppish면 격주로 보유분 25%씩 **매도**.

---

## 2. 종목별 속도 — universe.yaml 확장

차영석 제안: 종목 뒤에 매수/매도 속도(리듬)를 붙인다.

```yaml
safe:
  - { code: "379800", name: "KODEX 미국S&P500", cadence: "weekly", slice: 10 }
  # 매주 1회, 목표 부족분의 10%씩 — 안전자산은 꾸준히 속도 조절(보수적)

aggressive:
  - { code: "005930", name: "삼성전자",   cadence: "biweekly_even", slice: 25 }
  - { code: "000660", name: "SK하이닉스", cadence: "biweekly_odd",  slice: 25 }
  # 격주로 번갈아 매수 — 분산(같은 주에 몰아서 안 사고 엇갈리게)

moderate:
  - { code: "049720", name: "고려신용정보", cadence: "weekly", slice: 20 }
  - { code: "009680", name: "모토닉",       cadence: "weekly", slice: 20 }
```

### 필드 정의
- **cadence (빈도)**: 얼마나 자주 거래하는가
  - `daily`         : 매 영업일 (※ 잘게 쪼개짐·수수료↑. 백테스트상 과한 분할 불리 → 큰 금액 천천히 넣을 때만)
  - `weekly`        : 주 1회 (기본 추천)
  - `biweekly_even` : 짝수 주차에만 (ISO week number 짝수)
  - `biweekly_odd`  : 홀수 주차에만
  - (확장 여지) `weekly_2x` 주 2회 등 — 필요시 추가
- **slice (한 번에 %)**: 목표까지 부족/초과분의 몇 %씩 채우거나 줄이는가
  - 예: `slice: 10` → 한 번 거래 시 (목표 - 현재)의 10%만큼 주문
  - 작을수록 더 천천히. 안전자산 10%, 개별주 25% 식으로 차등.

### 기본값 (cadence/slice 없는 종목)
- 누락 시 `cadence: weekly, slice: 100` (= 주1회 전액)으로 폴백. 기존 종목 호환.
- ※ 로더(portfolio.py)에서 기본값 주입.

### "매일 산다"의 현실 (주의)
- daily는 수수료 자주 나가고 잘게 쪼개짐. 백테스트 결론: **과한 분할(5·10회)은 꼴찌**.
- → 기본은 **weekly**. daily는 정말 큰 금액을 아주 천천히 넣을 때만.
- 짝수/홀수 주는 **종목 분산**(같은 주에 안 몰리게) 용도로 적합.

---

## 3. 국면 — config market_regime 확장

기존 bull/bear/sideways 에 **toppish(고점)** 추가.

```yaml
market_regime:
  domestic: "bull"          # 국내 국면
  overseas: "bull"          # 해외 국면
  updated: "2026-06-27"
  bull_since: "2024-01"     # 상승 시작 시점 (장기 고점 판단 참고)
  note: "코스피·미국 대세 상승장"
```

`domestic`/`overseas` 값에 **`toppish`** 허용. (예: 5년째 상승이라 고점이라 판단되면 `overseas: "toppish"`)

### 국면별 동작
| 국면 | 매수 | 매도 | 의미 |
|------|------|------|------|
| **bull** | 종목별 속도대로 매수 (목표까지) | 없음 | 정상 상승장 |
| **toppish** | **신규 매수 0** | **위험·안전 모두 분할 매도** (cadence/slice 대로) → 현금↑ | 고점, 방어. 한 번에 안 팔고 천천히 |
| **bear** | 분할 매수 (나눠 담기) | 없음(또는 보수적) | 하락장, 백테스트상 분할 우위 |
| **sideways** | 종목 성격대로 | 종목 성격대로 | 횡보, ETF분할/개별주재량 |

### toppish 핵심 (차영석 결정)
- 고점은 정확히 못 맞히므로 **한 번에 다 팔지 않고 조금씩 비중 축소**.
- **안전자산(S&P500)도 예외 없이** 비중 줄여 매도. ("고점이면 다 같이 천천히 줄인다")
- 신규 매수는 완전 중단. 매도만 cadence/slice 리듬으로 진행.
- 다시 사고 싶으면 사람이 `bull`로 되돌림 (+ 필요시 cash 비중 원복).

---

## 4. "어디까지 팔지" — 매도 목표

toppish 분할 매도 시 목표(어디까지 줄일지)가 필요.

### 방식1 — cash 비중 올려 자동 계산 (추천)
- allocations.yaml `cash` 를 일시 상향 (예: 20 → 50).
- allocation_plan 이 "위험자산이 목표보다 많음 → 매도 차액" 자동 산출.
- toppish 면 그 차액을 한 번에 안 팔고 cadence/slice 로 분할 실행.
- **장점**: 기존 allocation_plan 그대로 사용. 비중 한 줄 + 국면 한 줄만 바꾸면 됨. 직관적.
- 되돌릴 때: cash 원복 + 국면 bull.

### 방식2 — toppish 전용 감축률
- config에 "toppish면 위험자산 매주 X%씩 감축, 현금 Y%까지" 별도 규칙.
- 비중 구조 안 건드림. 더 유연하나 설정 증가.

→ **방식1로 진행 추천** (구현 단순, 기존 재료 재사용). 차영석 최종 확정 대기.

---

## 5. 매도 안전장치 (구현 시 결정)

매도는 매수보다 신중해야 함. 아래는 옵션:
- **손익 구분**: 수익 종목만 덜어낼지 / 손익 구분 없이 비중대로 다 조절할지 — **미정(구현 때 결정)**.
  - 단, 차영석은 "안전자산도 비중 줄여 매도" 입장 → 전반적으로 비중대로 조절하는 쪽에 가까움.
- **최소 매도 단위**: 너무 작은 수량(1주 미만 등)은 스킵.
- **모의 우선**: 반드시 모의(vps)에서 검증 후 실전.

---

## 6. 구현 순서 (다음 세션)

1. **universe.yaml 확장**: 종목에 cadence/slice 필드 추가. portfolio.py 로더에 기본값 주입 + 파싱.
2. **common.py 국면 확장**: get_regime 이 toppish 도 인식. regime_for_symbol 그대로 활용(해외추종 종목은 overseas 국면).
3. **cadence 판정 함수**: 오늘 날짜(ISO week)로 "이 종목 오늘 거래일인가?" 판정. daily/weekly/biweekly_even/odd.
4. **주문 계획 빌더** (allocation_plan 확장 또는 신규 split_order_plan):
   - allocation_plan 의 종목별 차액(목표-보유) 받기.
   - 국면별 방향 결정: bull→매수만, toppish→매도만(+신규매수0), bear→분할매수, sideways→성격대로.
   - cadence 로 "오늘 거래 종목" 필터.
   - slice 로 "이번에 거래할 금액" = 차액 × slice%.
   - → 종목별 (BUY/SELL, 수량) 리스트 산출. **계산/출력만, 주문 안 함** (검증 단계).
5. **모의 주문 연결**: 위 계획 → submit_order (모의 vps). bull 일시매수 / toppish 분할매도 / bear 분할매수 시나리오 테스트.
6. **백테스트 반영**: 과한 분할 배제(이미 결론). cadence/slice 조합을 splitfill_sim 으로 검증 후 실제 종목에 적용.
7. **cron 연결** (선택): 매일/매주 정해진 시각에 "오늘 거래 종목" 자동 실행. 단, 실주문 자동화는 충분한 모의 검증 후.

---

## 7. 재료 (이미 준비됨)

- `common.get_regime(market)` / `regime_for_symbol(symbol, overseas_symbols)` — 국면 (bull/bear/sideways → toppish 추가 예정)
- `allocation_plan.build_plan(snapshot, portfolio, user, account)` — 목표 대비 차액(BUY/SELL/HOLD, diff_value)
- `splitfill_sim` — 분할매수 방식 백테스트 (lump/dca, 횟수·간격)
- `portfolio.py` — universe.yaml/allocations.yaml 로더 (cadence/slice 파싱 추가 예정)
- `account_snapshot.get_snapshot(brokerage)` — 현재 보유 (매도 대상 파악)
- `KISBrokerageProvider.submit_order(symbol, side, qty, type, price)` — 실주문 (모의 검증됨)
- 거시 데이터 (지수·금리·환율·원자재) — 국면 판단 **보조 참고** (자동 아님, 사람이 보고 판단)

## 8. 백테스트 핵심 결론 (반영 필수)
- 분할 방식 평균순위: **일시매수(1위) > 3회×1일 > 5회×1일 > 3회×3일 > 5회×5일 > 10회×1일(꼴찌)**
- 상승장=일시매수 압도적, 하락장=분할 우위, 횡보장=종목 따라.
- → "분할이 무조건 낫다" 틀림. **과한 분할(5·10회) 배제**. cadence/slice 설계에 반영(daily 남용 금지).

---

## 9. 미결정 (구현 때 차영석 확정)
- [ ] 매도 목표: 방식1(cash 비중) vs 방식2(전용 감축률) — **방식1 추천**
- [ ] toppish 매도 시 손익 구분: 수익 종목만 vs 비중대로 전부 — (차영석은 "안전자산도 줄여 매도" → 비중대로 쪽)
- [ ] cadence 에 daily/weekly 외 추가 빈도(주2회 등) 필요한지
- [ ] sideways 의 구체 동작(ETF만 분할? 개별주는?)