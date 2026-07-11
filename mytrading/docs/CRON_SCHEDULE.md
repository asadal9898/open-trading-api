전체 스케줄 표
시각요일작업하는 일결과물매시간 (0분)매일weekly_report.py --scheduled매시간 깨어나 사용자별 발송 조건 확인조건 맞으면 주간 리포트 텔레그램 발송07:00평일(월~금)daily_update.py종목 일봉 + 거시 지수 증분 갱신CSV 캐시 최신화09:00주말(토·일)index_data.py해외/국내 지수·환율·유가 일봉 수집거시 데이터 CSV (국면 판단용)11:00일요일newsletter_check.py뉴스레터 만료·KCIF 미수신·한투 점검 감시감지 시 텔레그램 알림14:00주말(토·일)scan_dividend.py --market kospi코스피 전체 배당주 스캔universe.yaml에 후보 추가 + 알림15:00주말(토·일)scan_dividend.py --market kosdaq코스닥 전체 배당주 스캔universe.yaml에 후보 추가 + 알림05:00매월 1일dividend_calendar.py --update-universe배당락일 캘린더 연간 갱신dividend_calendar.yaml
작업별 상세
weekly_report.py — 주간 알림
매시간 실행되지만, 실제 발송은 각 사용자의 notify_day/notify_time(기본 토 15:00)과 현재 시각이 맞을 때만. 다음 주 휴장 일정 + 계좌 현황을 텔레그램으로 보냄. 로그: ~/KIS/cache/weekly.log
daily_update.py — 일간 데이터 갱신 (평일 07:00)
universe 종목 전체의 일봉 + 거시 지수(코스피·S&P500·나스닥)를 증분 갱신. 전일까지 데이터를 최신 유지. 한·미 둘 다 휴장이면 조용히 종료. 증분이라 가벼움. 로그: ~/KIS/cache/daily_update.log
index_data.py — 거시 데이터 수집 (주말 09:00)
시장 국면 판단(market_regime) 참고용. KIS API 하나로 여러 종류 수집:

지수: 코스피, S&P500, 나스닥종합, 나스닥100 (다우는 가격가중이라 제외)
환율: 달러/원 (엔·위안·유로도 코드상 가능)
유가·원자재: WTI원유, 금, 옥수수
로그: /tmp/index_update.log

newsletter_check.py — 감시 통합 (일요일 11:00)
Gmail 기반 3종 감시: ①뉴스레터 만료 키워드 ②KCIF 리스크워치 미수신(마지막 주) ③한투 점검공지 OCR. 감지 시 텔레그램 알림. 설정: mytrading_config.yaml. 로그: ~/KIS/cache/newsletter.log
scan_dividend.py — 배당주 스캔 (주말 14·15시)
코스피(14시)·코스닥(15시) 시장 전체에서 배당주 후보 발견. 필터 통과 종목을 universe.yaml에 confirm:Waiting으로 추가하고 텔레그램 알림. 로그: /tmp/scan_kospi.log, /tmp/scan_kosdaq.log
dividend_calendar.py — 배당락일 캘린더 (매월 1일 05:00)
종목은 안 변해도 배당락일은 매년 변하므로 별도 관리. dividend_calendar.yaml에 저장.
실행 순서 (주말)
09:00 index_data (거시 데이터)
11:00 newsletter_check (일요일만)
14:00 scan_dividend kospi
15:00 scan_dividend kosdaq
지수 데이터(09시)가 먼저 갱신된 뒤 배당주 스캔(14·15시)이 도는 순서. 배당주 스캔은 30~40분 소요되므로 1시간 간격으로 안 겹침.
크론 편집
crontab -e     # 편집
crontab -l     # 확인
변경 전 백업 권장: crontab -l > /tmp/crontab.bak.$(date +%s)