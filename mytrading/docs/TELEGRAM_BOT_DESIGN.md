# 텔레그램 봇 사용 가이드 · 설계

작성: 2026-07-01
목적: 텔레그램으로 자유투자 종목 추가·승인, 시스템 관리

봇 설치·서비스 운영은 `docs/BOT_SERVICE_GUIDE.md` 참고.

## 1. 개념

자유투자(free_holdings) 종목을 텔레그램으로 관리한다.

- 종목 추가/승인/제거를 명령으로
- 각자 자기 계좌만 (사용자별 격리)
- 매매는 단계적 승인 (추가 != 매수)

역할 분담: 명령 파싱 = 간단 코드 (안전·즉시) / 종목 분석·자유입력 = AI

## 2. 화이트리스트 (사용자 인증)

kis_devlp.yaml 의 users 에 telegram_chat_id 로 연결.
필드: name, role(owner=모든권한 / trader=거래만), telegram_chat_id(챗 ID)

- chat_id 로 발신자 확인 -> 등록 안 된 ID는 거부
- role: owner=모든 권한(reboot 등) / trader=거래만
- chat_id -> 사용자 매핑 -> 그 사용자의 free_holdings 만 접근

## 3. 명령

### /add {종목코드} — 종목 추가

흐름:

1. 화이트리스트 확인 (chat_id -> 사용자)
2. KIS 조회: 종목명·섹터·표준산업분류 (search_stock_info)
3. 재무 분석 (get_financial_summary):
   - 부채비율 (업종별 _debt_note)
   - 영업이익·매출 증가율
   - ROE, 시가총액
4. 자유투자 자금 표시:
   총액: X만원 (계좌평가 x free비중)
   현재 투자: Y만원
   여유: (X-Y)만원
5. [예][아니요] 버튼
   예 -> free_holdings[사용자][계좌]에 추가 (confirm: Waiting)
         안내: "매수는 /approve 로 비중·속도 지정 필요"
   아니요 -> 취소

- 추가는 항상 Waiting (관찰만, 매수 안 함)
- "자유투자니까 자유를" - 분석 주고 사람이 판단

### /approve {종목코드} — 매수 승인

흐름:

1. 매수 방식 입력 (수량 중심, %가 아님):
   · 일시 10주
   · 분할 주1회 1주
   · 분할 매일 1주
   · 일시 5주 + 분할 주1회 1주  (동시)
2. AI 파싱 -> 재확인:
   "일시매수 5주 + 매주 1주씩, 맞나요? [예][다시]"
3. 수량 = 금액 표시:
   "5주 = 약 Z만원, 매수 후 여유 (X-Y-Z)만원"
   (한도 초과 시 경고)
4. free_holdings 업데이트:
   confirm: Approval
   buy_plan: onetime=5, split(every=weekly, qty=1)

- 매수는 수량으로 (사람이 실제 쓰는 언어, %는 안 씀)
- 비중(%)은 뒤에서 한도 체크용만

### /reboot (`/재부팅`) — 미니PC 재부팅 (owner만)

봇에서 미니PC를 재부팅하는 명령.

- **owner 역할만** 실행 가능 (trader 거부)
- **매매시간(개장일 09:00~15:30)**: `reboot_password` 입력을 요구 (장중 실수 재부팅 방지)
- **장외 시간**: `[재부팅][취소]` 확인 버튼
- 실제 실행: `sudo /usr/sbin/reboot` (sudoers NOPASSWD 로 비번 없이)

재부팅 후 봇은 systemd `enable` 설정으로 자동 복귀한다.

### /상태 (`/status`) — 계좌·모드 현황

- 현재 투자 모드 표시 ("모의 투자" / "실전 투자")
- 계좌 현황

### 나중에 추가

- /list - 내 자유 종목 목록
- /remove {코드} - 종목 제거

## 4. 안전장치 (핵심)

1. 화이트리스트: 등록된 chat_id 만 (토큰 노출돼도 ID 거부)
2. 사용자 격리: 각자 자기 free_holdings 만 (Owner는 Owner만)
3. 단계적 승인: 추가(Waiting) -> 매수승인(Approval) -> 실제매매
   한 번에 매매 안 됨, 세 번 걸러짐
4. reboot 보호: owner만 + 매매시간 비번

## 5. 저장 (free_holdings)

봇이 yaml 에 쓸 때: yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

- allow_unicode: 한글 안 깨짐
- sort_keys=False: 필드 순서 유지

종목 형태 (allocations.yaml free_holdings 참고):

- Waiting: buy_plan 없음
- Approval: buy_plan 추가 (onetime, split(every, qty))
- sector·industry 필수 (업종전망 -> 종목 주의알림용)

## 6. buy_plan 실행 (시스템)

- onetime=N -> 다음 매매일 즉시 N주
- split(every=daily/weekly, qty=N) -> 주기마다 N주
- free 한도 초과 시 알림

## 7. 구현 순서 (단계적)

1. 봇 기본 (메시지 수신 + 화이트리스트)
2. /add (KIS조회 + 재무분석 + 금액표시 + 버튼 + 추가)
3. /approve (매수계획 입력 + AI파싱 + buy_plan 저장)
4. /reboot (owner + 매매시간 비번)
5. /list, /remove 등

## 8. 구현 시 메모

- _fh_for (portfolio.py 로더)에 buy_plan 필드 추가 필요
- 시가총액: KIS inquire_price 에서 가져오기
- reboot 비번: 환경변수 (평문 저장 금지)
- AI 파싱(매수계획)은 반드시 재확인 (매매 관련 오해 방지)