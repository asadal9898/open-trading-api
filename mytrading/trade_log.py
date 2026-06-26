"""
거래 로그 — 매매 내역을 JSONL 로 기록하고 조회한다.

- 저장 위치: ~/KIS/cache/trades.jsonl  (저장소 밖, git 에 안 올라감 — 실제 거래내역 보호)
- 형식: 한 줄에 거래 1건 (JSON)
- 모의/실전 같은 파일, mode 필드("vps"/"prod")로 구분

기록 (test_order/test_sell 에서):
    from mytrading.trade_log import record_trade
    record_trade(symbol="005930", name="삼성전자", side="BUY",
                 quantity=1, price=331500, order_id="0000044082")

조회 (weekly_report 에서):
    from mytrading.trade_log import trades_in_week
    trades = trades_in_week()  # 이번 주(월~일) 거래

CLI:
    uv run python mytrading/trade_log.py            # 최근 거래 출력
    uv run python mytrading/trade_log.py --week     # 이번 주 거래
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

# 저장소 밖 (실제 거래내역이 git 에 올라가지 않도록)
TRADES_PATH = Path.home() / "KIS" / "cache" / "trades.jsonl"


def _resolve_mode_safe() -> str:
    """현재 모드(vps/prod). common 을 못 불러오면 'vps' 로 안전 기본."""
    try:
        from mytrading.common import resolve_mode
        return resolve_mode()
    except Exception:
        return "vps"


def record_trade(symbol: str, name: str, side: str, quantity: int,
                 price: float, order_id: str = "", mode: str = None,
                 status: str = "submitted", account_no: str = "") -> dict:
    """
    거래 한 건을 기록한다 (trades.jsonl 에 한 줄 추가).
    side: "BUY" / "SELL"
    mode: None 이면 현재 모드 자동 판단 (vps/prod)
    반환: 기록된 거래 딕셔너리
    """
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "mode": mode or _resolve_mode_safe(),
        "symbol": str(symbol),
        "name": str(name or symbol),
        "side": str(side).upper(),
        "quantity": int(quantity),
        "price": float(price) if price is not None else None,
        "order_id": str(order_id or ""),
        "status": str(status),
        "account_no": str(account_no or ""),
    }
    try:
        TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        # 기록 실패해도 거래 자체를 막지 않음 (로그는 부가 기능)
        print(f"  [trade_log] 기록 실패(무시): {e}")
    return rec


def load_trades(mode: str = None) -> List[dict]:
    """
    전체 거래 로그를 리스트로 읽는다 (최신순 아님, 기록순).
    mode: "vps"/"prod" 주면 그 모드만 필터. None 이면 전체.
    """
    if not TRADES_PATH.exists():
        return []
    out = []
    try:
        with open(TRADES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if mode and rec.get("mode") != mode:
                    continue
                out.append(rec)
    except Exception as e:
        print(f"  [trade_log] 읽기 실패: {e}")
    return out


def trades_in_range(start: datetime, end: datetime,
                    mode: str = None) -> List[dict]:
    """start <= 거래시각 < end 인 거래만. (end 미포함)"""
    res = []
    for rec in load_trades(mode):
        try:
            ts = datetime.fromisoformat(rec["ts"])
        except (ValueError, KeyError):
            continue
        if start <= ts < end:
            res.append(rec)
    return res


def trades_in_week(ref: datetime = None, mode: str = None) -> List[dict]:
    """
    ref 가 속한 주(월요일 00:00 ~ 다음 월요일 00:00)의 거래.
    ref 기본 = 지금. weekly_report 에서 '이번 주 매매내역' 용도.
    """
    ref = ref or datetime.now()
    monday = (ref - timedelta(days=ref.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    next_monday = monday + timedelta(days=7)
    return trades_in_range(monday, next_monday, mode)


def summarize(trades: List[dict]) -> str:
    """거래 리스트를 사람이 읽는 문자열로 (주간알림용)."""
    if not trades:
        return "  (매매 없음)"
    lines = []
    for t in trades:
        ts = t.get("ts", "")[:16].replace("T", " ")  # YYYY-MM-DD HH:MM
        side_kr = "매수" if t.get("side") == "BUY" else "매도"
        px = t.get("price")
        px_str = f"{px:,.0f}원" if isinstance(px, (int, float)) else "-"
        lines.append(f"  {ts} {t.get('name', t.get('symbol'))} "
                     f"{t.get('quantity')}주 {side_kr} @ {px_str}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--week" in args:
        ts = trades_in_week()
        print(f"=== 이번 주 매매 ({len(ts)}건) ===")
        print(summarize(ts))
    else:
        allt = load_trades()
        recent = allt[-10:]
        print(f"=== 최근 거래 (전체 {len(allt)}건 중 {len(recent)}건) ===")
        print(summarize(recent))
        print(f"\n로그 파일: {TRADES_PATH}")