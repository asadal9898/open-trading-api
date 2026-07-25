import yaml as _yaml_qs

class _QStr(str):
    """YAML 덤프 시 항상 따옴표로 감싸지는 문자열 (종목코드 앞자리 0 보존)."""
    pass

def _q_repr(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="'")

_yaml_qs.SafeDumper.add_representer(_QStr, _q_repr)

# --- allocations.yaml 주석 보존용 (ruamel 라운드트립) ---
from ruamel.yaml import YAML as _YAML
from ruamel.yaml.scalarstring import SingleQuotedScalarString as _SQStr
_RT = _YAML()
_RT.preserve_quotes = True
_RT.indent(mapping=2, sequence=4, offset=2)
_RT.allow_unicode = True

def _rt_load(path):
    from pathlib import Path as _PP
    with open(path, encoding="utf-8") as _f:
        return _RT.load(_f) or {}

def _rt_dump(data, path):
    with open(path, "w", encoding="utf-8") as _f:
        _RT.dump(data, _f)


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
        "/상태": "/status",
    }
    cmd = _KO.get(cmd, cmd)

    if _get_pending("reboot:" + user["key"]):
        return _reboot_check_pw(user, text)
    if cmd == "/reboot":
        return _cmd_reboot(user)
    if cmd == "/start" or cmd == "/help":
        return ("자유투자 봇 (한글 명령도 됨)\n"
                "/add(/추가) 종목명 - 종목 추가\n"
                "/approve(/승인) 종목명 - 매수 승인\n"
                "/list(/목록) - 내 종목\n"
                "/status(/상태) - 모드·계좌·예산\n"
                "/reboot(/재부팅) - 재부팅 (owner)")
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
    if text.strip() in ("예", "yes", "y", "네"):
        return _cmd_confirm_add(user)
    if text.strip() in ("아니요", "no", "n", "아니오"):
        _set_pending("add:" + user["key"], None)
        return "취소했어요."
    if cmd == "/list":
        return _cmd_list(user)
    if cmd == "/status":
        return _cmd_status(user)
    return f"모르는 명령: {cmd}"


def _cmd_status(user: dict) -> str:
    """현재 모드·계좌·자유예산·종목수 조회 (전환 없음, 안전)."""
    # 모드
    paper = _is_paper()
    mode_line = "✅ 모의투자 (vps)" if paper else "🚨 실전투자 (prod)"

    # 계좌·평가금액
    acct_line = "계좌: (조회 실패)"
    equity_line = ""
    try:
        from mytrading.common import get_brokerage
        from mytrading.account_snapshot import get_snapshot
        snap = get_snapshot(get_brokerage())
        equity_line = f"총평가금액: {snap.total_equity:,.0f}원\n"
        equity_line += f"주문가능현금: {snap.available_cash:,.0f}원\n"
        equity_line += f"보유종목: {len(snap.holdings)}개\n"
    except Exception as e:
        equity_line = f"평가금액 조회 실패: {e}\n"

    # 자유예산
    budget, acc_name, free_pct = _free_budget(user)
    if budget > 0:
        acct_line = f"계좌: {acc_name}"
        budget_line = f"자유예산: {budget:,.0f}원 (free {free_pct:.0f}%)\n"
    else:
        budget_line = "자유예산: (계산 불가 — free 비중 확인)\n"

    # 등록 종목 수
    n_stocks = 0
    try:
        from pathlib import Path as _P
        _repo = _P(__file__).resolve().parents[1]
        data = _rt_load(_repo / "mytrading" / "allocations.yaml")
        fh = (data.get("free_holdings") or {}).get(user["key"], {})
        for _acc, lst in fh.items():
            if isinstance(lst, list):
                n_stocks += len(lst)
    except Exception:
        pass

    return (f"📊 시스템 상태\n"
            f"────────\n"
            f"모드: {mode_line}\n"
            f"{acct_line}\n"
            f"{equity_line}"
            f"{budget_line}"
            f"자유 등록종목: {n_stocks}개")


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


def _is_trading_hours() -> bool:
    """개장일 AND 09:00~15:30 이면 True. 판정 실패시 안전측(True)."""
    from datetime import datetime, time as _t
    try:
        from mytrading.market_calendar import is_market_open
        if not is_market_open():
            return False
    except Exception:
        pass
    now = datetime.now().time()
    return _t(9, 0) <= now <= _t(15, 30)


def _do_reboot() -> str:
    """실제 재부팅 실행. sudo /usr/sbin/reboot (NOPASSWD)."""
    import subprocess, threading
    def _delayed():
        import time as _tm
        _tm.sleep(2)  # 응답 메시지 전송 완료 대기
        try:
            subprocess.run(["sudo", "/usr/sbin/reboot"], timeout=10)
        except Exception as _e:
            print(f"[bot] 재부팅 실행 오류: {_e}")
    threading.Thread(target=_delayed, daemon=True).start()
    return "재부팅합니다. 잠시 후 봇이 다시 올라옵니다."


def _cmd_reboot(user: dict) -> str:
    """재부팅. owner만. 매매시간이면 비번, 장외면 확인 버튼."""
    if user.get("role") != "owner":
        return "재부팅은 owner만 가능해요."
    if _is_trading_hours():
        _set_pending("reboot:" + user["key"], [["await_pw"]])
        return "매매시간입니다. 재부팅하려면 비밀번호를 입력하세요."
    return "재부팅할까요?\x00REBOOT_CONFIRM"


def _reboot_check_pw(user: dict, text: str) -> str:
    """reboot 대기 중 비번 대조 (kis_devlp.yaml reboot_password)."""
    _set_pending("reboot:" + user["key"], None)
    pw = str(_cfg.get("reboot_password", "")).strip()
    if not pw:
        return "reboot_password가 설정 안 됐어요 (kis_devlp.yaml)."
    if text.strip() == pw:
        return _do_reboot()
    return "비밀번호가 틀렸어요. 재부팅 취소."


def _cmd_add(user: dict, query: str) -> str:
    """종목명 -> 코드 검색. 하나면 분석, 여러개면 후보 선택."""
    # 새 흐름 시작 - 이전 미완료 대기 모두 정리 (add/approve 흐름 섞임 방지)
    for _k in ("approve:", "approve_plan:", "add:", ""):
        _set_pending(_k + user["key"], None)
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


def _sector_now(code: str) -> str:
    """업종명 조회. find_dividend_stocks._stock_sector 재사용."""
    import sys as _sys
    from pathlib import Path as _P
    fdir = _P(__file__).resolve().parents[1] / "mytrading"
    if str(fdir) not in _sys.path:
        _sys.path.insert(0, str(fdir))
    try:
        from find_dividend_stocks import _stock_sector
        return _stock_sector(str(code)) or ""
    except Exception:
        return ""


def _industry_now(code: str) -> str:
    """DART로 표준산업분류(업종명) 조회. 실패 시 '모의추가' 폴백.
    모의(vps)에서 KIS 재무 API가 막힐 때 DART로 업종을 채운다."""
    try:
        from mytrading.dart_data import get_industry
        ind = get_industry(str(code))
        return ind if ind else "모의추가"
    except Exception as e:
        print(f"[bot] _industry_now({code}) 실패 → 모의추가 폴백: {e}")
        return "모의추가"


def _analyze_paper(code: str, name: str, ukey: str = None) -> str:
    """모의투자(vps): 재무분석 없이 현재가·예산 기반 매수 UI 생성."""
    price = _price_now(code)
    sector = _sector_now(code)
    budget, acc_name, free_pct = _free_budget(ukey and {"key": ukey} or {"key": ""})
    if price <= 0:
        return f"{name}({code}) 현재가 조회 실패. 잠시 후 다시 시도하세요."
    if budget <= 0:
        return (f"{name}({code}) 자유투자 예산을 계산할 수 없어요.\n"
                f"allocations.yaml에 free 비중이 설정됐는지 확인하세요.")

    full = int(budget // price)
    half = full // 2

    # add 대기 저장 (industry는 DART로 조회, 실패 시 '모의추가' 폴백)
    if ukey:
        industry = _industry_now(code)  # DART 업종 조회 (실패 시 모의추가 폴백)
        _set_pending("add:" + ukey, [[code, name, industry, sector or ""]])
        # 매수 UI 컨텍스트 저장 (콜백 재그리기·확정에 필요)
        _set_pending("pbuy_ctx:" + ukey,
                     [{"code": code, "name": name, "half": half,
                       "full": full, "price": int(price), "sel": 0}])

    header = (f"✅ {name}({code}) 모의 투자\n"
              f"💼 자유 투자 예산: {budget:,.0f}원 (총자산의 {free_pct:.0f}%)\n"
              f"📈 현재가: {price:,.0f}원\n")
    if full < 1:
        header += (f"📊 예산으로 1주도 부족해요.\n"
                   f"그래도 등록만 하려면 [취소 (등록만)]을 누르세요."
                   f"\x00BUYUI:{code}:0:0:{int(price)}")
        return header
    header += (f"📊 최대 매수: {full}주 ({full*int(price):,}원)"
               f"\x00BUYUI:{code}:{half}:{full}:{int(price)}")
    return header


def _analyze(code: str, name: str, ukey: str = None) -> str:
    """종목 재무 분석 표시 (get_financial_summary)."""
    # 모의투자(vps): 재무분석 API 미지원 → 매수 UI로 바로
    if _is_paper():
        return _analyze_paper(code, name, ukey)
    try:
        from mytrading.common import init
        init(require_confirm=False)
        from mytrading.finance_data import get_financial_summary
        industry = None
        sector = None
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
        # sector: 현재가 조회(inquire_price)의 bstp_kor_isnm(업종 한글명).
        #   search_stock_info의 idx_bztp_*는 지수소속이라 부정확
        #   (카카오=KOGI지배구조지수, 하이브=빈값). bstp_kor_isnm은 전종목 안정적.
        try:
            ip = _repo / "examples_llm" / "domestic_stock" / "inquire_price"
            if str(ip) not in _s.path:
                _s.path.insert(0, str(ip))
            from inquire_price import inquire_price
            pdf = inquire_price("real", "J", code)
            if pdf is not None and not pdf.empty:
                bs = pdf.iloc[0].get("bstp_kor_isnm")
                if bs:
                    sector = bs
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
        "이 종목을 추가할까요?  예 / 아니요\x00YESNO",
    ]
    if ukey:
        _set_pending("add:" + ukey, [[code, name, industry or "", sector or ""]])
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
    sector = row[3] if len(row) > 3 else ""
    _set_pending("add:" + user["key"], None)

    _repo = _P(__file__).resolve().parents[1]
    alloc_path = _repo / "mytrading" / "allocations.yaml"
    try:
        data = _rt_load(alloc_path)
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
        "code": _SQStr(str(code).zfill(6)), "name": name, "style": "free",
        "added_by": "telegram", "confirm": "Waiting",
        "added_date": date.today().isoformat(),
        "sector": sector or "",
        "industry": industry or "",
        "note": "자유투자",
    })
    try:
        _rt_dump(data, alloc_path)
    except Exception as e:
        return f"저장 실패(쓰기): {e}"

    return (f"✅ {name}({code}) 추가됨 (관찰중/Waiting)\n"
            f"매수하려면 아래 버튼을 누르세요.\x00ADDDONE:{code}:{name}")


def _cmd_approve(user: dict, query: str) -> str:
    """종목명 -> free_holdings에서 찾기 -> 매수방식 입력 안내."""
    # 새 흐름 시작 - 이전 미완료 대기 모두 정리 (add/approve 흐름 섞임 방지)
    for _k in ("approve:", "approve_plan:", "add:", ""):
        _set_pending(_k + user["key"], None)
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

    # 예산·현재가 조회 → 수량 버튼. 실패 시 텍스트 폴백.
    budget, acc_name, free_pct = _free_budget(user)
    price = _price_now(code)
    _text_fallback = (f"{name}({code}) 매수 방식을 입력하세요 (수량):\n"
                      f"  · 일시 10주\n"
                      f"  · 분할 주1회 1주\n"
                      f"  · 분할 매일 1주\n"
                      f"  · 일시 5주 + 분할 주1회 1주  (동시)")
    if budget <= 0 or price <= 0:
        return _text_fallback

    full = int(budget // price)          # 전액 매수 가능 주수
    half = full // 2                     # 반

    # 매수 UI 컨텍스트 저장 (스테퍼 콜백이 읽음)
    _set_pending("pbuy_ctx:" + user["key"],
                 [{"code": str(code), "name": name, "half": half,
                   "full": full, "price": int(price), "sel": 0}])

    _mode_txt = "모의 투자" if _is_paper() else "실전 투자"
    header = (f"✅ {name}({code}) {_mode_txt}\n"
              f"💼 자유 투자 예산: {budget:,.0f}원 ({acc_name} free {free_pct:.0f}%)\n"
              f"📈 현재가: {price:,.0f}원\n")
    if full < 1:
        header += (f"📊 예산으로 1주도 부족해요.\n"
                   f"그래도 등록만 하려면 [취소 (등록만)]을 누르세요."
                   f"\x00BUYUI:{code}:0:0:{int(price)}")
        return header
    header += (f"📊 최대 매수: {full}주 ({full*int(price):,}원)"
               f"\x00BUYUI:{code}:{half}:{full}:{int(price)}")
    return header


def _is_paper() -> bool:
    """현재 모의투자(vps) 모드인가. common.resolve_mode 재사용."""
    try:
        from mytrading.common import resolve_mode
        return resolve_mode() == "vps"
    except Exception:
        import os as _os
        return _os.environ.get("KIS_MODE", "vps") != "prod"


def _price_now(code: str) -> float:
    """현재가 조회. find_dividend_stocks._current_price 재사용."""
    import sys as _sys
    from pathlib import Path as _P
    fdir = _P(__file__).resolve().parents[1] / "mytrading"
    if str(fdir) not in _sys.path:
        _sys.path.insert(0, str(fdir))
    try:
        from find_dividend_stocks import _current_price
        return float(_current_price(str(code)) or 0.0)
    except Exception:
        return 0.0


def _free_budget(user: dict):
    """사용자 첫 계좌의 자유투자 예산.
    반환: (budget, account_name, free_pct) 또는 (0, None, 0) 실패 시.
    예산 = total_equity x (free% / 100).
    """
    try:
        from mytrading.portfolio import load_portfolio
        pf = load_portfolio()
        accts = pf.allocations.get(user["key"], {}) or {}
        # free 비중 > 0 인 첫 계좌
        acc_name, alloc = None, None
        for _an, _al in accts.items():
            if getattr(_al, "free", 0) and _al.free > 0:
                acc_name, alloc = _an, _al
                break
        if alloc is None:
            return (0.0, None, 0.0)
        from mytrading.common import get_brokerage
        from mytrading.account_snapshot import get_snapshot
        snap = get_snapshot(get_brokerage())
        budget = float(snap.total_equity) * (float(alloc.free) / 100.0)
        return (budget, acc_name, float(alloc.free))
    except Exception as e:
        print(f"[bot] _free_budget 실패: {e}")
        return (0.0, None, 0.0)


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
            "\n맞나요?  예 / 아니요\x00YESNO")


def _save_buy_plan(user: dict, code: str, name: str, plan: dict) -> str:
    """buy_plan 저장 + confirm: Approval. allocations.yaml."""
    import yaml
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parents[1]
    alloc_path = _repo / "mytrading" / "allocations.yaml"
    try:
        data = _rt_load(alloc_path)
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
        _rt_dump(data, alloc_path)
    except Exception as e:
        return f"저장 실패(쓰기): {e}"
    return f"✅ {name}({code}) 매수 승인 완료 (Approval)\n다음 매매 시점부터 반영됩니다."


# --- add 버튼 헬퍼 ---
def _name_by_code(user: dict, code: str):
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parents[1]
    alloc_path = _repo / "mytrading" / "allocations.yaml"
    try:
        data = _rt_load(alloc_path)
    except Exception:
        return None
    fh = (data.get("free_holdings") or {}).get(user["key"], {})
    for _acc, lst in fh.items():
        if not isinstance(lst, list):
            continue
        for it in lst:
            if isinstance(it, dict) and str(it.get("code")) == str(code):
                return it.get("name")
    return None


def _delete_holding(user: dict, code: str) -> str:
    from pathlib import Path as _P
    _repo = _P(__file__).resolve().parents[1]
    alloc_path = _repo / "mytrading" / "allocations.yaml"
    try:
        data = _rt_load(alloc_path)
    except Exception as e:
        return f"삭제 실패(로드): {e}"
    fh = (data.get("free_holdings") or {}).get(user["key"], {})
    removed = None
    for _acc, lst in fh.items():
        if not isinstance(lst, list):
            continue
        for i, it in enumerate(lst):
            if isinstance(it, dict) and str(it.get("code")) == str(code):
                removed = it.get("name", code)
                del lst[i]
                break
        if removed:
            break
    if not removed:
        return f"{code} 종목을 목록에서 못 찾았어요."
    try:
        _rt_dump(data, alloc_path)
    except Exception as e:
        return f"삭제 실패(쓰기): {e}"
    return f"🗑 {removed}({code}) 목록에서 삭제했어요."


# --- 인라인 버튼 지원 ---
_YESNO_KEYBOARD = {"inline_keyboard": [[
    {"text": "✅ 예", "callback_data": "yes"},
    {"text": "❌ 아니요", "callback_data": "no"},
]]}

def _stepper_keyboard(code, cur, full, price, half=0):
    """수량 스테퍼 키보드 (전환 없이 단독 사용).
    cur=현재수량(0 허용=매수안함), full=상한(전액), half=반."""
    cur = max(0, int(cur or 0))
    amt = cur * price
    rows = [
        [{"text": (f"수량: {cur}주 ({amt:,}원)" if cur >= 1 else "수량: 0주 (일시매수 안 함)"), "callback_data": "noop"}],
        [
            {"text": "-10", "callback_data": f"pstep:{code}:-10"},
            {"text": "-5", "callback_data": f"pstep:{code}:-5"},
            {"text": "-1", "callback_data": f"pstep:{code}:-1"},
            {"text": "+1", "callback_data": f"pstep:{code}:1"},
            {"text": "+5", "callback_data": f"pstep:{code}:5"},
            {"text": "+10", "callback_data": f"pstep:{code}:10"},
        ],
    ]
    big = []
    if full and full >= 1000:
        big.append({"text": "-1000", "callback_data": f"pstep:{code}:-1000"})
    if full and full >= 100:
        big.append({"text": "-100", "callback_data": f"pstep:{code}:-100"})
        big.append({"text": "+100", "callback_data": f"pstep:{code}:100"})
    if full and full >= 1000:
        big.append({"text": "+1000", "callback_data": f"pstep:{code}:1000"})
    if big:
        rows.append(big)
    preset = []
    if half and half >= 1:
        preset.append({"text": f"반 {half}주", "callback_data": f"pset:{code}:{half}"})
    if full and full >= 1:
        preset.append({"text": f"전액 {full}주", "callback_data": f"pset:{code}:{full}"})
    if preset:
        rows.append(preset)
    rows.append([
        {"text": "\u2705 매수 (분할매수 설정)", "callback_data": f"pbuy_go:{code}"},
        {"text": "\u274c 취소 (등록만)", "callback_data": f"pbuy_cancel:{code}"},
    ])
    return {"inline_keyboard": rows}

def _split_keyboard(code, qty, onetime, every="daily"):
    """분할매수 설정 키보드. qty=주기당수량, onetime=확정된일시매수, every=주기."""
    qty = max(1, int(qty or 1))
    freq_labels = [("daily", "매일"), ("weekly", "매주"),
                   ("biweekly", "격주"), ("monthly", "매월")]
    rows = [
        [{"text": f"분할 수량: {qty}주 (주기당)", "callback_data": "noop"}],
        [
            {"text": "-10", "callback_data": f"sstep:{code}:-10"},
            {"text": "-5", "callback_data": f"sstep:{code}:-5"},
            {"text": "-1", "callback_data": f"sstep:{code}:-1"},
            {"text": "+1", "callback_data": f"sstep:{code}:1"},
            {"text": "+5", "callback_data": f"sstep:{code}:5"},
            {"text": "+10", "callback_data": f"sstep:{code}:10"},
        ],
    ]
    freq_row = []
    for key, label in freq_labels:
        chk = "\u2705 " if key == every else ""
        freq_row.append({"text": f"{chk}{label}",
                         "callback_data": f"sfreq:{code}:{key}"})
    rows.append(freq_row)
    rows.append([
        {"text": "\u2705 분할매수 저장", "callback_data": f"ssave:{code}"},
        {"text": "\u274c 취소 (분할 안 함)", "callback_data": f"snone:{code}"},
    ])
    return {"inline_keyboard": rows}


def _split_marker(reply: str):
    """응답 문자열에서 버튼 마커를 분리. (텍스트, reply_markup) 반환."""
    if not reply:
        return reply, None
    if "\x00ADDDONE:" in reply:
        head, _, rest = reply.partition("\x00ADDDONE:")
        code = rest.split(":", 1)[0]
        kb = {"inline_keyboard": [[
            {"text": "💰 승인", "callback_data": f"approve:{code}"},
            {"text": "🗑 취소", "callback_data": f"reject:{code}"},
        ]]}
        return head, kb
    if "\x00REBOOT_CONFIRM" in reply:
        kb = {"inline_keyboard": [[
            {"text": "🔄 재부팅", "callback_data": "reboot_yes"},
            {"text": "❌ 취소", "callback_data": "reboot_no"},
        ]]}
        return reply.replace("\x00REBOOT_CONFIRM", ""), kb
    if "\x00BUYUI:" in reply:
        head, _, rest = reply.partition("\x00BUYUI:")
        parts = rest.split(":")
        code = parts[0] if len(parts) > 0 else ""
        half = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        full = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        price = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        kb = _stepper_keyboard(code, 0, full, price, half=half)
        return head, kb
    if "\x00YESNO" in reply:
        return reply.replace("\x00YESNO", ""), _YESNO_KEYBOARD
    return reply, None

def _edit_markup(chat_id, message_id, reply_markup):
    """기존 메시지의 인라인 키보드만 교체 (라디오 체크 표시용)."""
    import json as _json
    try:
        requests.post(f"{_API}/editMessageReplyMarkup", json={
            "chat_id": str(chat_id),
            "message_id": message_id,
            "reply_markup": reply_markup,
        }, timeout=5)
    except Exception as e:
        print(f"[bot] _edit_markup 실패: {e}")


def _answer_callback(cb_id: str):
    """버튼 탭 응답(로딩 표시 제거)."""
    try:
        requests.get(f"{_API}/answerCallbackQuery",
                     params={"callback_query_id": cb_id}, timeout=5)
    except Exception:
        pass


def _classify_document(file_name: str):
    """파일명에서 저장 폴더 결정. 반환: (폴더, 카테고리 라벨)."""
    fn = file_name.lower()
    # KCIF 카테고리 (내용 키워드 우선)
    if "리스크" in file_name and "워치" in file_name:
        return ("mytrading/reports/kcif/risk_watch", "KCIF 리스크워치")
    if "국제금융" in file_name or "insight" in fn or "인사이트" in file_name:
        return ("mytrading/reports/kcif/insight", "KCIF INSIGHT")
    # 한국은행 3종 (기존 폴더 재활용)
    if "금융안정" in file_name:
        return ("mytrading/reports/금융안정보고서", "금융안정보고서")
    if "통화신용정책" in file_name or "통화신용" in file_name:
        return ("mytrading/reports/통화신용정책보고서", "통화신용정책보고서")
    if "경제전망" in file_name:
        return ("mytrading/reports/bok/eor", "경제전망보고서")
    # 분류 안 되면 저장하지 않음
    return None


def _safe_filename(name: str) -> str:
    """파일명에서 위험 문자 제거."""
    import re
    # 경로 구분자, 상위 이동, 제어문자 제거
    name = name.replace("/", "_").replace("\\", "_")
    name = name.replace("..", "_")
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name.strip() or "unnamed"


def _save_document(user: dict, file_name: str, file_id: str, chat_id: str):
    """받은 파일을 분류해서 저장. 결과 답장."""
    if not file_name:
        notify._send_raw(chat_id, "파일명이 비어있어요.")
        return
    safe_name = _safe_filename(file_name)
    result = _classify_document(safe_name)
    if result is None:
        notify._send_raw(chat_id, f"⏭️ 분류되지 않는 문서라 저장하지 않아요.\n파일: {safe_name}")
        return
    folder, label = result
    save_path = f"{folder}/{safe_name}"
    print(f"[bot] {user['name']} → 파일 수신: {safe_name} → {label}")
    ok = notify.download_telegram_file(file_id, save_path)
    if ok:
        notify._send_raw(chat_id,
            f"📁 저장 완료\n분류: {label}\n파일: {safe_name}")
    else:
        notify._send_raw(chat_id,
            f"❌ 저장 실패\n파일: {safe_name}\n로그를 확인하세요.")
def poll_once():
    """한 번 폴링해서 새 명령 처리."""
    if not _TOKEN or _TOKEN.startswith("여기에"):
        print("[bot] bot_token 미설정")
        return
    users = _load_users()
    updates = get_updates()
    for up in updates:
        try:
            _save_offset(up["update_id"])
    
            # --- 버튼 탭(callback_query) 처리 ---
            cb = up.get("callback_query")
            if cb:
                cb_msg = cb.get("message") or {}
                chat_id = str((cb_msg.get("chat") or {}).get("id", "")).strip()
                data = cb.get("data", "")
                _answer_callback(cb.get("id", ""))
                if chat_id not in users:
                    continue
                user = users[chat_id]
                if data.startswith("approve:"):
                    code = data.split(":", 1)[1]
                    nm = _name_by_code(user, code) or code
                    print(f"[bot] {user['name']}({user['role']}) [버튼] 승인: {nm}")
                    reply = _cmd_approve(user, nm)
                    body, markup = _split_marker(reply)
                    notify._send_raw(chat_id, body, reply_markup=markup)
                    continue
                if data.startswith("pstep:"):
                    _p = data.split(":")
                    delta = int(_p[2]) if len(_p) > 2 and _p[2].lstrip("-").isdigit() else 0
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp:
                        notify._send_raw(chat_id, "매수 대기가 만료됐어요. 다시 /추가 하세요.")
                        continue
                    ctx = ctxp[0]
                    cur = (ctx.get("sel") or 0) + delta
                    hi = ctx["full"] if ctx.get("full", 0) >= 1 else cur
                    cur = max(0, min(cur, hi))
                    ctx["sel"] = cur
                    _set_pending("pbuy_ctx:" + user["key"], [ctx])
                    mid = cb_msg.get("message_id")
                    kb = _stepper_keyboard(ctx["code"], cur, ctx["full"], ctx["price"])
                    if mid:
                        _edit_markup(chat_id, mid, kb)
                    print(f"[bot] {user['name']} [스테퍼] {cur}주")
                    continue
                if data.startswith("pset:"):
                    _p = data.split(":")
                    val = int(_p[2]) if len(_p) > 2 and _p[2].isdigit() else 0
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp or val < 1:
                        continue
                    ctx = ctxp[0]
                    hi = ctx["full"] if ctx.get("full", 0) >= 1 else val
                    val = max(1, min(val, hi))
                    ctx["sel"] = val
                    _set_pending("pbuy_ctx:" + user["key"], [ctx])
                    mid = cb_msg.get("message_id")
                    kb = _stepper_keyboard(ctx["code"], val, ctx["full"],
                                           ctx["price"], half=ctx.get("half", 0))
                    if mid:
                        _edit_markup(chat_id, mid, kb)
                    print(f"[bot] {user['name']} [프리셋] {val}주")
                    continue
                if data == "noop":
                    continue
                if data.startswith("pbuy_go:"):
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp:
                        notify._send_raw(chat_id, "매수 대기가 만료됐어요. 다시 /추가 하세요.")
                        continue
                    ctx = ctxp[0]
                    sel = max(0, int(ctx.get("sel", 0) or 0))
                    amt = sel * ctx["price"]
                    if sel >= 1:
                        print(f"[bot] {user['name']} [버튼] 일시매수 확정: {ctx['name']} {sel}주")
                    else:
                        print(f"[bot] {user['name']} [버튼] 일시매수 0주(안함) → 분할설정")
                    # 일시매수 확정 저장, 분할 설정 초기화 (같은 ctx 재사용)
                    ctx["onetime"] = sel
                    ctx["split_qty"] = 1
                    ctx["every"] = "daily"
                    _set_pending("pbuy_ctx:" + user["key"], [ctx])
                    _onetime_line = (f"일시매수 {sel}주 ({amt:,}원) 확정.\n"
                                     if sel >= 1 else "일시매수 없음 (분할만).\n")
                    body = (f"✅ (모의) {ctx['name']}({ctx['code']}) "
                            + _onetime_line +
                            f"────────\n"
                            f"📊 분할매수 설정\n"
                            f"주기당 살 수량과 주기를 고르세요.")
                    kb = _split_keyboard(ctx["code"], 1, sel, every="daily")
                    notify._send_raw(chat_id, body, reply_markup=kb)
                    continue
                if data.startswith("sstep:"):
                    _p = data.split(":")
                    delta = int(_p[2]) if len(_p) > 2 and _p[2].lstrip("-").isdigit() else 0
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp:
                        continue
                    ctx = ctxp[0]
                    q = max(1, (ctx.get("split_qty", 1)) + delta)
                    ctx["split_qty"] = q
                    _set_pending("pbuy_ctx:" + user["key"], [ctx])
                    mid = cb_msg.get("message_id")
                    kb = _split_keyboard(ctx["code"], q, ctx.get("onetime", 0),
                                         every=ctx.get("every", "daily"))
                    if mid:
                        _edit_markup(chat_id, mid, kb)
                    continue
                if data.startswith("sfreq:"):
                    _p = data.split(":")
                    every = _p[2] if len(_p) > 2 else "daily"
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp:
                        continue
                    ctx = ctxp[0]
                    ctx["every"] = every
                    _set_pending("pbuy_ctx:" + user["key"], [ctx])
                    mid = cb_msg.get("message_id")
                    kb = _split_keyboard(ctx["code"], ctx.get("split_qty", 1),
                                         ctx.get("onetime", 0), every=every)
                    if mid:
                        _edit_markup(chat_id, mid, kb)
                    print(f"[bot] {user['name']} [분할주기] {every}")
                    continue
                if data.startswith("ssave:"):
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp:
                        continue
                    ctx = ctxp[0]
                    plan = {"onetime": ctx.get("onetime", 0),
                            "split": {"every": ctx.get("every", "daily"),
                                      "qty": ctx.get("split_qty", 1)}}
                    freq_ko = {"daily": "매일", "weekly": "매주",
                               "biweekly": "격주", "monthly": "매월"}.get(
                        plan["split"]["every"], plan["split"]["every"])
                    # 종목이 아직 free_holdings에 없으면 먼저 등록 (모의 흐름)
                    if _name_by_code(user, ctx["code"]) is None:
                        _cmd_confirm_add(user)
                    result = _save_buy_plan(user, ctx["code"], ctx["name"], plan)
                    _set_pending("pbuy_ctx:" + user["key"], None)
                    _set_pending("add:" + user["key"], None)
                    print(f"[bot] {user['name']} [분할매수 저장] {ctx['name']} "
                          f"일시{plan['onetime']} +{freq_ko}{plan['split']['qty']}주")
                    notify._send_raw(chat_id,
                        f"✅ (모의) {ctx['name']} 매수계획 저장\n"
                        f"  일시매수 {plan['onetime']}주\n"
                        f"  분할매수 {freq_ko} {plan['split']['qty']}주씩\n" + result)
                    continue
                if data.startswith("snone:"):
                    ctxp = _get_pending("pbuy_ctx:" + user["key"])
                    if not ctxp:
                        continue
                    ctx = ctxp[0]
                    _onetime = int(ctx.get("onetime", 0) or 0)
                    # 방어: 일시매수 0주 + 분할 안 함 = 빈 계획 → 저장 안 하고 등록만
                    if _onetime < 1:
                        if _name_by_code(user, ctx["code"]) is None:
                            _cmd_confirm_add(user)
                        _set_pending("pbuy_ctx:" + user["key"], None)
                        _set_pending("add:" + user["key"], None)
                        print(f"[bot] {user['name']} [빈 계획 방어] {ctx['name']} 등록만")
                        notify._send_raw(chat_id,
                            f"⚠️ {ctx['name']}: 일시매수 0주 + 분할 안 함이라 "
                            f"매수 계획이 없어요.\n종목은 등록만 했어요 "
                            f"(나중에 /승인 으로 매수 계획을 넣을 수 있어요).")
                        continue
                    plan = {"onetime": _onetime}
                    if _name_by_code(user, ctx["code"]) is None:
                        _cmd_confirm_add(user)
                    result = _save_buy_plan(user, ctx["code"], ctx["name"], plan)
                    _set_pending("pbuy_ctx:" + user["key"], None)
                    _set_pending("add:" + user["key"], None)
                    print(f"[bot] {user['name']} [분할 안함] {ctx['name']} 일시{plan['onetime']}주")
                    notify._send_raw(chat_id,
                        f"✅ (모의) {ctx['name']} 일시매수 {plan['onetime']}주만 저장 "
                        f"(분할 안 함)\n" + result)
                    continue
                if data.startswith("pbuy_cancel:"):
                    reply = _cmd_confirm_add(user)
                    _set_pending("pbuy_ctx:" + user["key"], None)
                    print(f"[bot] {user['name']} [버튼] 등록만(매수취소)")
                    notify._send_raw(chat_id, "등록만 했어요 (매수 안 함).\n" + reply)
                    continue
                if data == "reboot_yes":
                    if user.get("role") != "owner":
                        notify._send_raw(chat_id, "재부팅은 owner만 가능해요.")
                        continue
                    print(f"[bot] {user['name']} [버튼] 재부팅")
                    notify._send_raw(chat_id, _do_reboot())
                    continue
                if data == "reboot_no":
                    notify._send_raw(chat_id, "재부팅 취소했어요.")
                    continue
                if data.startswith("reject:"):
                    code = data.split(":", 1)[1]
                    print(f"[bot] {user['name']}({user['role']}) [버튼] 취소(삭제): {code}")
                    reply = _delete_holding(user, code)
                    notify._send_raw(chat_id, reply)
                    continue
                text = {"yes": "예", "no": "아니요"}.get(data, data)
                print(f"[bot] {user['name']}({user['role']}) [버튼]: {text}")
                reply = handle_command(user, text)
                body, markup = _split_marker(reply)
                notify._send_raw(chat_id, body, reply_markup=markup)
                continue
    
            # --- 일반 메시지 처리 ---
            msg = up.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id", "")).strip()
            text = msg.get("text", "")
            doc = msg.get("document")
            if not chat_id:
                continue
            # 화이트리스트 확인
            if chat_id not in users:
                print(f"[bot] 미등록 chat_id={chat_id} 거부")
                notify._send_raw(chat_id, "등록되지 않은 사용자입니다.")
                continue
            user = users[chat_id]
            # 파일 첨부 처리 (분류 → 저장)
            if doc:
                _save_document(user, doc.get("file_name", ""), doc.get("file_id", ""), chat_id)
                continue
            # 텍스트 없으면 스킵
            if not text:
                continue
            print(f"[bot] {user['name']}({user['role']}): {text}")
            reply = handle_command(user, text)
            body, markup = _split_marker(reply)
            notify._send_raw(chat_id, body, reply_markup=markup)
    
    
        except Exception as _e:
            print(f"[bot] update 처리 오류(무시하고 계속): {_e}")
            import traceback; traceback.print_exc()
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
