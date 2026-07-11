# 배당주 자동 필터 설계

> 상태: **구현·운영 중** (2026-07 기준, 코스피/코스닥 주말 스캔 작동)
> 목적: 정량 조건으로 배당주 후보를 자동 선별 → value_range(보수) 종목 풀 보강
> 원칙: 자동 선별은 **후보 제시(Waiting)**까지. 실제 매매(Approval)는 백테스트 검증 후 사람이 결정.

---

## 1. 차영석 정의 — 배당주 조건

3가지 정량 조건을 **모두** 만족:
1. **시가배당률 > 현재 금리** — 배당수익률이 기준금리(또는 국고채 수익률)보다 높음
2. **영업이익 꾸준** — 최근 N년 영업이익이 안정적으로 (+) (적자/급감 없음)
3. **부채비율 낮음** — 재무 안정성 (예: 부채비율 < 100% 또는 업종 평균 이하)

→ 셋 다 통과한 종목 = 배당주 후보.

---

## 2. 필요 데이터 & KIS API

재무·배당 데이터는 KIS 국내주식 재무/배당 API로 확보. (examples_llm/domestic_stock 에서 확인 완료 ✅)

배당주 3조건에 필요한 API:

**조건1 배당률:**
- `dividend_rate` — 배당률 (시가배당률)
- `ksdinfo_dividend` — 예탁원 배당정보 (배당락일·배당금 등)

**조건2 영업이익:**
- `finance_income_statement` — 손익계산서 (영업이익 직접)
- `finance_profit_ratio` — 수익성비율
- `finance_growth_ratio` — 성장성비율 (영업이익 증감 추이)

**조건3 부채비율:**
- `finance_stability_ratio` — 안정성비율 (부채비율 직접) ★
- `finance_balance_sheet` — 재무상태표
- `finance_financial_ratio` — 재무비율 종합 (여러 지표 한 번에) ← 실제 사용 (23년치 한번에)

### 금리 기준값 (조건 1)
- 거시 데이터에 **미국채10년(ust10y)** 등 금리 보유.
- config `dividend_filter.rate_threshold`(%) 로 사람이 갱신.

---

## 3. 구현 (완료)

### 3-1. 재무 API 래퍼 — `mytrading/finance_data.py` ✅
- `get_financials(symbol, years)` → 부채비율·ROE·영업이익증가율 추이, roe_positive_years
- `get_dividend_yield(symbol, price)` → 시가배당률 (연배당금합 ÷ 현재가)
- examples_llm 재무 API import 재사용 (원본 수정 금지). 레이트리밋 텀 적용.
- (확장) `finance_unified.py` — KIS 우선 + DART 폴백/보완, `get_financials_safe()`

### 3-2. 배당주 스크리너 — `mytrading/find_dividend_stocks.py` ✅
```
uv run python mytrading/find_dividend_stocks.py 005930 000660 ...   # 특정 종목 검사
uv run python mytrading/find_dividend_stocks.py --universe          # universe 종목 검사
uv run python mytrading/find_dividend_stocks.py 종목들 --add        # 통과종목 자동추가(Waiting)
```
- 3조건 검사 → 통과/탈락 + 이유 출력. `add_to_universe()` 로 자동 추가.
- config 임계값: `dividend_filter: { rate_threshold, debt_max, profit_years }` (기본 배당3%·부채100%·ROE5년)

### 3-3. 전체 종목 스캔 — `mytrading/scan_dividend.py` ✅ (코스피/코스닥 주말 분할)

**핵심 통찰 (차영석):**
- 시가배당률 = 배당금 ÷ **주가**. 배당금은 분기 갱신(느림)이지만 **주가는 매일 변함**
  → 주가 하락 시 배당률 상승. 월 1회면 기회 놓침 → **자주 스캔 필요**.
- **소외 배당주는 코스닥에 많음**. 코스피는 대형주 위주.
  → 거래대금 반분할보다 **코스피/코스닥 분할**이 자연스러움 (마스터 파일도 분리).

**스캔 흐름:**
```
[주말 분할] 배당주 전체 스캔 (장 안 열림 → API 한가)
  ├─ 코스피 (토요일)
  │    1. 거래량/거래대금 하한 컷 (잡주 제외)
  │    2. 남은 종목 배당주 3조건 검사
  │    3. 통과 → universe moderate 에 confirm:Waiting 추가 (상위 N개)
  └─ 코스닥 (일요일)  ★ 소외 배당주 본진
       동일 (거래량 컷 → 3조건 → Waiting 추가)
  공통: Rejected 종목 건너뜀(재추천 방지), 통과 시 텔레그램 알림(종목코드+종목명).
```

**빈도/시점 (크론, 매주말):**
- **토 14:00 코스피 / 일 15:00 코스닥** (2026-07 조정: 기존 새벽 06·07시 → 오후로 이동)
- 하루 한 시장씩, 매주말 전체 시장 검사 → 주가 변화 매주 추적.
- **2단계 압축**: ①거래량만 보고 잡주 컷(빠름) → ②남은 것만 재무 3조건(느림). 재무 호출 최소화.
- ※ 스케줄 상세는 `mytrading/docs/CRON_SCHEDULE.md` 참고.

---

## 4. 주도주(momentum)는 별도 — 후보 제시 방식

"시장 주도주"는 정량 기준이 애매(섹터 순환, 질적 판단).
→ 자동 선별보다 **후보 제시 + 사람 판단**:
- 거래대금 상위 종목 받기 (KIS 거래량/거래대금 순위 API) → 후보 목록.
- 차영석이 시장 주도 섹터 보고 선택.
- (선택) 추세 필터: get_trend 로 5일선·20일선 위 + 신고가 근처만.
- (선택) 수급 필터: 외인·기관 순매수.

### 주도주 관련 KIS API (확인됨)
- `inquire_investor` — 종목별 투자자(외인·기관·개인) 매매
- `investor_trade_by_stock_daily` — 종목별 일별 투자자 매매
- `inquire_investor_daily_by_market` — 시장별 투자자 동향
- `invest_opinion` / `invest_opbysec` — 투자의견·증권사별 의견
- ※ 배당주 필터 완성 후 별도 진행 (미착수).

---

## 5. 종목 추가 원칙 (반복)
- 자동 필터는 **후보 제시(Waiting)**까지. Approval(실매매)은 사람 + 백테스트 검증 후.
- 거래량·시총도 확인 (find_stock_code.py --vol) — 자동매매 유동성.
- style 부여: 배당주 → value_range, 주도주 → momentum.

---

## 5b. 종목 상태(confirm) 4단계 ✅ (구현됨)

universe.yaml 각 종목에 **confirm 필드**로 생애주기 표현. (candidates.yaml 별도 안 씀)

| confirm    | 의미                  | 매수 | 매도 | 누가 |
|------------|-----------------------|------|------|------|
| `Approval` | 승인 — 정상 매매       | ✅   | ✅   | Owner / 승인된 AI |
| `Paused`   | 멈춤 — 신규매매 중단    | ❌   | ❌   | Owner (※ 보유는 유지) |
| `Waiting`  | 승인 대기 — AI 추천     | ❌   | ❌   | AI 가 추가 |
| `Rejected` | 거절 — 다시 추천 안 함  | ❌   | ❌   | Owner 가 AI 추천 반려 |

### 매매 규칙
- **confirm == "Approval" 인 종목만 매매 대상.** 나머지는 전부 제외.
  (`portfolio.tradable_symbols()` 가 Approval 만 반환 → `trade_plan` 이 사용)
- **Paused 특별 처리**: 신규 매수/매도만 중단, **이미 보유한 건 안 팔고 유지**.
- confirm **없으면 기본 "Waiting"** (안전 — 명시적 승인 없으면 매매 안 함).

### 추가 필드
- `added_by`: "Owner" / "AI" — 누가 추가했는지
- `added_date`: "YYYY-MM-DD" — 추가일
- `note`: 메모 (업종·사유 등)

### 종목 생애주기 흐름
```
AI 스캔 → universe moderate 에 자동 추가 (added_by:AI, confirm:Waiting)
        → 텔레그램 알림 "배당주 발견: XXX (Waiting)"
        → Owner 확인 (거래량·맥락·백테스트)
        ├─ 좋음   → confirm:Approval  (다음 거래일부터 매매)
        ├─ 보류   → confirm:Paused    (나중에)
        └─ 별로   → confirm:Rejected  (앞으로 추천 안 함)
```
- **Rejected 의 효용**: AI 가 다음 스캔에서 같은 종목 재추천하지 않게 차단.

### universe.yaml 예시
```yaml
moderate:
  - { code: "049720", name: "고려신용정보", style: "value_range",
      added_by: "Owner", confirm: "Paused",  added_date: "2026-06-28", note: "배당주 채권추심" }
  - { code: "009680", name: "모토닉", style: "value_range",
      added_by: "Owner", confirm: "Approval", added_date: "2026-06-28", note: "배당주 자동차부품" }
  - { code: "012345", name: "어떤배당주", style: "value_range",
      added_by: "AI", confirm: "Waiting", added_date: "2026-06-28", note: "배당주 필터 통과" }
```

---

## 6. 구현 현황

### 완료 ✅
1. ✅ examples_llm 재무·배당 API 확인 (finance_financial_ratio 23년치, ksdinfo_dividend 주당배당금). 둘 다 prod 전용.
2. ✅ `finance_data.py` 래퍼: get_financials, get_dividend_yield.
3. ✅ 테스트 검증: 삼성(배당0.49%❌) / 고려신용정보(✅) / 모토닉(✅).
4. ✅ `find_dividend_stocks.py` 스크리너: 3조건 판정, --universe/--add 옵션, config 임계값.
5. ✅ portfolio.py 로더: confirm/added_by/added_date 필드 보존, 기본 "Waiting".
6. ✅ `tradable_symbols()`: confirm=="Approval" 만 반환. Paused 보유 유지 처리. trade_plan 이 사용.
7. ✅ find_dividend_stocks --add / add_to_universe: 통과 종목 자동 추가(Waiting). Rejected 건너뜀.
8. ✅ 텔레그램 알림: AI 추가 시 "배당주 발견" 통지 (종목코드+종목명).
9. ✅ 전체 스캔 배치 `scan_dividend.py`: 코스피/코스닥 주말 분할, 거래량 컷 → 3조건 → Waiting 추가. cron 토/일.
10. ✅ DART 교차검증 (finance_unified): KIS 우선 + DART 폴백. 배당성향 교차검증으로 데이터 오류 탐지.

### 남은 것 ⬜
- ⬜ 후보 → 거래량 확인 → 백테스트 → Owner 가 Approval (운영 루프, 사람 판단 영역)
- ⬜ 주도주(momentum) 자동선별 — 정량기준 애매, 후보제시 방식, 별도 진행

---

## 관련 문서
- 매매 전략 전체: `mytrading/docs/TRADING_STRATEGIES.md`
- 국면별 분할주문: `mytrading/docs/REGIME_SPLIT_ORDER_DESIGN.md`
- 펀더멘털 분석: `mytrading/docs/FUNDAMENTAL_ANALYSIS_DESIGN.md`
- 크론 스케줄: `mytrading/docs/CRON_SCHEDULE.md`