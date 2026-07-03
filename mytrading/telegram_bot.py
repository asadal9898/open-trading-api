"""텔레그램 봇 — 명령 수신 (requests 폴링, getUpdates).

notify.py(보내기)와 짝. 이건 받기(명령).
- getUpdates 로 새 메시지 폴링
- 화이트리스트: chat_id -> 사용자 (kis_devlp.yaml users)
- 명령: /add /approve /reboot (단계적 추가)

실행: uv run python -m mytrading.telegram_bot
  (한 번 실행 = 한 번 폴링. cron 이나 루프로 주기 호출)
"""
import sys
import time
import requests

from mytrading import notify
from mytrading.portfolio import load_portfolio

# notify 의 설정/토큰 재사용
_cfg = notify._load_config()
_TOKEN = str(_cfg.get("bot_token", "")).strip()
_API = f"https://api.telegram.org/bot{_TOKEN}"

# 마지막으로 처리한 update_id 저장 (중복 처리 방지)
_OFFSET_FILE = notify.CONFIG_PATH.parent / ".telegram_offset"


def _load_users() -> dict:
    """chat_id -> {key, name, role} 매핑 (화이트리스트)."""
    users = {}
    for key, u in (_cfg.get("users") or {}).items():
        cid = str(u.get("telegram_chat_id", "")).strip()
        if cid:
            users[cid] = {"key": key, "name": u.get("name", key),
                          "role": u.get("role", "trader")}
    return users


def _get_offset() -> int:
    try:
        return int(_OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def _save_offset(v: int):
    try:
        _OFFSET_FILE.write_text(str(v))
    except Exception:
        pass


def get_updates(timeout: int = 10) -> list:
    """새 메시지 가져오기 (getUpdates)."""
    offset = _get_offset()
    url = f"{_API}/getUpdates"
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset + 1
    try:
        r = requests.get(url, params=params, timeout=timeout + 5)
        data = r.json()
    except Exception as e:
        print(f"[bot] getUpdates 오류: {e}")
        return []
    if not data.get("ok"):
        print(f"[bot] getUpdates 실패: {data}")
        return []
    return data.get("result", [])


def handle_command(user: dict, text: str) -> str:
    """명령 처리. user=화이트리스트 정보. 반환: 응답 메시지."""
    parts = text.strip().split()
    cmd = parts[0].lower() if parts else ""
    args = parts[1:]

    if cmd == "/start" or cmd == "/help":
        return ("자유투자 봇\n"
                "/add {종목코드} - 종목 추가\n"
                "/list - 내 종목\n"
                "(구현 예정: /approve /reboot)")
    if cmd == "/add":
        if not args:
            return "사용법: /add 000660"
        return f"[준비중] {args[0]} 추가 기능은 다음 단계에서 구현"
    if cmd == "/list":
        return _cmd_list(user)
    return f"모르는 명령: {cmd}"


def _cmd_list(user: dict) -> str:
    """내 자유 종목 목록."""
    try:
        pf = load_portfolio()
    except Exception as e:
        return f"목록 로드 실패: {e}"
    ukey = user["key"]
    lines = [f"{user['name']} 자유 종목:"]
    found = False
    for acc_name, al in (pf.allocations.get(ukey, {}) or {}).items():
        for sym in al.free_symbols:
            found = True
            conf = sym.get("confirm", "?")
            lines.append(f"  {sym['code']} {sym.get('name','')} [{conf}]")
    if not found:
        lines.append("  (없음)")
    return "\n".join(lines)


def poll_once():
    """한 번 폴링해서 새 명령 처리."""
    if not _TOKEN or _TOKEN.startswith("여기에"):
        print("[bot] bot_token 미설정")
        return
    users = _load_users()
    updates = get_updates()
    for up in updates:
        _save_offset(up["update_id"])
        msg = up.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
        text = msg.get("text", "")
        if not chat_id or not text:
            continue
        # 화이트리스트 확인
        if chat_id not in users:
            print(f"[bot] 미등록 chat_id={chat_id} 거부")
            notify._send_raw(chat_id, "등록되지 않은 사용자입니다.")
            continue
        user = users[chat_id]
        print(f"[bot] {user['name']}({user['role']}): {text}")
        reply = handle_command(user, text)
        notify._send_raw(chat_id, reply)


if __name__ == "__main__":
    poll_once()
