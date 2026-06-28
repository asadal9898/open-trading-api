# 배당주 자동 필터 설계 (다음 세션 구현)

> 상태: **설계, 구현 대기**
> 목적: 정량 조건으로 배당주 후보를 자동 선별 → value_range(보수) 종목 풀 보강
> 원칙: 자동 선별은 **후보 제시**까지. 실제 universe 추가는 백테스트 검증 후 사람이 결정.

---

## 1. 차영석 정의 — 배당주 조건

3가지 정량 조건을 **모두** 만족:
1. **시가배당률 > 현재 금리** — 배당수익률이 기준금리(또는 국고채 수익률)보다 높음
2. **영업이익 꾸준** — 최근 N년 영업이익이 안정적으로 (+) (적자/급감 없음)
3. **부채비율 낮음** — 재무 안정성 (예: 부채비율 < 100% 또는 업종 평균 이하)

→ 셋 다 통과한 종목 = 배당주 후보.

---

## 2. 필요 데이터 & KIS API

현재 우리는 가격·거래량만 보유. 재무·배당 데이터 통로 없음
(`get_financial_data` 는 원본 미구현 → None).

→ KIS 국내주식 재무/배당 API 를 mytrading/ 에 새로 붙여야 함.

### KIS API (examples_llm/domestic_stock 에서 확인 완료 ✅)
배당주 3조건에 필요한 API 가 모두 존재:

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
- `finance_financial_ratio` — 재무비율 종합 (여러 지표 한 번에)

**경로 예:** examples_llm/domestic_stock/{name}/{name}.py
→ 다음 세션에 각 함수 시그니처(파라미터·응답 필드) 확인 후 래퍼 작성.
→ finance_financial_ratio 가 여러 지표를 한 번에 주면 호출 수 절약 가능 (우선 확인).

### 금리 기준값 (조건 1)
- 이미 거시 데이터에 **미국채10년(ust10y)** 등 금리 보유.
- 한국 기준금리 또는 국고채3년 수익률을 기준으로. (국고채3년 ETF 가격에서 역산 or 별도 금리 데이터)
- 간단히는 config 에 `dividend_filter.rate_threshold: 3.0` (%) 두고 사람이 갱신.

---

## 3. 구현 계획 (다음 세션)

### 3-1. 재무 API 래퍼 (mytrading/finance_data.py 신규)
```
get_dividend_yield(symbol) -> float | None   # 시가배당률 %
get_operating_profit(symbol, years=3) -> list # 최근 N년 영업이익
get_debt_ratio(symbol) -> float | None        # 부채비율 %
```
- examples_llm 의 재무 API 를 import 재사용 (원본 수정 금지 원칙).
- 레이트리밋 주의 (종목마다 호출 → 텀 필요).

### 3-2. 배당주 스크리너 (mytrading/find_dividend_stocks.py 신규)
```
uv run python mytrading/find_dividend_stocks.py 005930 000660 ...   # 특정 종목 검사
uv run python mytrading/find_dividend_stocks.py --universe          # universe 종목 검사
```
- 입력 종목들에 대해 3조건 검사 → 통과/탈락 + 이유 출력.
- 출력 예:
  ```
  종목       배당률   영업이익(3년)      부채비율   판정
  -------------------------------------------------------
  고려신용정보 5.2%   흑자 꾸준          45%       ✅ 배당주
  XXX          2.1%   ...                ...       ❌ 배당률 낮음(<금리)
  ```
- config 임계값: `dividend_filter: { rate_threshold, debt_max, profit_years }`

### 3-3. 전체 종목 스캔 — 코스피/코스닥 주말 분할 ★ (차영석 확정)

**핵심 통찰 (차영석):**
- 시가배당률 = 배당금 ÷ **주가**. 배당금은 분기마다 갱신(느림)이지만 **주가는 매일 변함**
  → 주가 하락 시 배당률 상승. 월 1회 스캔이면 이런 기회 놓침 → **자주 스캔 필요**.
- **소외 배당주는 코스닥에 많음**. 코스피는 대형주 위주라 시가배당률 높은 소외주 적음.
  (고려신용정보·모토닉도 사실상 중소형. value_range 본진은 코스닥)
  → 거래대금 반분할보다 **코스피/코스닥 분할**이 자연스러움 (마스터 파일도 이미 분리).

**스캔 설계:**
```
[주말 분할] 배당주 전체 스캔 (장 안 열림 → API 한가, 매매와 안 부딪힘)
  ├─ 코스피 (토요일 또는 홀수주)
  │    1. 거래량/거래대금 하한 컷 (잡주 제외 — 자동매매 유동성)
  │    2. 남은 종목 배당주 3조건 검사
  │    3. 통과 + 거래량 충분 → universe moderate 에 confirm:Waiting 추가
  └─ 코스닥 (일요일 또는 짝수주)  ★ 소외 배당주 본진
       동일 (거래량 컷 → 3조건 → Waiting 추가)

  공통: Rejected 종목 건너뜀(재추천 방지), 통과 시 텔레그램 알림.
```

**빈도/시점 (차영석 확정 — 토/일 분할, 매주말):**
- **토요일 새벽: 코스피 스캔 / 일요일 새벽: 코스닥 스캔** (소외 배당주 본진).
- 하루 한 시장씩 → 부담 절반. **매주말마다 전체 시장 검사** → 주가 변화 매주 추적
  (격주 홀짝보다 촘촘: 각 종목이 2주 1회가 아니라 매주 검사됨).
- 실행 예: 토 06:00 코스피, 일 06:00 코스닥. weekly_report(토 15:00)와 안 겹침. 새벽이라 API 한가.
- ※ 재무 API 부담 → 거래량 하한으로 1차 압축 필수.

**예상 소요 (거래량 컷 후):**
- 코스피 ~300개 × 1.5초(API 2회+텀) ≈ 8분 / 코스닥 ~400개 ≈ 10분. 주말 새벽이라 부담 없음.

**거래량 하한 (잡주 컷):**
- 코스닥은 거래량 적은 잡주 많음. 배당률 높아도 거래량 100주면 자동매매 불가(SOL 채권 사례).
- find_stock_code.py 의 거래량 조회(get_history) + 시총(inquire_price) 재사용.
- 하한 예: 일 거래대금 N억 이상 또는 거래량 N주 이상 (config 로 조절).
- **2단계 압축**: ①거래량만 보고 잡주 컷(빠름) → ②남은 것만 재무 3조건(느림). 재무 호출 최소화.

**구현 메모:**
- kospi_code.mst / kosdaq_code.mst (find_stock_code.py 파싱 재사용).
- cron: 토 06:00 코스피, 일 06:00 코스닥 (weekly_report 패턴 참고). 휴장 무관(주말 고정).
- 각 스캔 끝 → 텔레그램 "토요일 코스피 배당주 후보 N개" / "일요일 코스닥 배당주 후보 N개".
- 무거우니 진행상황 로그 + 중간 실패해도 이어가게(종목별 try/except).

---

## 4. 주도주(momentum)는 별도 — 후보 제시 방식

"시장 주도주"는 정량 기준이 애매(섹터 순환, 질적 판단).
→ 자동 선별보다 **후보 제시 + 사람 판단**:
- 거래대금 상위 종목 받기 (KIS 거래량/거래대금 순위 API) → 후보 목록.
- 차영석이 시장 주도 섹터 보고 선택.
- (선택) 추세 필터: get_trend 로 5일선·20일선 위 + 신고가 근처만.
- (선택) 수급 필터: 외인·기관 순매수 — 아래 API 활용.

### 주도주 관련 KIS API (확인됨)
- `inquire_investor` — 종목별 투자자(외인·기관·개인) 매매
- `investor_trade_by_stock_daily` — 종목별 일별 투자자 매매
- `inquire_investor_daily_by_market` — 시장별 투자자 동향
- `invest_opinion` / `invest_opbysec` — 투자의견·증권사별 의견
- ※ 거래대금 순위 API 는 ranking 쪽에서 별도 확인 필요.
- ※ 배당주 필터 완성 후 별도 진행.

---

## 5. 종목 추가 원칙 (반복)
- 자동 필터는 **후보 제시**까지. universe.yaml 추가는 사람 + 백테스트 검증 후.
- 거래량·시총도 확인 (find_stock_code.py --vol) — 자동매매 유동성.
- style 부여: 배당주 → value_range, 주도주 → momentum.

---

## 5b. 종목 상태(confirm) 4단계 ★ (차영석 확정)

universe.yaml 각 종목에 **confirm 필드**로 생애주기 표현. candidates.yaml 별도 파일 안 씀
(confirm 으로 충분 → universe 하나로 관리).

| confirm    | 의미                  | 매수 | 매도 | 누가 |
|------------|-----------------------|------|------|------|
| `Approval` | 승인 — 정상 매매       | ✅   | ✅   | Owner / 승인된 AI |
| `Paused`   | 멈춤 — 신규매매 중단    | ❌   | ❌   | Owner (※ 보유는 유지) |
| `Waiting`  | 승인 대기 — AI 추천     | ❌   | ❌   | AI 가 추가 |
| `Rejected` | 거절 — 다시 추천 안 함  | ❌   | ❌   | Owner 가 AI 추천 반려 |

### 매매 규칙
- **confirm == "Approval" 인 종목만 매매 대상.** 나머지는 전부 제외.
- **Paused 특별 처리**: 신규 매수/매도만 중단, **이미 보유한 건 안 팔고 유지**.
  (Waiting/Rejected 는 애초에 보유 없음)
- confirm **없으면 기본 "Waiting"** (안전 — 명시적 승인 없으면 매매 안 함).
  실수로 빠진 종목이 매매되는 것보다, 안 사고 확인하는 게 안전.

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
  (매번 같은 후보 올라오는 것 방지)

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

## 6. 진행 상황 / 다음 세션 시작 순서

### 완료 (이번 세션) ✅
1. ✅ examples_llm 에서 재무·배당 API 확인:
   - `finance_financial_ratio` (국내주식-080): 23년치 한번에. lblt_rate(부채비율)·bsop_prfi_inrt(영업이익증가율)·roe_val(ROE). **조건2·3 둘 다 처리**.
   - `ksdinfo_dividend` (예탁원 배당): per_sto_divi_amt(주당배당금). 연합÷현재가 = 시가배당률. **조건1**.
   - 둘 다 **실전(prod) 전용**. env_dv 없음.
2. ✅ `finance_data.py` 래퍼 작성:
   - `get_financials(symbol, years)` → 부채비율·ROE·영업이익증가율 추이, roe_positive_years.
   - `get_dividend_yield(symbol, price)` → 시가배당률(연배당금합÷현재가).
3. ✅ 테스트 검증: 삼성(배당0.49%❌) / 고려신용정보(배당5.26%·부채79.77%·ROE5년✅) / 모토닉(배당8.35%·부채13.79%✅).
4. ✅ `find_dividend_stocks.py` 스크리너: 3조건 판정, --universe 옵션, config dividend_filter 임계값(기본 배당3%·부채100%·ROE5년).

### 다음 세션 ⬜
5. ⬜ **portfolio.py 로더 확장**: confirm/added_by/added_date 필드 보존 (style 처럼 화이트리스트에 추가).
   없으면 confirm 기본 "Waiting".
6. ⬜ **tradable_symbols() 헬퍼**: confirm=="Approval" 인 종목만 반환. 매매 로직(7번)이 이걸 사용.
   - Paused 는 보유 유지(매도 안 함) 별도 처리.
7. ⬜ **find_dividend_stocks.py --add 옵션**: 통과 종목을 universe moderate 에
   `added_by:AI, confirm:Waiting` 으로 자동 추가. **Rejected 목록은 건너뜀**(재추천 방지).
8. ⬜ **텔레그램 알림**: AI 추가 시 "배당주 발견: XXX (Waiting)" 통지 (weekly_report 또는 별도).
9. ⬜ config 에 dividend_filter 임계값 추가 (선택 — 기본값으로도 동작).
10. ⬜ **전체 스캔 배치** (3-3): 코스피/코스닥 주말 분할. 거래량 컷 → 3조건 → Waiting 추가. cron 토/일.
11. ⬜ 후보 → 거래량(find_stock_code --vol) → 백테스트 → Owner 가 Approval.

### 커밋 예정 (이번 세션 결과물)
```
git add mytrading/finance_data.py mytrading/find_dividend_stocks.py mytrading/docs/DIVIDEND_FILTER_DESIGN.md
git commit -m "feat: 배당주 자동 필터 — 재무 래퍼 + 3조건 스크리너 + confirm 4상태 설계"
```

> ※ 1-b 주문 계획 빌더(7번)와는 독립. 둘 중 우선순위는 차영석이 그때 결정.
> ※ 주도주(momentum) 자동선별은 정량기준 애매 → 후보제시 방식, 배당주 완성 후 별도.