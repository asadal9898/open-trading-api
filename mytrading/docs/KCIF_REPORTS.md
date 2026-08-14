# KCIF 국제금융센터 리포트 파이프라인

국제금융센터(KCIF) 리포트에서 경제 전망을 추출해 **전망 수정 방향**을 추적하고,
이를 리스크 관리(현금 하한 조절 등)에 활용하기 위한 데이터 수집·파싱·분석 문서.

작성일: 2026-07-25

---

## 1. 목적

리포트의 전망이 **매월 어느 방향으로 수정되는지**를 추적한다.

전망의 적중 여부(2026년 성장률 전망이 맞았는지)는 이듬해에나 알 수 있어
실시간 판단에 못 쓴다. 대신 **같은 대상에 대한 전망이 매월 어떻게 바뀌는지**는
지금 계산할 수 있고, 기관이 현실을 뒤늦게 반영하므로 실제 지표보다 먼저
움직이는 경향이 있다(단, 선행성은 아직 검증 중 — 5절 참조).

LLM 을 쓰지 않는다. 이미 숫자로 구조화된 표를 계산할 뿐이라 오탐이 없다.

---

## 2. 수집 절차

### 2-1. PDF 확보 (수동)

KCIF 리포트는 **자동 다운로드가 불가능**하다. 다음 순서로 수동 확보한다.

1. KCIF 사이트 또는 이메일 뉴스레터에서 PDF를 직접 내려받는다.
2. 받은 PDF를 **텔레그램 봇에게 파일로 전송**한다.
3. 봇(`telegram_bot.py`)이 파일명을 보고 폴더를 자동 분류해 저장한다
   (`_classify_document` → `download_telegram_file`).

파일명 기반 분류 규칙 (`_classify_document`):

| 파일명 키워드 | 저장 폴더 | 라벨 |
|---|---|---|
| "리스크" + "워치" | `reports/kcif/risk_watch` | KCIF 리스크워치 |
| "국제금융" / "insight" / "인사이트" | `reports/kcif/insight` | KCIF INSIGHT |
| "금융안정" | `reports/금융안정보고서` | 금융안정보고서 |
| "통화신용정책" / "통화신용" | `reports/통화신용정책보고서` | 통화신용정책보고서 |
| "경제전망" | `reports/경제전망보고서` | 경제전망보고서 |
| 그 외 | 저장하지 않음(`None`) | — |

> 원자재 리포트는 KIS API로 별도 수집하므로 이 경로로 저장하지 않는다.

### 2-2. 미수신 감시 (자동)

매주 일요일 11:00 크론으로 `newsletter_check.py`가 실행된다
(`docs/CRON_SCHEDULE.md`).

Gmail 기반 3종 감시 중 하나가 **KCIF 리스크워치 미수신 감지**다.
매월 마지막 주에 리스크워치가 아직 안 왔으면 텔레그램으로 알림을 보낸다.
설정은 `mytrading_config.yaml`, 로그는 `~/KIS/cache/newsletter.log`.

> 즉 "받아야 할 리포트가 안 왔다"는 알림은 자동, 실제 PDF 확보와 전송은 수동이다.

---

## 3. 파서 실행

PDF를 폴더에 넣은 뒤 파서를 **수동 실행**한다. (크론 미등록)

```bash
cd ~/workspace/open-trading-api

# ① 인사이트 리포트 뒤쪽 주요지표 표들 (세계/아시아 전망, 주요지표, 실물경기, IB금리)
uv run python -m mytrading.reports.kcif_insight_parser

# ② 인사이트 앞쪽 동향&전망의 그림2 성장률 전망치 (값 + 전월대비 수정폭)
uv run python mytrading/reports/kcif_growth.py

# ③ 인사이트 앞쪽 IB 정책금리 전망표 (미국/유로존/일본)
uv run python mytrading/reports/kcif_ib_rates.py

# ④ 리스크워치 리포트
uv run python -m mytrading.reports.kcif_risk_parser
```

각 파서는 `reports/investment_checklist/*.yaml`에 결과를 누적 저장한다
(기존 히스토리에 추가/갱신).

---

## 4. 생성되는 데이터

모두 `mytrading/reports/investment_checklist/` 아래.
커버리지는 2026-07 기준(PDF 56개, 2021-12 ~ 2026-07).

| 파일 | 커버리지 | 내용 |
|---|---|---|
| `kcif_growth_forecast_history.yaml` | 56개월 | 그림2 성장률 전망 **값 + 전월대비 수정폭** |
| `kcif_insight_world_economic_history.yaml` | 53개월 | 세계 주요국 성장률 전망 시계열 |
| `kcif_insight_asia_economic_history.yaml` | 56개월 | 아시아 10개국 성장률·물가·경상수지 전망 |
| `kcif_risk_watch_history.yaml` | 53개월 | 월별 리스크 순위 + 발생확률·영향력 |
| `kcif_insight_real_economy_history.yaml` | 42개월 | 실물경기 지표(경제성장률·PMI·소비자물가 등) |
| `kcif_insight_key_indicators_history.yaml` | 38개월 | 금융시장 지표(KOSPI·환율·금리 등) |
| `kcif_insight_ib_us_rates_history.yaml` | 11개월 | (구버전) IB 미 정책금리 — `kcif_ib_rates.py`로 대체 예정 |

### 주요 파일 구조

**kcif_growth_forecast_history.yaml** — 수정폭이 원본에 있는 유일한 표
```yaml
history:
  2026-05:
    source_file: 국제금융+인사이트+`26.5월호.pdf
    page: 6
    countries:
      미국:
        "2026-Q2": {value: 2.1, revision: -0.2}   # revision = 전월 대비 수정폭
        "2026":    {value: 2.2, revision: -0.3}
        "2027":    {value: 2.0, revision: 0.0}
      유로존: { ... }
```

**kcif_insight_world_economic_history.yaml**
```yaml
history:
  2026-06:
    timepoints: ["'26.2Q", "'26.3Q", "'26.4Q", "'27.1Q", "2026f", "2027f"]
    countries:
      미국: {values: [2.5, 1.9, 1.9, 2.0, 2.2, 2.0]}
```

**kcif_risk_watch_history.yaml**
```yaml
history:
  2026-06:
    risks:
      - {rank: 1, name: 중동전쟁 장기화, probability: 3, impact: 3}
```

---

## 5. 분석

```bash
uv run python mytrading/reports/report_trend.py            # 연도별 대조
uv run python mytrading/reports/report_trend.py --monthly  # 월별 상세
uv run python mytrading/reports/kcif_growth.py --show       # 월별 수정폭 합계
```

`report_trend.py`는 리스크 총점, 전망 수정 방향을 전략 연간 수익률과 나란히
놓고 "전망 하향이 나쁜 해를 구분하는가"를 본다.

### 현재까지의 결과 (2022~2026)

원본 수정폭 합계(`kcif_growth --show`) 연도별:

| 연도 | 전망 수정합 | 전략 수익 | 판정 |
|---|---|---|---|
| 2022 | 크게 음수(-49.8) | -17.6% | 하향과 손실 일치 |
| 2023 | +5.9(하반기만) | +8.7% | 정상 |
| 2024 | +7.1 | -6.6% | 무경고 |
| 2025 | +0.4 | +23.8% | 정상 |
| 2026 | +1.7 | +15.2% | 정상 |

- **오경보 0건.** "기관 전망은 늘 하향된다"는 우려는 사실이 아니었다.
  좋은 해(2023·2025)에는 실제로 상향이었고, 경고가 뜬 유일한 해가 무너진 해다.
- 다만 **신뢰할 사건이 2022년 하나뿐**이다. 이것 하나로 규칙을 만들면 과최적화가 된다.
- 2022년의 특징은 **크기가 아니라 지속성**이다. 1~10월 열 달 연속 음수였다.
  반면 2025-05는 단월 -7.3(2022년 어느 달보다 큼)이었으나 6월에 +4.3으로 즉시
  반전했고 그 해는 +23.8%였다. 규칙을 만든다면 단월 임계치가 아니라
  "N개월 연속"이어야 한다.
- **리스크 총점은 구분력이 없다.** 가장 높았던 2023년이 좋은 해였다. 폐기 대상.

### 다음 단계

`cash_floor`(현금 하한) 연결은 **아직 하지 않는다.** 사건이 1건이라 우연과
구분되지 않는다. **2020년 코로나 급락 구간** 리포트를 확보해 성격이 다른
두 번째 사건을 얻은 뒤에 판단한다.

---

## 6. 유지보수 주의 (pdfplumber 함정)

파서를 손볼 때 반드시 알아야 할 것들. 자세한 내용은 메모리 참조.

- **진단은 반드시 `pdfplumber`로.** 파서가 pdfplumber를 쓰는데 `pypdf`로
  진단하면 추출 결과가 달라 원인을 잘못 짚는다.
- **2단 레이아웃을 한 줄로 합친다.** 값 행 앞뒤에 옆 단 텍스트가 붙는다
  (`그림9 중국 생산… 미국 5.8 3.3 …`). `startswith`·`match`처럼 줄 시작을
  전제하는 코드는 실패한다 — `search`로 바꾸고 국가 x 범위로 걸러야 한다.
- **`find_tables()`는 이 리포트에 잘 안 맞는다.** 표가 격자선이 아니라 행 음영으로
  구분돼 조각난다. 좌표 기반 텍스트 읽기(`kcif_ib_rates.py`, `kcif_growth.py`
  방식)가 안정적이다.
- **시점 라벨을 하드코딩하지 말 것.** 옛 호 값에 최신 라벨이 붙어 조용히 틀린
  데이터가 된다. PDF에서 추출하고, 못 뽑으면 저장하지 말고 건너뛴다.
- **그림2 국가 배정은 x 최근접이 아니라 구조 규칙으로.** "연간 라벨 뒤에 분기
  라벨이 오면 새 국가"다. 열 폭이 균일하지 않아 최근접은 어긋난다.

### 알려진 미완 항목

- `real_economy`(42개월), `key_indicators`(38개월): 옛 호 표 페이지 탐색이
  키워드 첫 페이지를 잡아 본문 그래프와 혼동. 탐색 로직 재작성 필요.
  (key_indicators는 `index_data.py`와 중복이라 우선순위 낮음)
- `ib_us_rates`(11개월, 구버전): `kcif_ib_rates.py`로 대체 예정.
  신버전은 미국/유로존/일본 분리 수집하나 파일별 quirk가 남아 있음.
- 그림2 수정폭 결측: 2023-01~06(원본은 있으나 미추출), 2022-01·2025-12 일부.