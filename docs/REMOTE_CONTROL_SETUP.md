# Claude Code Remote Control — 폰 원격 연결

미니PC(헤드리스 Ubuntu)에서 Claude Code 세션을 띄우고, 폰에서 원격으로
이어받아 트레이딩 환경(파일·bash·git)을 조작하는 셋업.

## 구조

```
[미니PC Ubuntu]  ← Claude Code 세션 (tmux로 유지), 실제 실행은 여기서
      ▲
      │ Remote Control (claude.ai 경유, 아웃바운드 HTTPS만)
      ▼
[폰 Claude 앱]   ← Code 탭에서 세션 접속, 원격 조작
```

- 세션은 **미니PC에서 계속 실행**. 파일·MCP는 미니PC를 안 떠남.
- 폰은 원격 제어(채팅·도구결과만 오감). 인바운드 포트 안 열림 → 방화벽 설정 불필요.
- SSH 끊겨도(윈도우 꺼도) tmux 세션이 미니PC에서 유지됨.

## 전제

- Claude Code v2.1.52 이상 (Remote Control 지원). 현재 설치: v2.1.220
- Claude 구독 (Pro/Max/Team/Enterprise). API-only 계정은 불가.
- tmux (세션 유지용)
- 폰에 Claude 앱 설치 + 같은 계정 로그인

## 셋업 순서

### 1. tmux 설치 (최초 1회)
```bash
sudo apt install -y tmux
tmux -V   # 확인
```

### 2. tmux 세션에서 Claude Code 실행
```bash
tmux new -s trading                    # 세션 생성 (하단 초록 바 = tmux 안)
cd ~/workspace/open-trading-api
claude
```
- 최초 실행 시: 테마 선택 → 로그인(구독 계정) → 폴더 신뢰
- 헤드리스라 로그인은 브라우저가 안 열리고 **URL 출력** → 윈도우/폰 브라우저에서 열어 로그인 (인증은 미니PC에 저장됨)

### 3. Remote Control 활성화
Claude Code 프롬프트에서:
```
/rc
```
(또는 `/remote-control`) → "Enable Remote Control" 선택.
세션 URL이 나옴 (예: claude.ai/code/session_xxx).

### 4. 폰 연결
- 폰 Claude 앱 → **Code 탭** → 세션 목록에서 선택
- 또는 브라우저에서 claude.ai/code 접속 (같은 계정)
- QR 대신 이 방식 사용 (헤드리스는 QR 렌더링 안 됨 — 정상)

## 재접속 (SSH 다시 붙을 때)

미니PC에 SSH로 다시 접속하면, 살아있는 tmux 세션에 재부착:
```bash
tmux attach -t trading
```
- 세션 목록 보기: `tmux ls`
- tmux에서 나오기(세션은 유지): `Ctrl+B` 누르고 `D`

## 사용 예 (폰에서)

- "git log 최근 5개 보여줘"
- "common.py 의 load_config 함수 보여줘"
- "value_range 백테스트 돌려줘"
- 파일 수정·생성·이동, bash 실행, git 작업 모두 가능

## 주의

- Claude의 파일 수정·명령 실행은 **승인**을 물을 수 있음 (폰에서 확인).
- 위험 작업(삭제 등)은 승인 게이트로 실수 방지.
- Remote Control 끄기: `/remote-control` 재실행.
- 세션 만료: 장시간 방치 시 원격 제어 연결이 끊길 수 있음.
  긴 무인 작업은 tmux 안에서 계속 돌고, 폰은 확인·조작용으로.

## 보안

- 로컬(미니PC)은 아웃바운드 HTTPS만, 인바운드 포트 없음.
- 파일·MCP는 미니PC를 안 떠남. 채팅·도구결과만 TLS로 오감.
- 프롬프트 인젝션 주의: 신뢰하는 코드에서만 사용.
