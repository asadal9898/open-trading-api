"""
텔레그램 알림 모듈
설정: ~/KIS/config/kis_devlp.yaml 에 bot_token, chat_id, enabled 추가
표준 라이브러리(urllib)만 사용 — 추가 패키지 불필요.

기본 사용:
    from mytrading.notify import send_message
    send_message("아무 메시지")

상황별 헬퍼:
    from mytrading.notify import notify_order_filled, notify_order_submitted, \
                                 notify_error, notify_balance
    notify_order_filled("005930", "삼성전자", "BUY", 1, 354000)
    notify_error("주문 실패", "모의투자 영업일이 아닙니다")

테스트:
    uv run python mytrading/notify.py            # 테스트 메시지
    uv run python mytrading/notify.py "메시지"
    uv run python mytrading/notify.py --demo     # 상황별 메시지 미리보기 전송
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


def send_message(text: str, silent: bool = False, html_mode: bool = True) -> bool:
    """
    텔레그램으로 메시지 전송.
    text       : 보낼 내용 (html_mode=True 면 <b></b> 등 HTML 태그 사용 가능)
    silent     : True 면 알림음 없이
    html_mode  : True 면 parse_mode=HTML (굵게 등)
    반환: 성공 True / 실패·비활성 False
    """
    cfg = _load_config()

    if not cfg.get("enabled", True):
        print("[notify] enabled=false → 전송 건너뜀")
        return False

    token = str(cfg.get("bot_token", "")).strip()
    chat_id = str(cfg.get("chat_id", "")).strip()

    if not token or not chat_id or token.startswith("여기에"):
        print("[notify] bot_token / chat_id 미설정")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": silent,
    }
    if html_mode:
        payload["parse_mode"] = "HTML"
    data = urllib.parse.urlencode(payload).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("ok"):
            return True
        print(f"[notify] 전송 실패: {result.get('description', result)}")
        return False
    except urllib.error.HTTPError as e:
        # 텔레그램이 주는 실제 사유 출력 (디버깅에 유용)
        try:
            detail = e.read().decode()
        except Exception:
            detail = str(e)
        print(f"[notify] HTTP {e.code}: {detail}")
        return False
    except Exception as e:
        print(f"[notify] 전송 오류: {e}")
        return False


def _esc(s) -> str:
    """HTML 모드에서 안전하게 — 사용자 텍스트의 &<> 이스케이프."""
    return html.escape(str(s))


# ----- 상황별 헬퍼 -----

def notify_order_submitted(symbol: str, side: str, qty: int,
                           order_type: str = "시장가") -> bool:
    """주문 접수 알림."""
    side_kr = "매수" if str(side).upper() == "BUY" else "매도"
    text = (f"📤 <b>주문 접수</b>\n"
            f"종목: {_esc(symbol)}\n"
            f"{side_kr} {qty}주 ({_esc(order_type)})")
    return send_message(text)


def notify_order_filled(symbol: str, name: str, side: str,
                        qty: int, price: float) -> bool:
    """체결 완료 알림."""
    side_kr = "매수" if str(side).upper() == "BUY" else "매도"
    emoji = "🟢" if str(side).upper() == "BUY" else "🔴"
    amount = qty * price
    text = (f"{emoji} <b>체결 완료</b>\n"
            f"종목: {_esc(name)}({_esc(symbol)})\n"
            f"{side_kr} {qty}주 @ {price:,.0f}원\n"
            f"금액: {amount:,.0f}원")
    return send_message(text)


def notify_error(context: str, detail: str = "") -> bool:
    """에러/예외 알림."""
    text = f"⚠️ <b>오류</b>\n{_esc(context)}"
    if detail:
        text += f"\n{_esc(detail)}"
    return send_message(text)


def notify_balance(total_cash: float, total_equity: float,
                   total_pnl: float, pnl_pct: float) -> bool:
    """잔고 요약 알림."""
    sign = "📈" if total_pnl >= 0 else "📉"
    text = (f"💰 <b>잔고 요약</b>\n"
            f"현금: {total_cash:,.0f}원\n"
            f"평가액: {total_equity:,.0f}원\n"
            f"{sign} 손익: {total_pnl:,.0f}원 ({pnl_pct:+.2f}%)")
    return send_message(text)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--demo":
        # 상황별 메시지 미리보기 전송
        print("[notify] 데모 메시지 전송...")
        notify_order_submitted("005930", "BUY", 1, "시장가")
        notify_order_filled("005930", "삼성전자", "BUY", 1, 354000)
        notify_balance(10000000, 10354000, 354000, 3.54)
        notify_error("주문 실패", "모의투자 영업일이 아닙니다")
        print("[notify] 데모 완료 — 폰을 확인하세요")
    else:
        msg = args[0] if args else "✅ 텔레그램 연결 테스트 — KIS 자동매매 시스템"
        print(f"[notify] 전송 시도: {msg}")
        ok = send_message(msg)
        print("[notify] 결과:", "성공 ✅ (폰을 확인하세요)" if ok else "실패 ❌")