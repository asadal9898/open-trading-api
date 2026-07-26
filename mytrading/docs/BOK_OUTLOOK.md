# 한국은행 경제전망 파이프라인

한국은행 경제전망보고서의 '경제전망 요약표'에서 연간 전망값과
**전월(직전 전망) 대비 수정폭**을 추출해, 전망 수정 방향을 추적한다.
KCIF 인사이트와 같은 성격의 신호를 정부 공식 전망으로 교차검증하기 위한 것.

작성일: 2026-07-26

---

## 1. 목적

경제전망 요약표에는 각 지표의 연간 전망값과 함께 **직전 전망 대비 수정폭**이
대괄호로 표기된다(예: `GDP 성장률 2.6 [+0.6]` = 전월 전망 대비 0.6 상향).

이 수정폭이 핵심 신호다. 전망의 적중 여부는 이듬해에나 알 수 있어 실시간
판단에 못 쓰지만, **전망을 매 호 어느 방향으로 고치는지**는 지금 계산할 수
있다. LLM을 쓰지 않는다 — 이미 숫자로 구조화된 표를 계산할 뿐이라 오차가 없다.

KCIF 인사이트(월간)와 달리 경제전망은 분기 발간(연 4회, 2·5·8·11월 중)이라
성기지만, **정부 공식 전망**이라 무게가 다르다. 둘의 수정 방향이 일치하면
신호가 강해진다.

---

## 2. 수집

### 2-1. 다운로드

`download_reports.py`가 한국은행 보도자료 검색에서 경제전망 PDF를 받는다.

```bash
uv run python mytrading/download_reports.py 경제전망
```

- 검색: menuNo 201263, 게시판 B0000502, searchKwd=경제전망
- 첨부 PDF(fileSrc 방식)를 받아 `reports/bok/eor/경제전망_YYYY-MM.pdf`로 저장
- 최신 호만 받는다(`.downloaded` 마커로 중복 방지)

### 2-2. 파일명 규칙

`경제전망_YYYY-MM.pdf` (발표 연월). 수동으로 받은 옛 파일은
`rename_eor.py`로 이 형식에 맞춰 통일했다.

```bash
uv run python mytrading/rename_eor.py           # 미리보기
uv run python mytrading/rename_eor.py --apply    # 적용
```

### 2-3. 간이판 주의

한국은행은 분기마다 정식 전망과 별개로 **간이판(Indigo Book)**을 내기도 한다.
간이판은 1쪽짜리 텍스트 브리핑으로 요약표가 없다. 파서가 자동으로 넘긴다
(요약표 없음 처리). 정식 전망 PDF를 받아야 파싱된다.

---

## 3. 파싱

```bash
uv run python mytrading/bok_outlook_parser.py                  # 전체 수집
uv run python mytrading/bok_outlook_parser.py --dry-run         # 저장 없이 확인
uv run python mytrading/bok_outlook_parser.py --file 2026-05    # 한 호만
uv run python mytrading/bok_outlook_parser.py --file 2026-05 --debug
uv run python mytrading/bok_outlook_parser.py --show            # 저장 결과 요약
```

`bok/eor/*.pdf`를 전부 읽어
`reports/investment_checklist/bok_economic_outlook_history.yaml`에 저장한다.

---

## 4. 데이터 구조

각 호는 `forecast_years`(전망연도), `items`(23개 지표), `source_file`을 담는다.
지표별로 연간 전망값(`annual`)과 수정폭(`revision`)이 있다.

```yaml
history:
  2026-05:
    source_file: 경제전망_2026-05.pdf
    forecast_years: [2026, 2027]
    items:
      gdp_growth:
        annual:   {"2026": 2.6, "2027": 2.1}
        revision: {"2026": 0.6, "2027": 0.3}   # 전월 대비 수정폭
        section: domestic
      world_growth:
        annual:   {"2026": 2.9, "2027": 3.1}
        revision: {"2026": -0.2, "2027": -0.1}
        section: global
```

반기값은 저장하지 않는다(호별 열 구조가 유동적이라). 연간값+수정폭이 신호다.

### 23개 지표

**global(전제)**: world_growth(세계경제 성장률), us_growth(미국), euro_growth(유로),
china_growth(중국), japan_growth(일본), world_trade(세계교역 신장률), brent_oil(브렌트유가)

**domestic(국내)**: gdp_growth, private_consumption(민간소비),
construction_invest(건설투자), facility_invest(설비투자), ip_invest(지식재산투자),
goods_export(재화수출), goods_import(재화수입), cpi(소비자물가), core_cpi(근원물가),
current_account(경상수지), goods_balance(상품수지), service_balance(서비스수지),
primary_income_balance(본원·이전소득수지), employment_change(취업자수 증감),
unemployment(실업률), employment_rate(고용률)

---

## 5. 커버리지

요약표가 있는 **12개 호(2023-05 ~ 2026-05) 전부 수집**.

| 구분 | 호 | 내용 |
|---|---|---|
| 수정폭 있음 | 2024-08 ~ 2026-05 (8개) | 연간 전망값 + 전월대비 수정폭 |
| 수정폭 없음 | 2023-05, 08, 11, 2024-02 (4개) | 연간 전망값만 (한은이 수정폭 표기 전) |

한국은행은 **2024년 중반부터 요약표에 수정폭을 표기**하기 시작했다.
그 이전 호는 값(레벨)만 있고 `revision`이 비어 있다.

`--show`로 첫 전망연도의 GDP·물가 전망과 수정폭을 한눈에 본다.

---

## 6. 유지보수 주의 (호별 변형)

옛 호와 특수 호마다 표 형식이 달라, 파서에 다음 대응이 들어 있다.
파서를 손볼 때 알아둘 것.

- **열 구조 변형** — 호마다 연간 컬럼 수가 1~3개로 다르다(다음연도 열이
  붙기도 함). 값을 고정 위치로 세지 않고, **수정폭 대괄호 위치로 연간값을
  특정**한다. 열이 몇 개든 안 밀린다.
- **전망연도 판별** — 헤더의 `e)` 표기가 호마다 1~2개로 달라 `e)` 개수로는
  못 가린다. **파일명 연도를 첫 전망연도**로 삼고 그 이상만 취한다.
- **항목명 띄어쓰기** — `세계경제성장률` vs `세계경제 성장률`. 공백을 무시하고
  매칭한다.
- **항목명이 이미지인 호(2025-11)** — 요약표 항목명이 이미지로 박혀 값 줄에
  라벨이 없다(`3.3 3.4 2.7 3.0 [+0.2] ...`). 항목 매칭이 과반 미만이면
  **값 줄 순서를 ITEMS 순서와 1:1 대응**시켜 파싱한다(값 줄 23개 확인).
- **수정폭 없는 옛 호(2023~2024-02)** — 대괄호가 없어 값만 나열된다.
  헤더의 **`연간` 토큰 위치로 연간값을 뽑고** revision은 비운다.
- **서술 페이지 오인(2023-08 등)** — 본문 '요약' 서술 페이지에도 항목명이
  언급돼 요약표로 오인될 수 있다. `_find_page`는 **항목명 뒤에 숫자값이
  붙은 페이지**를 요약표로 인식해 서술 페이지를 거른다.
- **글자 3중 중복(옛 호)** — 제목이 `경경경제제제`처럼 추출되나, 값·헤더는
  정상이라 파싱에 지장 없다.

### 진단은 pdfplumber로

KCIF와 마찬가지로 텍스트 추출은 pdfplumber로 진단한다. 페이지가 안 잡히면
`extract_text()`로 실제 텍스트를 직접 확인해 원인을 짚는다(항목명 이미지화,
서술 페이지 오인 등은 이 방법으로만 판별된다).

---

## 7. 관련

- 수집 데이터: `reports/investment_checklist/bok_economic_outlook_history.yaml`
- 파서: `mytrading/bok_outlook_parser.py`
- 다운로드: `mytrading/download_reports.py`, `mytrading/rename_eor.py`
- 같은 성격의 월간 신호: KCIF 인사이트 → `KCIF_REPORTS.md`
- 폴더 전체 안내: `INVESTMENT_CHECKLIST.md`