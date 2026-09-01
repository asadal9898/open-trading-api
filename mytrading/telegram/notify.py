"""
텔레그램 알림 모듈
설정: ~/KIS/config/kis_devlp.yaml 에 bot_token, chat_id, enabled 추가
표준 라이브러리(urllib)만 사용 — 추가 패키지 불필요.

기본 사용:
    from mytrading.notify import send_message, send_to, broadcast
    send_message("아무 메시지")           # 전역 chat_id 에게
    send_to("123456", "특정인에게")       # 특정 chat_id 에게
    broadcast("전체 공지")                # 모든 사용자(users)에게

상황별(to=chat_id 면 그 사람에게, 없으면 전역):
    notify_order_filled("005930","삼성전자","BUY",1,354000, to="123456")

상황별 헬퍼:
    from mytrading.notify import notify_order_filled, notify_order_submitted, \
                                 notify_error, notify_balance
    notify_order_filled("005930", "삼성전자", "BUY", 1, 354000)
    notify_error("주문 실패", "모의투자 영업일이 아닙니다")

테스트:
    uv run python mytrading/notify.py            # 테스트 메시지
    uv run python mytrading/notify.py "메시지"
    uv run python mytrading/notify.py --demo          # 상황별 미리보기(전역)
    uv run python mytrading/notify.py --broadcast "공지"  # 전체 사용자에게
"""
import html
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

# 설정 파일 위치 (kis_devlp.yaml 에 bot_token/chat_id/enabled 를 함께 둠)
CONFIG_PATH = Path.home() / "KIS" / "config" / "kis_devlp.yaml"

_cached_config = None


def _load_config() -> dict:
    """설정 로드 (캐시). 없으면 빈 설정."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config
    if not CONFIG_PATH.exists():
        print(f"[notify] 설정 파일 없음: {CONFIG_PATH}")
        _cached_config = {}
        return _cached_config
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        _cached_config = yaml.safe_load(f) or {}
    return _cached_config


def _send_raw(chat_id: str, text: str, silent: bool = False,
              html_mode: bool = True, reply_markup=None) -> bool:
    """지정한 chat_id 로 직접 전송 (내부용). token 은 전역 설정에서."""
    cfg = _load_config()

    if not cfg.get("enabled", True):
        print("[notify] enabled=false → 전송 건너뜀")
        return False

    token = str(cfg.get("bot_token", "")).strip()
    chat_id = str(chat_id).strip()

    if not token or not chat_id or token.startswith("여기에"):
        print("[notify] bot_token / chat_id 미설정")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": silent,
    }
    if reply_markup is not None:
        import json as _json
        payload["reply_markup"] = _json.dumps(reply_markup, ensure_ascii=False)
    if html_mode:
        payload["parse_mode"] = "HTML"
    data = urllib.parse.urlencode(payload).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("ok"):
            return True
        print(f"[notify] 전송 실패(chat={chat_id}): {result.get('description', result)}")
        return False
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()
        except Exception:
            detail = str(e)
        # HTML 파싱 실패(<...> 포함 메시지)면 평문으로 재전송
        if e.code == 400 and "parse entities" in detail and html_mode:
            print(f"[notify] HTML 파싱 실패 -> 평문 재전송 (chat={chat_id})")
            payload.pop("parse_mode", None)
            data2 = urllib.parse.urlencode(payload).encode("utf-8")
            try:
                req2 = urllib.request.Request(url, data=data2, method="POST")
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    result2 = json.loads(resp2.read().decode("utf-8"))
                if result2.get("ok"):
                    return True
                print(f"[notify] 평문 재전송도 실패(chat={chat_id}): {result2.get('description', result2)}")
            except Exception as e2:
                print(f"[notify] 평문 재전송 오류(chat={chat_id}): {e2}")
            return False
        print(f"[notify] HTTP {e.code} (chat={chat_id}): {detail}")
        return False
    except Exception as e:
        print(f"[notify] 전송 오류(chat={chat_id}): {e}")
        return False


def send_message(text: str, silent: bool = False, html_mode: bool = True) -> bool:
    """
    기본 대상(전역 chat_id)에게 전송. 기존 호출 호환용.
    text       : 보낼 내용 (html_mode=True 면 <b></b> 등 HTML 태그 사용 가능)
    silent     : True 면 알림음 없이
    html_mode  : True 면 parse_mode=HTML (굵게 등)
    """
    cfg = _load_config()
    chat_id = str(cfg.get("chat_id", "")).strip()
    if not chat_id:
        print("[notify] 전역 chat_id 미설정")
        return False
    return _send_raw(chat_id, text, silent=silent, html_mode=html_mode)


def send_to(chat_id: str, text: str, silent: bool = False,
            html_mode: bool = True) -> bool:
    """특정 chat_id 에게 전송 (사용자별 알림용)."""
    return _send_raw(chat_id, text, silent=silent, html_mode=html_mode)


def broadcast(text: str, silent: bool = False, html_mode: bool = True,
              include_traders: bool = True) -> dict:
    """
    모든 사용자에게 전송 (Owner 공지용).
    accounts 로더의 users 에서 telegram_chat_id 를 모아 각각 전송.
    include_traders=False 면 Owner 에게만.
    반환: {user_key: 성공여부} 딕셔너리.
    중복 chat_id 는 한 번만 전송.
    """
    try:
        from mytrading.accounts import load_accounts
    except ImportError:
        from accounts import load_accounts  # 단독 실행 대비

    data = load_accounts()
    results = {}

    if not data.enabled:
        # 멀티계좌 미설정 → 전역 chat_id 한 명에게 (하위호환)
        ok = send_message(text, silent=silent, html_mode=html_mode)
        return {"(global)": ok}

    sent_ids = set()
    for u in data.users:
        if not include_traders and not u.is_owner:
            continue
        cid = str(u.telegram_chat_id).strip()
        if not cid:
            results[u.key] = False
            print(f"[notify] {u.key}: telegram_chat_id 없음 → 건너뜀")
            continue
        if cid in sent_ids:
            results[u.key] = True  # 이미 같은 chat_id 로 보냄
            continue
        ok = _send_raw(cid, text, silent=silent, html_mode=html_mode)
        results[u.key] = ok
        if ok:
            sent_ids.add(cid)
    return results


def _esc(s) -> str:
    """HTML 모드에서 안전하게 — 사용자 텍스트의 &<> 이스케이프."""
    return html.escape(str(s))


# ----- 상황별 헬퍼 -----
# to=None 이면 전역 chat_id, to=chat_id 면 그 사용자에게

def _dispatch(text: str, to: str = None) -> bool:
    return send_to(to, text) if to else send_message(text)


def notify_order_submitted(symbol: str, side: str, qty: int,
                           order_type: str = "시장가", to: str = None) -> bool:
    """주문 접수 알림. side 는 "BUY"/"SELL"(영문, 대소문자 무관) 고정 — 호출부가
    한글 "매수"/"매도"를 그대로 넘기면 "매수"가 "BUY"와 안 맞아 전부 "매도"로
    잘못 표시되는 버그가 있었다(2026-09-01 발견, 호출부들을 영문으로 통일해 수정).
    여기서도 인식 못 하는 값이면 조용히 "매도"로 떨어뜨리지 않고 원값을 그대로
    노출해서 — 앞으로 같은 실수가 나면 알림 문구에서 바로 티나게 한다."""
    side_up = str(side).upper()
    if side_up == "BUY":
        side_kr = "매수"
    elif side_up == "SELL":
        side_kr = "매도"
    else:
        side_kr = f"?({side})"
    text = (f"📤 <b>주문 접수</b>\n"
            f"종목: {_esc(symbol)}\n"
            f"{side_kr} {qty}주 ({_esc(order_type)})")
    return _dispatch(text, to)


def notify_order_filled(symbol: str, name: str, side: str,
                        qty: int, price: float, to: str = None) -> bool:
    """체결 완료 알림."""
    side_kr = "매수" if str(side).upper() == "BUY" else "매도"
    emoji = "🟢" if str(side).upper() == "BUY" else "🔴"
    amount = qty * price
    text = (f"{emoji} <b>체결 완료</b>\n"
            f"종목: {_esc(name)}({_esc(symbol)})\n"
            f"{side_kr} {qty}주 @ {price:,.0f}원\n"
            f"금액: {amount:,.0f}원")
    return _dispatch(text, to)


def notify_error(context: str, detail: str = "", to: str = None) -> bool:
    """에러/예외 알림."""
    text = f"⚠️ <b>오류</b>\n{_esc(context)}"
    if detail:
        text += f"\n{_esc(detail)}"
    return _dispatch(text, to)


def notify_balance(total_cash: float, total_equity: float,
                   total_pnl: float, pnl_pct: float, to: str = None) -> bool:
    """잔고 요약 알림."""
    sign = "📈" if total_pnl >= 0 else "📉"
    text = (f"💰 <b>잔고 요약</b>\n"
            f"현금: {total_cash:,.0f}원\n"
            f"평가액: {total_equity:,.0f}원\n"
            f"{sign} 손익: {total_pnl:,.0f}원 ({pnl_pct:+.2f}%)")
    return _dispatch(text, to)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--demo":
        # 상황별 메시지 미리보기 전송 (전역 chat_id)
        print("[notify] 데모 메시지 전송...")
        notify_order_submitted("005930", "BUY", 1, "시장가")
        notify_order_filled("005930", "삼성전자", "BUY", 1, 354000)
        notify_balance(10000000, 10354000, 354000, 3.54)
        notify_error("주문 실패", "모의투자 영업일이 아닙니다")
        print("[notify] 데모 완료 — 폰을 확인하세요")
    elif args and args[0] == "--broadcast":
        # 모든 사용자에게 공지 전송 (Owner 브로드캐스트 테스트)
        msg = args[1] if len(args) > 1 else "📢 전체 공지 테스트"
        print(f"[notify] 브로드캐스트: {msg}")
        results = broadcast(f"📢 <b>공지</b>\n{_esc(msg)}")
        for user_key, ok in results.items():
            print(f"  {user_key}: {'✅' if ok else '❌'}")
    else:
        msg = args[0] if args else "✅ 텔레그램 연결 테스트 — KIS 자동매매 시스템"
        print(f"[notify] 전송 시도: {msg}")
        ok = send_message(msg)
        print("[notify] 결과:", "성공 ✅ (폰을 확인하세요)" if ok else "실패 ❌")

def download_telegram_file(file_id: str, save_path: str) -> bool:
    """텔레그램 봇이 받은 파일을 다운로드해서 save_path 에 저장.
    
    흐름: getFile API 로 파일 경로 조회 → 그 경로에서 실제 파일 다운로드.
    반환: 성공 True, 실패 False.
    """
    cfg = _load_config()
    token = str(cfg.get("bot_token", "")).strip()
    if not token:
        print("[notify] bot_token 미설정 - 파일 다운로드 불가")
        return False

    # 1. file_id → file_path 조회
    try:
        url = f"https://api.telegram.org/bot{token}/getFile"
        data = json.dumps({"file_id": file_id}).encode("utf-8")
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if not result.get("ok"):
            print(f"[notify] getFile 실패: {result}")
            return False
        file_path = result["result"]["file_path"]
    except Exception as e:
        print(f"[notify] getFile 오류: {e}")
        return False

    # 2. 실제 파일 다운로드
    try:
        download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        req = urllib.request.Request(download_url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        import os
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(data)
        print(f"[notify] 파일 저장 완료: {save_path} ({len(data)} bytes)")
        return True
    except Exception as e:
        print(f"[notify] 파일 다운로드 오류: {e}")
        return False
