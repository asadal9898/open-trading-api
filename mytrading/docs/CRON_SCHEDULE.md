# 크론 스케줄 (자동 실행 작업)

> cron으로 자동 실행되는 데이터 수집·알림 작업 정리.
> KIS API 기반. 실행 로그는 각 작업의 로그 파일 참조.

## 전체 스케줄 표

| 시각 | 요일 | 작업 | 하는 일 | 결과물 |
|------|------|------|---------|--------|
| 매시간 (0분) | 매일 | `weekly_report.py --scheduled` | 매시간 깨어나 사용자별 발송 조건 확인 | 조건 맞으면 주간 리포트 텔레그램 발송 |
| 07:00 | 평일(월~금) | `daily_update.py` | 종목 일봉 + 거시 지수 증분 갱신 | CSV 캐시 최신화 |
| 09:00 | 매일 | `newsletter_ai.py` | 경제 뉴스레터 Gmail 라벨 AI 분석 (Groq) | 국면·영향도 점수 JSON (참고용) |
| 09:00 | 주말(토·일) | `index_data.py` | 지수·환율·금리·유가 등 17개 거시지표 수집 | 거시 데이터 CSV (국면 판단용) |
| 11:00 | 일요일 | `newsletter_check.py` | 뉴스레터 만료·KCIF 미수신·한투 점검 감시 | 감지 시 텔레그램 알림 |
| 14:00 | 주말(토·일) | `scan_dividend.py --market kospi` | 코스피 전체 배당주 스캔 | universe_ko.yaml에 후보 추가 + 알림 |
| 15:00 | 주말(토·일) | `scan_dividend.py --market kosdaq` | 코스닥 전체 배당주 스캔 | universe_ko.yaml에 후보 추가 + 알림 |
| 05:00 | 매월 1일 | `dividend_calendar.py --update-universe` | 배당락일 캘린더 연간 갱신 | dividend_calendar.yaml |

> **이름 주의** — `newsletter_check.py`(감시·알림)와 `newsletter_ai.py`(AI 분석)는
> 이름만 비슷할 뿐 역할이 완전히 다르다. 아래 상세 참조.

## 작업별 상세

**weekly_report.py** — 주간 알림
매시간 실행되지만, 실제 발송은 각 사용자의 `notify_day`/`notify_time`(기본 토 15:00)과 현재 시각이 맞을 때만. 다음 주 휴장 일정 + 계좌 현황을 텔레그램으로 보냄. 로그: `~/KIS/cache/weekly.log`

**daily_update.py** — 일간 데이터 갱신 (평일 07:00)
universe 종목 전체의 일봉 + 거시 지수(코스피·S&P500·나스닥)를 증분 갱신. 전일까지 데이터를 최신 유지. 한·미 둘 다 휴장이면 조용히 종료. 증분이라 가벼움. 로그: `~/KIS/cache/daily_update.log`

**newsletter_ai.py** — 경제 뉴스레터 AI 분석 (매일 09:00)

Gmail의 경제 라벨(국제금융·한국은행 등) 메일을 읽어 Groq LLM(기본 llama-3.3-70b)으로 분석. daily/weekly/brief 뉴스레터별 국면(bull/bear/sideways)·영향도 점수·핵심 포인트를 뽑는다. **참고용** — `slice_pct`는 실제 주문 실행에 연결돼 있지 않음. 출력: `mytrading/reports/inbox/newsletter_ai.json`. 로그: `~/KIS/cache/newsletter_ai.log`. 수동 실행: `uv run --with pypdf --with beautifulsoup4 python mytrading/newsletter_ai.py`

**index_data.py** — 거시 데이터 수집 (주말 09:00)
시장 국면 판단(market_regime) 참고용. KIS API로 17개 거시지표를 수집. 인자 없이 실행하면(크론) 전체를 받음.

- 지수(6): 코스피, S&P500, 나스닥종합, 나스닥100, 니케이225, 상해종합 (다우는 가격가중이라 제외)
- 환율(4): 달러/원, 엔/원, 위안/원, 유로
- 금리(4): 미국채 10년·30년, 미국 연방기금금리, 일본채 10년
- 원자재(3): 금(COMEX), WTI원유, 옥수수(CBOT)

로그: `/tmp/index_update.log`

**newsletter_check.py** — 감시 통합 (일요일 11:00)
Gmail 기반 3종 감시: ①뉴스레터 만료 키워드 ②KCIF 리스크워치 미수신(마지막 주) ③한투 점검공지 OCR. 감지 시 텔레그램 알림. 설정: `mytrading_config.yaml`. 로그: `~/KIS/cache/newsletter.log`

**scan_dividend.py** — 배당주 스캔 (주말 14·15시)
코스피(14시)·코스닥(15시) 시장 전체에서 배당주 후보 발견. 필터 통과 종목을 universe_ko.yaml에 `confirm:Waiting`으로 추가하고 텔레그램 알림(종목코드+종목명). 로그: `/tmp/scan_kospi.log`, `/tmp/scan_kosdaq.log`

**dividend_calendar.py** — 배당락일 캘린더 (매월 1일 05:00)
종목은 안 변해도 배당락일은 매년 변하므로 별도 관리. `dividend_calendar.yaml`에 저장.

## 실행 순서 (주말)

```
09:00 index_data (거시 데이터)
11:00 newsletter_check (일요일만)
14:00 scan_dividend kospi
15:00 scan_dividend kosdaq
```

지수 데이터(09시)가 먼저 갱신된 뒤 배당주 스캔(14·15시)이 도는 순서. 배당주 스캔은 30~40분 소요되므로 1시간 간격으로 안 겹침.

## 크론 편집

```
crontab -e     # 편집
crontab -l     # 확인
```

변경 전 백업 권장: `crontab -l > /tmp/crontab.bak.$(date +%s)`