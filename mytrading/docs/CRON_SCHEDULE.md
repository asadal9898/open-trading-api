# 크론 스케줄 (자동 실행 작업)

> cron으로 자동 실행되는 데이터 수집·알림 작업 정리.
> KIS API 기반. 실행 로그는 각 작업의 로그 파일 참조.

## 전체 스케줄 표

| 시각 | 요일 | 작업 | 하는 일 | 결과물 |
|------|------|------|---------|--------|
| 매시간 (0분) | 매일 | `weekly_report.py --scheduled` | 매시간 깨어나 사용자별 발송 조건 확인 | 조건 맞으면 주간 리포트 텔레그램 발송 |
| 07:00 | 평일(월~금) | `daily_update.py` | 종목 일봉 + 거시 지수 증분 갱신 | CSV 캐시 최신화 |
| 09:00 | 주말(토·일) | `index_data.py` | 지수·환율·금리·유가 등 17개 거시지표 수집 | 거시 데이터 CSV (국면 판단용) |
| 11:00 | 일요일 | `newsletter_check.py` | 뉴스레터 만료·KCIF 미수신·한투 점검 감시 | 감지 시 텔레그램 알림 |
| 14:00 | 주말(토·일) | `scan_dividend.py --market kospi` | 코스피 전체 배당주 스캔 | universe_ko.yaml에 후보 추가 + 알림 |
| 15:00 | 주말(토·일) | `scan_dividend.py --market kosdaq` | 코스닥 전체 배당주 스캔 | universe_ko.yaml에 후보 추가 + 알림 |
| 05:00 | 매월 1일 | `dividend_calendar.py --update-universe` | 배당락일 캘린더 연간 갱신 | dividend_calendar.yaml |
| 07:30 | 평일(월~금) | `rsi_alert.py --notify` | 코스피 RSI(14) 과매도(&lt;30) 감시 (자동매매 아님, 참고용) | 감지 시 텔레그램 알림 |
| 07:30 | 매일 | `rclone copy` (저장소 백업) | 저장소 전체를 Google Drive로 백업 | `gdrive:open-trading-api` |
| 14:00 | 평일(월~금) | `free_loss_alert.py --threshold -10 --notify` | 자유종목(free_holdings) 중 -10% 이하 손실 감시 | 감지 시 텔레그램 알림 |
| 18:00 | 평일(월~금) | `fetch_credit_balance.py` | KOFIA 신용공여잔고 추이 수집 | `market_credit_balance.csv` |
| 19:00 | 매일 | `holdings_news.py --keep-days 100` | 자유종목 관련 구글뉴스 헤드라인 수집 (참고용) | `mytrading/reports/news/` |
| 19:30 | 평일(월~금) | `score_dividend.py --save --observe` | moderate(배당) 300점 재채점 → auto_confirm 갱신 + 관찰로그 | universe_ko.yaml auto_confirm 갱신, `~/dividend_score_log/YYYY-MM-DD.json` |

> **이름 주의** — `scan_dividend.py`(주말, 새 후보 발굴, 기존 종목 스킵)와
> `score_dividend.py`(평일 19:30, 기존 종목 전체 재채점)는 이름이 비슷하지만 별개다.
> 대상 집합이 정반대(전자는 신규만, 후자는 기존 전체).

## 작업별 상세

**weekly_report.py** — 주간 알림
매시간 실행되지만, 실제 발송은 각 사용자의 `notify_day`/`notify_time`(기본 토 15:00)과 현재 시각이 맞을 때만. 다음 주 휴장 일정 + 계좌 현황을 텔레그램으로 보냄. 로그: `~/KIS/cache/weekly.log`

**daily_update.py** — 일간 데이터 갱신 (평일 07:00)
universe 종목 전체의 일봉 + 거시 지수(코스피·S&P500·나스닥)를 증분 갱신. 전일까지 데이터를 최신 유지. 한·미 둘 다 휴장이면 조용히 종료. 증분이라 가벼움. 로그: `~/KIS/cache/daily_update.log`

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

**rsi_alert.py** — 코스피 RSI 과매도 알림 (평일 07:30)
RSI(14) < 임계값(기본 30)이면 텔레그램 알림. 백테스트상 RSI<25 → 5일 반등 승률 74.6%지만
하락 초입엔 손실 위험도 있는 칼날 신호 — **자동매매 아님, 관찰·참고용**. 로그: `~/KIS/cache/rsi_alert.log`

**rclone 백업** — 저장소 전체 백업 (매일 07:30)
`rclone copy . gdrive:open-trading-api`로 저장소 전체를 Google Drive에 백업(`~/rclone-filter.txt` 필터 적용). 로그: `/tmp/rclone_backup.log`

**free_loss_alert.py** — 자유종목 손실 알림 (평일 14:00)
자유종목(free_holdings) 보유분 중 손실률이 임계값(기본 -10%) 이하면 텔레그램 알림. value_range
종목과 달리 자유종목엔 자동 손절 규칙이 없어 알림만 — **자동매도 아님**. 로그: `~/KIS/cache/free_loss_alert.log`

**fetch_credit_balance.py** — 신용공여잔고 수집 (평일 18:00)
금융투자협회(KOFIA FreeSIS)에서 코스피/코스닥 신용거래융자 잔고 추이를 수집해
`market_credit_balance.csv`를 갱신. 국면 판단 참고 데이터. 로그: `~/KIS/cache/credit_balance.log`

**holdings_news.py** — 자유종목 뉴스 브리핑 (매일 19:00)
자유종목별 최근 구글뉴스 헤드라인을 모아 저장(100일 보관). **참고용 — 매매신호 아님**.
출력: `mytrading/reports/news/`. 로그: `~/KIS/cache/holdings_news.log`

**score_dividend.py** — moderate 배당종목 자동 재채점 (평일 19:30)
시가총액·배당률(1.5배 가중)·부채비율 순위 기반 300점 스코어링 → 상위100
`auto_confirm:Approval` / 101등 이하 `Paused` / 배당0% `Rejected`로 매일 갱신. 사람이 정한
`confirm` 필드는 절대 안 건드림(합성 게이트에서 confirm이 이김 — `DIVIDEND_FILTER_DESIGN.md`
§5c 참고). `--save --observe`를 같이 줘서 계산 1번으로 저장 + 관찰로그
(`~/dividend_score_log/`, hysteresis 검토용 이력)를 함께 남긴다.
로그: `~/KIS/cache/score_dividend_observe.log`

**(cron 미등록) moderate_buy_alert.py / moderate_order_runner.py** — B(매수후보 텔레그램
알림)와 D(자동발주 게이트 + dry-run/`--live`, D-1/D-2)는 아직 cron에 등록돼 있지 않다.
D는 코드 자체는 D-2까지 구현됐으나(게이트+dry-run/`--live` 스켈레톤) **모의계좌에서 실제
발주에 성공해 본 적이 아직 없다**(D-3 미완). cron 등록은 D-3(모의 실발주 검증)·D-4(cron
자동화) **완료 후**에나 붙일 예정이고, 지금은 사람이 수동 실행해야 한다. 자세한 건
`VALUE_RANGE.md` §0 참고.

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