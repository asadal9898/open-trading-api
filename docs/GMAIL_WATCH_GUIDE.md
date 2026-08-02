# Gmail 감시 가이드

Gmail 라벨의 메일을 읽어 뉴스레터 만료·KCIF 리스크워치 미수신·한국투자증권
점검 공지를 감시하고 텔레그램으로 알림하는 기능이다.

## 개요
- `gmail_client.py`: IMAP 읽기 + SMTP 발송 (앱 비밀번호 방식, 표준 라이브러리)
- `newsletter_check.py`: 3가지 감시를 통합 (크론: 매주 일요일 11시)
  1. 뉴스레터 만료 감시 (매주) — 한국은행·국제금융
  2. KCIF 리스크워치 미수신 (마지막 주 일요일) — 월간 리포트 미수신 시 PDF 알림
  3. 한국투자증권 점검 공지 (매주) — 점검 이미지 OCR → 점검 일시 추출
- 설정: `mytrading_config.yaml` (newsletter_watch, kcif_watch, kis_maintenance_watch)

> **참고** — 경제 뉴스레터를 Groq LLM으로 분석하는 `newsletter_ai.py` 는 별개 작업이다.
> 이 문서(감시·알림)와 무관하며, 매일 09:00 크론으로 돈다. 상세는 `CRON_SCHEDULE.md` 참조.

## 사전 준비

### 1. Gmail 앱 비밀번호
1. Gmail 2단계 인증 사용 설정
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호(16자리) 발급
3. `~/KIS/config/kis_devlp.yaml` 에 추가:
```yaml
my_GMAIL_address: "you@gmail.com"
my_GMAIL_app_password: "앱비밀번호16자리"
```
※ 앱 비밀번호는 공백 없이 붙여서 입력. Google 계정 비밀번호 변경 시 재발급 필요.

### 2. Tesseract OCR (점검 이미지 판독용)
```bash
sudo apt install -y tesseract-ocr tesseract-ocr-kor
```
`setup.sh` 실행 시 자동 설치됨.

## 감시 대상 설정 (mytrading_config.yaml)

### newsletter_watch — 뉴스레터 만료 감시
```yaml
newsletter_watch:
  keywords: [중단, 만료, 재신청, 갱신, 종료]  # 제목/본문에 있으면 알림
  targets:
    - label: 경제/한국은행
      name: 한국은행 뉴스레터
    - label: 경제/국제금융
      name: 국제금융센터(KCIF)
```
- 새 뉴스레터 라벨 추가 시 targets 에 항목만 늘리면 됨.
- keywords 는 예제. 실제 중단 메일 문구 확인 후 정밀화 권장.

### kcif_watch — KCIF 리스크워치 미수신
```yaml
kcif_watch:
  label: 경제/국제금융
  subject_keyword: 리스크 워치
  message: "이번 달 KCIF 글로벌 리스크 워치 뉴스레터가 아직 없어요..."
```
- 마지막 주 일요일에만 동작. 이번 달 리스크워치 메일이 없으면 알림.
- 제목에 '리스크 워치' + 이번달 태그(예 '26.7월)가 있으면 수신으로 판단.

### kis_maintenance_watch — 한국투자증권 점검 공지
```yaml
kis_maintenance_watch:
  label: 경제/한국투자증권
  subject_keyword: 중단 안내
  img_host: securities.koreainvestment.com
```
- 점검 안내 메일의 이미지를 OCR 하여 점검 일시 추출 → 텔레그램 알림.
- 이미 알린 공지는 `~/KIS/cache/kis_maint_seen.json` 에 기록해 중복 방지.

## 실행

### 수동 실행
```bash
uv run python mytrading/newsletter_check.py           # 전체 (최근 35일)
uv run python mytrading/newsletter_check.py --days 90 # 검사 기간 조정
```

### 크론 (자동, 매주 일요일 11시)
```
0 11 * * 0 cd ~/workspace/open-trading-api && uv run python mytrading/newsletter_check.py >> ~/KIS/cache/newsletter.log 2>&1
```

## 개별 기능 사용 (gmail_client)
```python
from mytrading.gmail_client import read_label, send_mail, list_labels

list_labels()                          # 전체 라벨 (한글)
mails = read_label("경제/한국은행", limit=10)  # 라벨 메일 읽기
send_mail("제목", "본문")               # 나에게 메일 발송
```

## 자주 겪는 문제

### IMAP 로그인 실패
- 앱 비밀번호가 정확한지 (공백 없이 16자리)
- Gmail 2단계 인증이 켜져 있는지
- Gmail 설정에서 IMAP 사용이 켜져 있는지

### 점검 일시 추출 실패
- Tesseract 한글팩(kor) 설치 확인: `tesseract --list-langs | grep kor`
- OCR 은 이미지 품질에 따라 실패할 수 있음. 실패 시 알림에 "이미지 직접 확인" 안내가 감.

### 한글 라벨이 안 읽힘
- 라벨명을 정확히 입력했는지 (예: '경제/한국은행', 슬래시 포함)
- `list_labels()` 로 정확한 라벨명 확인