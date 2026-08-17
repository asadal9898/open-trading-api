# 배당주 자동 필터 설계

> 상태: **구현·운영 중** (2026-07 기준, 코스피/코스닥 주말 스캔 작동)
> 목적: 정량 조건으로 배당주 후보를 자동 선별 → value_range(보수) 종목 풀 보강
> 원칙: 자동 선별은 **후보 제시(Waiting)**까지. 실제 매매(Approval)는 백테스트 검증 후 사람이 결정.
>
> ※ 배당은 **종목 평가의 재무 3순위**이기도 하다 (부채 > 영업이익 > 배당).
>   종목 평가 전체 구조는 `FUNDAMENTAL_ANALYSIS_DESIGN.md` 참고.

---

## 1. 차영석 정의 — 배당주 조건

3가지 정량 조건을 **모두** 만족:

1. **시가배당률 > 국고채 3년물 + 프리미엄** — 무위험 수익률보다 충분히 높은 배당
2. **영업이익 꾸준** — 최근 N년 영업이익이 안정적으로 (+) (적자/급감 없음)
3. **부채비율 낮음** — 재무 안정성 (업종별 기준 적용)

→ 셋 다 통과한 종목 = 배당주 후보.

---

## 2. 필요 데이터 & KIS API

**조건1 배당률:**
- `ksdinfo_dividend` — 예탁원 배당정보 (주당배당금·배당락일) ← 실제 사용
- `dividend_rate` — 배당률

**조건2 영업이익:**
- `finance_income_statement` — 손익계산서 (영업이익)
- `finance_growth_ratio` — 성장성비율 (영업이익 증감)
- `finance_financial_ratio` — 분기 조회 시 최근 분기 증가율(YoY) ← 종목평가에서 사용

**조건3 부채비율:**
- `finance_financial_ratio` — 재무비율 종합 (lblt_rate 부채비율, 23년치 한번에) ← 실제 사용

### 금리 기준값 (조건 1) — 국고채 3년물 연동 ✅

**배당률 기준 = 국고채 3년물 + 프리미엄** (코스피 +0.5%p / 코스닥 +1.0%p)

- **한국 시장금리 벤치마크는 국고채 3년물** (미국·일본은 10년물).
  한국 국채시장은 3년물 유동성이 가장 깊고, 배당주는 중기 보유 자산이라
  3년물과 비교하는 게 합리적.
- **금리가 오르면 배당 요구수익률도 자동 상승.** 고정값이면 못 잡는 변화
  (금리 5%인데 배당 4% 종목을 통과시키면 안 됨).
- 프리미엄은 **보수적으로** — 너무 높이면 통과 종목이 사라짐.

**데이터 출처: KCIF INSIGHT 파싱 YAML** (월간 자동 갱신)

    kcif_insight_key_indicators_history.yaml
      history / {최신호} / categories / 국내 채권시장 / 시장금리 / 국고채 3년
      → 시계열 마지막 값 = 최근월 금리

현재 **3.72%** (2026-06호). 상승 추세: 3.04 → 3.56 → 3.59 → 3.72
→ 기준: 코스피 **4.22%** / 코스닥 **4.72%**

**config:**

```yaml
dividend_filter:
  base_rate_source: kcif        # kcif(자동) | fixed(고정값 사용)
  fallback_rate: 3.5            # KCIF 못 읽을 때
  kospi:   { premium: 0.5, rate_threshold: 4.0, debt_max: 100.0, min_volume: 30000, top_n: 20 }
  kosdaq:  { premium: 1.0, rate_threshold: 4.5, debt_max: 80.0,  min_volume: 20000, top_n: 20 }
```

`base_rate_source: fixed` 로 두면 기존 고정값(rate_threshold) 방식으로 복귀.

**※ 거시(KCIF 금리) → 미시(종목 배당 판정) 연결의 첫 사례.**
DECISION_LAYER_DESIGN의 "거시가 미시를 지배" 원칙 구현.

---

## 3. 구현 (완료)

### 3-1. 재무·배당 함수 — `mytrading/finance_data.py` ✅

- `get_dividend_yield(symbol, price)` → 시가배당률 (연배당금합 ÷ 현재가)
  반환: `{symbol, annual_dividend, yield_pct, count}`
- `get_base_rate()` → 국고채 3년물 (KCIF YAML, 실패 시 fallback)
- `dividend_threshold(market)` → 기준선 = 국고채 + premium
- `evaluate_dividend(symbol, market)` → 배당 평가 (종목평가 재무 3순위)
- `get_financials(symbol)` → 부채비율·ROE·영업이익증가율 추이
- (확장) `finance_unified.py` — KIS 우선 + DART 폴백/교차검증

**검증**: 모토닉 8.20% > 4.22% 통과 / 고려신용정보 5.32% > 4.72% 통과 / 삼성전자 0.13% 미달

### 3-2. 배당주 스크리너 — `mytrading/find_dividend_stocks.py` ✅

```
uv run python mytrading/find_dividend_stocks.py 005930 000660 ...   # 특정 종목 검사
uv run python mytrading/find_dividend_stocks.py --universe          # universe 종목 검사
uv run python mytrading/find_dividend_stocks.py 종목들 --add        # 통과종목 자동추가(Waiting)
```

3조건 검사 → 통과/탈락 + 이유 출력. `add_to_universe()` 로 자동 추가.

### 3-3. 전체 종목 스캔 — `mytrading/scan_dividend.py` ✅ (코스피/코스닥 주말 분할)

**핵심 통찰 (차영석):**

- 시가배당률 = 배당금 ÷ **주가**. 배당금은 분기 갱신(느림)이지만 **주가는 매일 변함**
  → 주가 하락 시 배당률 상승. 월 1회면 기회 놓침 → **자주 스캔 필요**.
- **소외 배당주는 코스닥에 많음**. 코스피는 대형주 위주.
  → **코스피/코스닥 분할**이 자연스러움 (마스터 파일도 분리).

**스캔 흐름:**

```
[주말 분할] 배당주 전체 스캔 (장 안 열림 → API 한가)
  ├─ 코스피 (토 14:00)
  │    1. 거래량/거래대금 하한 컷 (잡주 제외)
  │    2. 남은 종목 배당주 3조건 검사
  │    3. 통과 → universe moderate 에 confirm:Waiting 추가 (상위 N개)
  └─ 코스닥 (일 15:00)  ★ 소외 배당주 본진
       동일 (거래량 컷 → 3조건 → Waiting 추가)
  공통: Rejected 종목 건너뜀(재추천 방지), 통과 시 텔레그램 알림(종목코드+종목명).
```

- 매주말 전체 시장 검사 → 주가 변화 매주 추적.
- **2단계 압축**: ①거래량만 보고 잡주 컷(빠름) → ②남은 것만 재무 3조건(느림).
- ※ 스케줄 상세는 `mytrading/docs/CRON_SCHEDULE.md` 참고.
- ⚠️ **레이트리밋**: 종목마다 API 여러 번 호출 → 텀 필요 ("초당 거래건수 초과" 방지).

---

## 4. 주도주(momentum)는 별도 — 후보 제시 방식

"시장 주도주"는 정량 기준이 애매(섹터 순환, 질적 판단).
→ 자동 선별보다 **후보 제시 + 사람 판단**:

- 거래대금 상위 종목 받기 (KIS 순위 API) → 후보 목록
- 차영석이 시장 주도 섹터 보고 선택
- (선택) 추세 필터: get_trend 로 5일선·20일선 위 + 신고가 근처만
- (선택) 수급 필터: 외인·기관 순매수

### 주도주 관련 KIS API (확인됨)

- `inquire_investor` — 종목별 투자자(외인·기관·개인) 매매
- `investor_trade_by_stock_daily` — 종목별 일별 투자자 매매
- `inquire_investor_daily_by_market` — 시장별 투자자 동향
- `invest_opinion` / `invest_opbysec` — 투자의견·증권사별 의견
- ※ **미착수.** 배당주 필터 완성 후 별도 진행.

---

## 5. 종목 추가 원칙

- 자동 필터는 **후보 제시(Waiting)**까지. Approval(실매매)은 사람 + 백테스트 검증 후.
- 거래량·시총도 확인 (find_stock_code.py --vol) — 자동매매 유동성.
- style 부여: 배당주 → value_range, 주도주 → momentum.

## 5b. 종목 상태(confirm) 4단계 ✅ (구현됨)

universe_ko.yaml 각 종목에 **confirm 필드**로 생애주기 표현.

| confirm    | 의미                  | 매수 | 매도 | 누가 |
|------------|-----------------------|------|------|------|
| `Approval` | 승인 — 정상 매매       | ✅   | ✅   | Owner / 승인된 AI |
| `Paused`   | 멈춤 — 신규매매 중단    | ❌   | ❌   | Owner (※ 보유는 유지) |
| `Waiting`  | 승인 대기 — AI 추천     | ❌   | ❌   | AI 가 추가 |
| `Rejected` | 거절 — 다시 추천 안 함  | ❌   | ❌   | Owner 가 AI 추천 반려 |

### 매매 규칙

- **confirm(사람) 이 설정돼 있으면 그 값만으로 판정한다** — Approval만 매매 대상, 나머지 제외.
  confirm이 없으면 auto_confirm(자동, §5c)으로 판정한다. 둘 다 없으면 매매 불가.
  (`portfolio.tradable_symbols()`가 이 합성(confirm 우선, 없으면 auto_confirm) 판정을 수행
  → `trade_plan.build_plan()`이 사용, 2026-08 구현)
- **Paused 특별 처리**: 신규 매수/매도만 중단, **이미 보유한 건 안 팔고 유지**.
- confirm **없으면 기본 "Waiting"** (안전 — 명시적 승인 없으면 매매 안 함).

### 추가 필드

- `added_date`: "YYYY-MM-DD" · `note`: 메모

### 종목 생애주기 흐름

```
AI 스캔 → universe_ko moderate 에 자동 추가 (confirm:Waiting)
        → 텔레그램 알림 "배당주 발견: XXX (Waiting)"
        → Owner 확인 (거래량·맥락·백테스트)
        ├─ 좋음   → confirm:Approval  (다음 거래일부터 매매)
        ├─ 보류   → confirm:Paused    (나중에)
        └─ 별로   → confirm:Rejected  (앞으로 추천 안 함)
```

- **Rejected 의 효용**: AI 가 다음 스캔에서 같은 종목 재추천하지 않게 차단.

### universe_ko.yaml 예시

```yaml
moderate:
  - { code: "049720", name: "고려신용정보", style: "value_range",
      confirm: "Paused",  added_date: "2026-06-28", note: "배당주 채권추심" }
  - { code: "009680", name: "모토닉", style: "value_range",
      confirm: "Approval", added_date: "2026-06-28", note: "배당주 자동차부품" }
```

---

## 5c. 자동 판정 — auto_confirm (score_dividend.py, 2026-08)

**⚠️ §5b는 사람이 매기는 confirm 4단계만 다룬다. 이후 자동 판정 계층(auto_confirm)이
추가되어, universe_ko moderate 종목은 이제 confirm(사람)/auto_confirm(자동) 두 필드를
동시에 갖는 합성 구조다.**

`mytrading/score_dividend.py`가 moderate 종목 전체를 매일 재채점한다:

- 시가총액(만점100)·배당률(만점150, 배당주 취지로 1.5배 가중)·부채비율(만점100)을 각각
  **순위 기반**(1등=만점, 꼴찌=0)으로 채점해 합산(0~350점)
- 배당률 0%(최근 1년 배당 이력 없음) 종목은 순위 계산에서 제외하고 `auto_confirm: "Rejected"`
- 남은 종목(배당 있음)만으로 다시 순위를 매겨 **상위 100 → `auto_confirm: "Approval"`,
  101등 이하 → `auto_confirm: "Paused"`**
- `confirm`(사람) 필드는 절대 건드리지 않는다 — `score`/`mcap_score`/`div_score`/`debt_score`/
  `scored_date`/`auto_confirm`만 기록

**합성 판정 우선순위** (`portfolio.tradable_symbols()`): confirm(사람)이 있으면 그 값이 이긴다
(auto_confirm 무시). confirm이 없으면 auto_confirm으로 판정. 둘 다 없으면 매매 불가.
→ **사람이 한 번이라도 confirm을 정한 종목은 스코어링 등락과 무관하게 그 판단이 유지된다.**

**cron**: 평일 19:30 `KIS_MODE=prod uv run python mytrading/score_dividend.py --save --observe`
— 계산은 한 번만 하고 저장(`--save`, universe_ko.yaml 갱신)과 관찰로그(`--observe`,
`~/dividend_score_log/YYYY-MM-DD.json`, hysteresis 검토용 이력) 둘 다 남긴다.
(`CRON_SCHEDULE.md` 참고 — `scan_dividend.py`와 이름 비슷하지만 별개 스크립트)

**여기서 이어지는 자동매수 파이프라인(B/D)**: auto_confirm=Approval인 종목은
`build_plan("moderate")`의 매수 후보 대상이 되고, `moderate_buy_alert.py`(B, 텔레그램 알림)와
`moderate_order_runner.py`(D-1/D-2, 게이트+dry-run/`--live` 발주)로 이어진다.
**모의(vps)에서 코드는 D-2까지 구현됐지만, 모의계좌 실제 발주 성공 사례는 아직 없다**
(D-3 미완, cron 연결도 D-4 미착수) — "구현"이지 "검증"·"실사용"은 아니다.
자세한 안전장치·단계는 `VALUE_RANGE.md` §0 참고.

---

## 6. 구현 현황

### 완료 ✅

1. ✅ KIS 재무·배당 API 확인 (finance_financial_ratio, ksdinfo_dividend). 둘 다 vps에서도 정상 동작 확인됨(2026-08-14 실측, 고려신용정보 조회).
2. ✅ `finance_data.py` 래퍼: get_financials, get_dividend_yield.
3. ✅ `find_dividend_stocks.py` 스크리너: 3조건 판정, --universe/--add 옵션.
4. ✅ portfolio.py 로더: confirm/added_date 필드 보존, 기본 "Waiting".
5. ✅ `tradable_symbols()`: confirm(사람) 우선, 없으면 auto_confirm(자동)으로 합성 판정 —
    2026-08 auto_confirm 도입으로 확장됨(§5c). trade_plan 이 사용.
6. ✅ add_to_universe: 통과 종목 자동 추가(Waiting). Rejected 건너뜀.
7. ✅ 텔레그램 알림: AI 추가 시 "배당주 발견" (종목코드+종목명).
8. ✅ 전체 스캔 배치 `scan_dividend.py`: 코스피/코스닥 주말 분할. cron 토14시/일15시.
9. ✅ DART 교차검증 (finance_unified): KIS 우선 + DART. 배당성향 교차검증.
10. ✅ **국고채 3년물 연동** (2026-07): 배당 기준을 고정값 → 국고채 + 프리미엄.
    KCIF INSIGHT에서 자동 갱신. `get_base_rate` / `dividend_threshold` / `evaluate_dividend`.
11. ✅ **auto_confirm 자동 판정** (`score_dividend.py`, 2026-08): 시총·배당·부채 300점
    스코어링 → 상위100 Approval / 101↓ Paused / 배당0% Rejected. cron 평일 19:30
    `--save --observe`. 상세 §5c.

### 남은 것 ⬜

- ⬜ **scan_dividend에 국고채 연동 적용** — 스캔 필터도 `dividend_threshold()` 를 쓰도록.
  (현재 스캔은 config 고정값(rate_threshold) 기준일 수 있음 — 확인 필요)
- ⬜ 후보 → 거래량 확인 → 백테스트 → Owner 가 Approval (운영 루프, 사람 판단)
  — **상위 100은 이제 auto_confirm으로 자동 Approval**(§5c)되므로, 사람 판단은 그 위에
  얹히는 override(승인/거절/멈춤)로만 남았다. 완전 자동화는 아니고, 백테스트 검증 후
  사람이 override하는 루프 자체는 그대로 유효.
- ⬜ 주도주(momentum) 자동선별 — 정량기준 애매, 후보제시 방식, 별도 진행

---

## 관련 문서

- **펀더멘털 분석 (종목 평가 전체)**: `mytrading/docs/FUNDAMENTAL_ANALYSIS_DESIGN.md`
- 종합 판단 구조: `mytrading/docs/DECISION_LAYER_DESIGN.md`
- 매매 전략 전체: `mytrading/docs/TRADING_STRATEGIES.md`
- 국면별 분할주문: `mytrading/docs/REGIME_SPLIT_ORDER_DESIGN.md`
- 크론 스케줄: `mytrading/docs/CRON_SCHEDULE.md`