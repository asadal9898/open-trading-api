"""
주간 알림 — 다음 주 휴장 일정 + 계좌 현황을 주 1회 텔레그램으로.

내용:
  - 다음 주(향후 N일) 휴장 일정 (market_calendar.next_holidays)
  - 현재 계좌 현황 (account_snapshot)
  - (TODO) 그 주 매매 내역 — 거래 로그 생기면 추가

사용자별 발송:
  kis_devlp.yaml users 의 telegram_chat_id 로 각자에게.
  notify_day/notify_time 설정으로 사람마다 발송 요일/시간 지정 (스케줄러가 사용).

수동 실행 (2-a):
    uv run python mytrading/weekly_report.py            # 지금 바로 전송 (전체)
    uv run python mytrading/weekly_report.py --preview  # 전송 안 하고 메시지만 출력
    uv run python mytrading/weekly_report.py --user Owner  # 특정 사용자만

스케줄 (2-b):
    cron 이 매 시각 호출 → 각 사용자의 notify_day/notify_time 과 현재 시각이 맞으면 전송.
    uv run python mytrading/weekly_report.py --scheduled  # cron 용 (시간 맞는 사람만)
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_WD_KR = ["월", "화", "수", "목", "금", "토", "일"]
# 요일 이름 → 파이썬 weekday (월=0)
_WD_MAP = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6,
           "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# 기본 발송 시각
_DEFAULT_DAY = "토"     # 토요일
_DEFAULT_TIME = "15:00"


def _esc(s) -> str:
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_holiday_section(config: dict = None) -> str:
    """
    다음 주 휴장 일정 섹션.
    - 국내: 다음 주 월~금 중 공휴일만 (주말 제외)
    - 해외(미국/일본): config 의 weekly_report.overseas 설정이 on 이면 표시 (기본 off)
    """
    from mytrading.market_calendar import next_week_holidays

    lines = ["📅 <b>다음 주 휴장 (국내)</b>"]
    try:
        hs = next_week_holidays()
        if not hs:
            lines.append("  공휴일 없음 (평일 정상 개장)")
        else:
            for h in hs:
                d = h["date"]
                md = f"{d[4:6]}/{d[6:8]}" if len(d) == 8 else d
                lines.append(f"  {md} ({h['weekday']}) 휴장")
    except Exception as e:
        lines.append(f"  (조회 실패: {_esc(e)})")

    # 해외 휴장 (설정 on 일 때만 — 기본 off)
    cfg = config or {}
    ov = (cfg.get("weekly_report", {}) or {}).get("overseas", {}) or {}
    show_us = bool(ov.get("us", False))
    show_jp = bool(ov.get("japan", False))
    show_cn = bool(ov.get("china", False))

    if show_us or show_jp or show_cn:
        from mytrading.market_calendar import overseas_next_week_holidays
        lines.append("")
        lines.append("🌏 <b>다음 주 휴장 (해외)</b>")
        if show_us:
            us_hs = overseas_next_week_holidays("us")
            if us_hs:
                for h in us_hs:
                    d = h["date"]
                    md = f"{d[4:6]}/{d[6:8]}"
                    lines.append(f"  미국장 {md} ({h['weekday']}) 휴장")
            else:
                lines.append("  미국장: 휴장 없음")
        if show_jp:
            jp_hs = overseas_next_week_holidays("japan")
            if jp_hs:
                for h in jp_hs:
                    d = h["date"]
                    md = f"{d[4:6]}/{d[6:8]}"
                    lines.append(f"  일본장 {md} ({h['weekday']}) 휴장")
            else:
                lines.append("  일본장: 휴장 없음")
        if show_cn:
            cn_hs = overseas_next_week_holidays("china")
            if cn_hs:
                for h in cn_hs:
                    d = h["date"]
                    md = f"{d[4:6]}/{d[6:8]}"
                    lines.append(f"  중국장 {md} ({h['weekday']}) 휴장")
            else:
                lines.append("  중국장: 휴장 없음")

    return "\n".join(lines)


def build_account_section(brokerage) -> str:
    """현재 계좌 현황 섹션 (스냅샷)."""
    from mytrading.account_snapshot import get_snapshot
    try:
        snap = get_snapshot(brokerage)
    except Exception as e:
        return f"💰 <b>계좌 현황</b>\n  (조회 실패: {_esc(e)})"

    lines = ["💰 <b>계좌 현황</b>",
             f"  총 평가금액: {snap.total_equity:,.0f}원",
             f"  주문가능현금: {snap.available_cash:,.0f}원",
             f"  손익: {snap.total_pnl:,.0f}원 ({snap.total_pnl_percent:+.2f}%)"]
    if snap.holdings:
        lines.append("  [보유]")
        for h in snap.holdings:
            lines.append(f"   • {_esc(h.name)} {h.quantity}주 "
                         f"({h.pnl_percent:+.2f}%)")
    else:
        lines.append("  보유 종목 없음")
    return "\n".join(lines)


def build_trades_section(mode: str = None) -> str:
    """이번 주 매매 내역 섹션 (거래 로그에서)."""
    from mytrading.trade_log import trades_in_week, summarize
    try:
        trades = trades_in_week(mode=mode)
    except Exception as e:
        return f"📈 <b>이번 주 매매</b>\n  (조회 실패: {_esc(e)})"

    lines = [f"📈 <b>이번 주 매매</b> ({len(trades)}건)"]
    if not trades:
        lines.append("  매매 없음")
    else:
        for t in trades:
            ts = str(t.get("ts", ""))[5:16].replace("T", " ")  # MM-DD HH:MM
            side_kr = "매수" if t.get("side") == "BUY" else "매도"
            px = t.get("price")
            px_str = f"{px:,.0f}" if isinstance(px, (int, float)) else "-"
            lines.append(f"  {ts} {_esc(t.get('name', t.get('symbol')))} "
                         f"{t.get('quantity')}주 {side_kr} @ {px_str}")
    return "\n".join(lines)

def build_fx_section() -> str:
    """환율 섹션 — 3년 평균보다 낮은 날 있을 때만 (없으면 빈 문자열)."""
    try:
        from mytrading.fx_alert import build_fx_alert
        return build_fx_alert()
    except Exception:
        return ""


def build_yield_curve_section() -> str:
    """장단기 금리차 섹션 (한국+미국). 실패 시 빈 문자열."""
    try:
        from mytrading.telegram.yield_curve_alert import build_yield_curve_section as _yc
        return _yc()
    except Exception:
        return ""


def build_message(brokerage=None, config: dict = None) -> str:
    """주간 알림 전체 메시지 생성."""
    if config is None:
        try:
            from mytrading.common import CONFIG
            config = CONFIG
        except Exception:
            config = {}
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    parts = [f"📊 <b>주간 리포트</b> — {today}", ""]
    parts.append(build_holiday_section(config))
    parts.append("")
    if brokerage is not None:
        parts.append(build_account_section(brokerage))
        parts.append("")
    parts.append(build_trades_section())
    fx = build_fx_section()
    if fx:
        parts.append("")
        parts.append(fx)
    yc = build_yield_curve_section()
    if yc:
        parts.append("")
        parts.append(yc)
    return "\n".join(parts)

# ---------- 사용자별 발송 (2-b 기반) ----------

def _user_schedule(user) -> tuple:
    """사용자의 (발송요일 weekday, 'HH:MM'). 미설정이면 기본값(토 15:00)."""
    # accounts.User 에 notify_day/notify_time 속성이 있으면 사용 (없으면 기본)
    day_raw = getattr(user, "notify_day", None) or _DEFAULT_DAY
    time_raw = getattr(user, "notify_time", None) or _DEFAULT_TIME
    wd = _WD_MAP.get(str(day_raw).strip().lower(), 5)  # 기본 토(5)
    return wd, str(time_raw).strip()


def _is_send_time(user, now: datetime = None) -> bool:
    """지금이 이 사용자의 발송 시각인가? (요일 일치 + 시:분이 현재 시각의 그 시간대)."""
    now = now or datetime.now()
    wd, hhmm = _user_schedule(user)
    if now.weekday() != wd:
        return False
    try:
        h, m = [int(x) for x in hhmm.split(":")]
    except Exception:
        h, m = 15, 0
    # cron 이 매시 정각 호출한다고 가정 → 시(hour)만 비교 (분 단위는 cron 주기에 맞춤)
    return now.hour == h


def send_weekly(brokerage=None, only_user: str = None,
                scheduled: bool = False, preview: bool = False) -> dict:
    """
    주간 알림 발송.
    - preview=True : 전송 안 하고 메시지만 반환 (콘솔 확인용)
    - only_user    : 특정 사용자에게만
    - scheduled    : 각 사용자의 notify_day/time 과 현재 시각이 맞는 사람에게만 (cron용)
    반환: {user_key: 성공여부} 또는 {'preview': 메시지}
    """
    from mytrading.telegram.notify import send_to, send_message
    try:
        from mytrading.accounts import load_accounts
        adata = load_accounts()
    except Exception:
        adata = None

    msg = build_message(brokerage)

    if preview:
        print(msg)
        return {"preview": msg}

    # 멀티계좌 미설정 → 전역 chat_id 한 명
    if adata is None or not adata.enabled:
        ok = send_message(msg)
        return {"(global)": ok}

    results = {}
    now = datetime.now()
    for u in adata.users:
        if only_user and u.key != only_user:
            continue
        if scheduled and not _is_send_time(u, now):
            continue
        cid = str(getattr(u, "telegram_chat_id", "") or "").strip()
        if not cid:
            results[u.key] = False
            continue
        results[u.key] = send_to(cid, msg)
    return results


if __name__ == "__main__":
    args = sys.argv[1:]
    preview = "--preview" in args
    scheduled = "--scheduled" in args
    only_user = None
    if "--user" in args:
        i = args.index("--user")
        if i + 1 < len(args):
            only_user = args[i + 1]

    # 계좌 현황을 넣으려면 brokerage 필요 (preview 시엔 생략 가능)
    brokerage = None
    if not preview or "--with-account" in args:
        try:
            from mytrading.common import init, get_brokerage
            init(require_confirm=False)
            brokerage = get_brokerage()
        except Exception as e:
            print(f"[weekly] 계좌 조회 생략 (인증 실패: {e})")

    res = send_weekly(brokerage=brokerage, only_user=only_user,
                      scheduled=scheduled, preview=preview)
    if not preview:
        print("[weekly] 발송 결과:")
        for k, v in res.items():
            print(f"  {k}: {'✅' if v else '❌'}")