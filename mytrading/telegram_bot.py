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

# 대기 상태 저장 (종목 선택 중) — chat_id별
_PENDING_FILE = notify.CONFIG_PATH.parent / ".telegram_pending"

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
    # 한글 명령 -> 영어 매핑
    _KO = {
        "/추가": "/add", "/목록": "/list", "/승인": "/approve",
        "/재부팅": "/reboot", "/도움": "/help", "/시작": "/start",
    }
    cmd = _KO.get(cmd, cmd)

    if cmd == "/start" or cmd == "/help":
        return ("자유투자 봇 (한글 명령도 됨)\n"
                "/add(/추가) 종목명 - 종목 추가\n"
                "/list(/목록) - 내 종목\n"
                "(구현 예정: /approve(/승인) /reboot(/재부팅))")
    if cmd == "/add":
        if not args:
            return "사용법: /add 종목명  (예: /add SK하이닉스)"
        return _cmd_add(user, " ".join(args))
    # 숫자만 왔는데 대기중이면 후보 선택으로 처리
    if cmd.isdigit() and _get_pending(user["key"]):
        return _pick_candidate(user, int(cmd))
    if cmd == "/approve":
        if not args:
            return "사용법: /승인 종목명  (예: /승인 카카오)"
        return _cmd_approve(user, " ".join(args))
    if _get_pending("approve:" + user["key"]):
        return _approve_parse(user, text.strip())
    if cmd == "/approve":
        if not args:
            return "사용법: /승인 종목명  (예: /승인 카카오)"
        return _cmd_approve(user, " ".join(args))
    if _get_pending("approve:" + user["key"]):
        return _approve_parse(user, text.strip())
    if text.strip() in ("예", "yes", "y", "네"):
        return _cmd_confirm_add(user)
    if text.strip() in ("아니요", "no", "n", "아니오"):
        _set_pending("add:" + user["key"], None)
        return "취소했어요."
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


def _load_pending() -> dict:
    import json
    try:
        return json.loads(_PENDING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pending(d: dict):
    import json
    try:
        _PENDING_FILE.write_text(json.dumps(d, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception:
        pass


def _get_pending(ukey: str):
    return _load_pending().get(ukey)


def _set_pending(ukey: str, candidates):
    d = _load_pending()
    if candidates:
        d[ukey] = candidates
    else:
        d.pop(ukey, None)
    _save_pending(d)


def _cmd_add(user: dict, query: str) -> str:
    """종목명 -> 코드 검색. 하나면 분석, 여러개면 후보 선택."""
    import sys as _sys
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parents[1]
    fdir = _repo / "mytrading"
    if str(fdir) not in _sys.path:
        _sys.path.insert(0, str(fdir))
    from find_stock_code import find_by_name

    if query.strip().isdigit():
        return _analyze(query.strip(), "(코드 직접입력)", user["key"])

    matches = find_by_name(query)
    if not matches:
        _set_pending(user["key"], None)
        return f"'{query}' 종목을 못 찾았어요. 정확한 이름이나 코드로 다시."
    if len(matches) == 1:
        _set_pending(user["key"], None)
        code, name, _mkt = matches[0]
        return _analyze(code, name, user["key"])
    cands = [[c, n] for c, n, _m in matches[:8]]
    _set_pending(user["key"], cands)
    lines = [f"'{query}' 여러 종목이 있어요. 번호로 선택:"]
    for i, (c, n) in enumerate(cands, 1):
        lines.append(f"  {i}. {n} ({c})")
    lines.append("예: 3")
    return "\n".join(lines)


def _pick_candidate(user: dict, idx: int) -> str:
    cands = _get_pending(user["key"])
    if not cands:
        return "선택 대기중인 종목이 없어요. /add 부터."
    if idx < 1 or idx > len(cands):
        return f"1~{len(cands)} 사이 번호로."
    code, name = cands[idx - 1]
    _set_pending(user["key"], None)
    return _analyze(code, name, user["key"])


def _analyze(code: str, name: str, ukey: str = None) -> str:
    """종목 재무 분석 표시 (get_financial_summary)."""
    try:
        from mytrading.common import init
        init(require_confirm=False)
        from mytrading.finance_data import get_financial_summary
        industry = None
        try:
            import sys as _s
            from pathlib import Path as _P
            _repo = _P(__file__).resolve().parents[1]
            si = _repo / "examples_llm" / "domestic_stock" / "search_stock_info"
            if str(si) not in _s.path:
                _s.path.insert(0, str(si))
            from search_stock_info import search_stock_info
            df = search_stock_info(prdt_type_cd="300", pdno=code)
            if df is not None and not df.empty:
                industry = df.iloc[0].get("std_idst_clsf_cd_name")
        except Exception:
            pass
        summ = get_financial_summary(code, industry=industry)
    except Exception as e:
        return f"{name}({code}) 분석 오류: {e}"
    if not summ:
        return f"{name}({code}) 재무 데이터 없음"
    lines = [
        f"{name} ({code})",
        f"업종: {industry or '?'}",
        "─────────",
        f"부채: {summ.get('debt_note','?')}",
        f"ROE: {summ.get('roe','?')}",
        f"영업이익흑자: {summ.get('op_positive','?')}",
        f"매출증가율: {summ.get('revenue_growth','?')}%",
        f"영업이익증가율: {summ.get('op_profit_growth','?')}%",
        "─────────",
        "이 종목을 추가할까요?  예 / 아니요",
    ]
    if ukey:
        _set_pending("add:" + ukey, [[code, name, industry or ""]])
    return "\n".join(lines)


def _cmd_confirm_add(user: dict) -> str:
    """예 -> free_holdings에 추가 (Waiting). allocations.yaml 저장."""
    import yaml
    from pathlib import Path as _P
    pend = _get_pending("add:" + user["key"])
    if not pend:
        return "추가할 종목이 없어요. /add 부터."
    row = pend[0]
    code, name = row[0], row[1]
    industry = row[2] if len(row) > 2 else ""
    _set_pending("add:" + user["key"], None)

    _repo = _P(__file__).resolve().parents[1]
    alloc_path = _repo / "mytrading" / "allocations.yaml"
    try:
        with open(alloc_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return f"저장 실패(로드): {e}"

    fh = data.setdefault("free_holdings", {})
    ukey = user["key"]
    uacct = fh.setdefault(ukey, {})
    # 계좌: 첫 계좌 사용 (지금은 일반증권1)
    acct_name = next(iter(uacct.keys()), None)
    if acct_name is None:
        acct_name = "일반증권1"
        uacct[acct_name] = []
    lst = uacct[acct_name]
    if not isinstance(lst, list):
        lst = []
        uacct[acct_name] = lst

    # 중복 체크
    for it in lst:
        if isinstance(it, dict) and str(it.get("code")) == code:
            return f"{name}({code})은 이미 목록에 있어요."

    from datetime import date
    lst.append({
        "code": str(code), "name": name, "style": "free",
        "added_by": "telegram", "confirm": "Waiting",
        "added_date": date.today().isoformat(),
        "sector": "",
        "industry": industry or "",
        "note": "자유투자",
    })
    try:
        with open(alloc_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        return f"저장 실패(쓰기): {e}"

    return (f"✅ {name}({code}) 추가됨 (관찰중/Waiting)\n"
            f"매수하려면 /승인 {name} (수량 지정)")


def _cmd_approve(user: dict, query: str) -> str:
    """종목명 -> free_holdings에서 찾기 -> 매수방식 입력 안내."""
    import sys as _sys
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parents[1]
    fdir = _repo / "mytrading"
    if str(fdir) not in _sys.path:
        _sys.path.insert(0, str(fdir))
    from find_stock_code import find_by_name

    # 이름 -> 코드
    if query.strip().isdigit():
        code = query.strip()
        name = query.strip()
    else:
        matches = find_by_name(query)
        if not matches:
            return f"'{query}' 종목을 못 찾았어요."
        code, name, _m = matches[0]

    # free_holdings에 있는지 확인
    from mytrading.portfolio import load_portfolio
    pf = load_portfolio()
    found = False
    for _acc, al in (pf.allocations.get(user["key"], {}) or {}).items():
        for sym in al.free_symbols:
            if str(sym.get("code")) == str(code):
                found = True
                name = sym.get("name", name)
    if not found:
        return f"{name}은 자유 종목에 없어요. 먼저 /추가 하세요."

    # approve 대기 저장
    _set_pending("approve:" + user["key"], [[str(code), name]])
    return (f"{name}({code}) 매수 방식을 입력하세요 (수량):\n"
            f"  · 일시 10주\n"
            f"  · 분할 주1회 1주\n"
            f"  · 분할 매일 1주\n"
            f"  · 일시 5주 + 분할 주1회 1주  (동시)")


def _parse_buy_plan(text: str) -> dict:
    """매수방식 텍스트 파싱 -> buy_plan. 규칙 기반.
    반환: {onetime, split:{every, qty}} 또는 None(파싱 실패).
    split.every: daily | weekly | weekly_2x | biweekly
    """
    import re
    plan = {}
    t = text

    # 1) 일시매수 추출 후 원문에서 제거 (분할 수량과 혼동 방지)
    onetime_pat = r"(?:일시매수|일시|한\s*번에|한번에|1회에)\s*([0-9]+)\s*주"
    m = re.search(onetime_pat, t)
    if m:
        plan["onetime"] = int(m.group(1))
        t = t[:m.start()] + " " + t[m.end():]

    # 2) 분할 빈도 판정 (우선순위: 격주 > 주2회 > 주1회 > 매일)
    #    매칭된 빈도 표현은 t에서 제거 -> 수량 숫자와 안 겹치게
    freq_pats = [
        ("biweekly",  r"격주|보름\s*마다?|2\s*주\s*(?:마다|에\s*(?:1\s*회|한\s*번)|1\s*회)"),
        ("weekly_2x", r"주\s*2\s*회|주에\s*2\s*회|일주일에\s*2\s*회"),
        ("weekly",    r"매주|주\s*1\s*회|주에\s*1\s*회|일주일에\s*1\s*회|주마다"),
        ("daily",     r"매일|하루\s*(?:마다|에)|데일리"),
    ]
    every = None
    for _name, _pat in freq_pats:
        mm = re.search(_pat, t)
        if mm:
            every = _name
            t = t[:mm.start()] + " " + t[mm.end():]
            break

    # 3) 남은 텍스트에서 분할 수량 (N주씩 우선, 없으면 N주)
    if every:
        qm = re.search(r"([0-9]+)\s*주\s*씩", t) or re.search(r"([0-9]+)\s*주", t)
        if qm:
            plan["split"] = {"every": every, "qty": int(qm.group(1))}

    return plan if plan else None
def _approve_parse(user: dict, text: str) -> str:
    """approve 대기 중 텍스트 처리. 재확인 or buy_plan 저장."""
    pend = _get_pending("approve:" + user["key"])
    if not pend:
        return "승인 대기중인 종목이 없어요."
    code, name = pend[0][0], pend[0][1]

    # 재확인 응답 (예/아니요)
    if text in ("예", "yes", "y", "네"):
        saved = _get_pending("approve_plan:" + user["key"])
        if not saved:
            return "매수 방식을 먼저 입력하세요."
        plan = saved[0]
        result = _save_buy_plan(user, code, name, plan)
        _set_pending("approve:" + user["key"], None)
        _set_pending("approve_plan:" + user["key"], None)
        return result
    if text in ("아니요", "no", "n", "아니오"):
        _set_pending("approve:" + user["key"], None)
        _set_pending("approve_plan:" + user["key"], None)
        return "취소했어요."

    # 매수방식 파싱
    plan = _parse_buy_plan(text)
    if not plan:
        return ("못 알아들었어요. 예시대로 입력해주세요:\n"
                "  일시 10주 / 분할 주1회 1주 / 일시 5주 + 분할 주1회 1주")
    # 파싱 결과 재확인
    _set_pending("approve_plan:" + user["key"], [plan])
    parts = []
    if plan.get("onetime"):
        parts.append(f"일시매수 {plan['onetime']}주")
    if plan.get("split"):
        sp = plan["split"]
        freq = {"daily": "매일", "weekly": "매주",
                "weekly_2x": "주2회", "biweekly": "격주"}.get(sp["every"], sp["every"])
        parts.append(f"{freq} {sp['qty']}주씩")
    return (f"{name} 매수 계획:\n  " + "\n  ".join(parts) +
            "\n맞나요?  예 / 아니요")


def _save_buy_plan(user: dict, code: str, name: str, plan: dict) -> str:
    """buy_plan 저장 + confirm: Approval. allocations.yaml."""
    import yaml
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parents[1]
    alloc_path = _repo / "mytrading" / "allocations.yaml"
    try:
        with open(alloc_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        return f"저장 실패(로드): {e}"

    fh = (data.get("free_holdings") or {}).get(user["key"], {})
    updated = False
    for _acc, lst in fh.items():
        if not isinstance(lst, list):
            continue
        for it in lst:
            if isinstance(it, dict) and str(it.get("code")) == str(code):
                it["confirm"] = "Approval"
                it["buy_plan"] = plan
                updated = True
    if not updated:
        return f"{name} 종목을 못 찾았어요."
    try:
        with open(alloc_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    except Exception as e:
        return f"저장 실패(쓰기): {e}"
    return f"✅ {name}({code}) 매수 승인 완료 (Approval)\n다음 매매 시점부터 반영됩니다."


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


def run_loop(interval: int = 2):
    """계속 폴링 (봇처럼 실시간). Ctrl+C로 종료."""
    print("[bot] 폴링 시작 (Ctrl+C 종료)")
    try:
        while True:
            poll_once()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[bot] 종료")


if __name__ == "__main__":
    # 인자 없으면 루프, --once 면 한 번만
    if "--once" in sys.argv:
        poll_once()
    else:
        run_loop()
