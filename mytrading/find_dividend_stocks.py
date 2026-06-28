"""
배당주 3조건 스크리너 — 종목들이 배당주 조건을 만족하는지 판정.

조건 (config order_pace 옆 dividend_filter, 없으면 기본값):
  1. 시가배당률 >= rate_threshold (%)   — 금리보다 높은 배당
  2. 영업이익 흑자 지속 (위기연도 제외)        — 본업 꾸준
  3. 부채비율 <= debt_max (%)           — 재무 안정

데이터: finance_data(재무·배당) + data_manager(현재가).
⚠️ 재무 API 실전 전용 → KIS_MODE=prod 로 실행.

실행:
  KIS_MODE=prod uv run python mytrading/find_dividend_stocks.py 049720 009680 005930
  KIS_MODE=prod uv run python mytrading/find_dividend_stocks.py --universe   # universe 전체
  KIS_MODE=prod uv run python mytrading/find_dividend_stocks.py 종목들 --add  # 통과종목 자동추가(Waiting)
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mytrading.common import init, CONFIG
from mytrading.finance_data import get_financials, get_dividend_yield

try:
    from mytrading.data_manager import get_52w_range
except Exception:
    get_52w_range = None


# 기본 임계값 (config 의 dividend_filter 로 덮어쓰기 가능)
_DEFAULTS = {
    "rate_threshold": 3.0,   # 시가배당률 최소 %
    "debt_max": 100.0,       # 부채비율 최대 %
    "profit_years": 5,       # 영업이익 흑자 봐야 하는 최근 년 수
    "exclude_years": ["2008", "2020"],  # 위기 연도 (영업이익 판단서 제외)
    "payout_max": 150.0,     # 배당성향 상한 % (초과는 데이터오류·특별배당 의심)
}


def _filter_cfg(market: str = None) -> dict:
    """배당주 필터 기준.
    market 없으면: 수동 검사 기본값 (dividend_filter 최상위).
    market 있으면(kospi/kosdaq): 공통(exclude_years 등) + 시장별 기준 병합.
    """
    cfg = CONFIG.get("dividend_filter", {}) or {}
    out = dict(_DEFAULTS)
    # 공통/수동 기본값 (최상위 키)
    for k in ("rate_threshold", "debt_max", "profit_years", "exclude_years", "payout_max"):
        if k in cfg:
            out[k] = cfg[k]
    # 시장별 덮어쓰기 (전체 스캔)
    if market and market in cfg and isinstance(cfg[market], dict):
        mk = cfg[market]
        for k in ("rate_threshold", "debt_max", "profit_years",
                  "min_volume", "top_n"):
            if k in mk:
                out[k] = mk[k]
    return out


def _current_price(symbol: str) -> float:
    """현재가 조회. inquire_price(실시간) 우선, 실패 시 52주 캐시 종가.
    배당주 스캔은 universe 밖 종목도 보므로 캐시 없을 수 있음 → inquire_price 직접 사용."""
    # 1순위: inquire_price (캐시 불필요, 실시간)
    try:
        ip_dir = _REPO_ROOT / "examples_llm" / "domestic_stock" / "inquire_price"
        if str(ip_dir) not in sys.path:
            sys.path.insert(0, str(ip_dir))
        import inquire_price as _ip
        df = _ip.inquire_price(env_dv="real", fid_cond_mrkt_div_code="J",
                               fid_input_iscd=symbol)
        if df is not None and not df.empty:
            v = df.iloc[0].get("stck_prpr", None)  # 주식 현재가
            if v not in (None, "", "0"):
                return float(str(v).replace(",", ""))
    except Exception:
        pass
    # 2순위: 52주 캐시 종가 (universe 종목은 캐시 있음)
    if get_52w_range is not None:
        try:
            r = get_52w_range(symbol)
            if r:
                return float(r["last"])
        except Exception:
            pass
    return 0.0


def screen(symbol: str, cfg: dict) -> dict:
    """한 종목 배당주 판정. 반환: {symbol, pass, dividend, debt, op_ok, reasons, ...}

    조건 (차영석 3조건):
      1. 시가배당률 >= rate_threshold (금리보다 높은 배당)
      2. 영업이익 꾸준 흑자 (위기연도 exclude_years 제외)
      3. 부채비율 <= debt_max
    """
    from mytrading.finance_data import get_operating_profit
    price = _current_price(symbol)
    fin = get_financials(symbol, years=cfg["profit_years"])
    dy = get_dividend_yield(symbol, price) if price > 0 else None
    op = get_operating_profit(symbol, years=cfg["profit_years"],
                              exclude_years=cfg.get("exclude_years", []))

    div_yield = dy["yield_pct"] if dy else 0.0
    debt = fin["debt_ratio"] if fin else None
    op_ok = op["all_positive"] if op else False
    op_pos = op["positive_years"] if op else 0
    op_chk = op["checked_years"] if op else 0

    # 배당성향 교차검증 (데이터 오류 탐지)
    # 배당성향 = 주당배당금 ÷ EPS. 정상은 0~100%(여유두어 payout_max).
    # KIS face_val 오류로 배당금이 N배 부풀려지면 배당성향이 튀어 잡힘.
    # (EPS 는 face_val 영향 안 받아 정확 → 교차검증 가능)
    eps = (fin.get("annual_eps") or fin.get("eps")) if fin else None
    annual_div = dy["annual_dividend"] if dy else 0.0
    payout = None
    payout_ok = True   # EPS 없거나 적자면 검증 스킵(통과 취급)
    payout_max = cfg.get("payout_max", 150.0)
    if eps and eps > 0 and annual_div > 0:
        payout = annual_div / eps * 100
        payout_ok = (payout <= payout_max)

    # 조건 판정
    c1 = div_yield >= cfg["rate_threshold"]
    c2 = op_ok   # 영업이익 흑자 지속 (위기 제외)
    c3 = (debt is not None) and (debt <= cfg["debt_max"])
    c4 = payout_ok   # 배당성향 정상 (데이터 오류 아님)
    passed = c1 and c2 and c3 and c4

    reasons = []
    reasons.append(f"배당 {div_yield:.2f}%{'≥' if c1 else '<'}{cfg['rate_threshold']:.0f}% {'✓' if c1 else '✗'}")
    reasons.append(f"영업이익흑자 {op_pos}/{op_chk}년(위기제외) {'✓' if c2 else '✗'}")
    dtxt = f"{debt:.1f}%" if debt is not None else "?"
    reasons.append(f"부채 {dtxt}{'≤' if c3 else '>'}{cfg['debt_max']:.0f}% {'✓' if c3 else '✗'}")
    if payout is not None:
        reasons.append(f"배당성향 {payout:.0f}%{'≤' if c4 else '>'}{payout_max:.0f}% {'✓' if c4 else '✗(데이터의심)'}")

    return {
        "symbol": symbol, "price": price,
        "dividend": div_yield, "debt": debt,
        "op_ok": op_ok, "op_years": f"{op_pos}/{op_chk}",
        "pass": passed, "reasons": reasons,
    }


def _existing_codes() -> set:
    """universe 에 이미 있는 모든 code (상태 무관).
    중복·Rejected 재추천 방지용 — 한 번 등장한 code 는 다시 안 넣음."""
    from mytrading.portfolio import load_portfolio
    pf = load_portfolio()
    codes = set()
    for cat in ("aggressive", "moderate", "safe"):
        for s in pf.names(cat):
            codes.add(str(s.get("code", "")).strip())
    return codes


def _stock_name(symbol: str) -> str:
    """종목명 조회 (find_stock_code 의 마스터). 실패 시 빈 문자열."""
    try:
        from mytrading.find_stock_code import download_master, parse_master
        for mkt in ("kospi", "kosdaq"):
            for code6, _std, name in parse_master(download_master(mkt)):
                if code6 == symbol:
                    return name
    except Exception:
        pass
    return ""


def add_to_universe(results: list, uni_path: Path = None) -> int:
    """
    배당주 통과 종목을 universe.yaml 맨 끝(moderate 아래)에 append.
    - confirm: "Waiting" (사람 승인 전까지 매매 안 함)
    - added_by: "AI", added_date: 오늘, note: 발견 당시 배당률·부채비율
    - 이미 universe 에 있는 code(Approval/Paused/Waiting/Rejected 무관)는 건너뜀.
      → Rejected 종목 재추천 방지, 중복 방지.
    반환: 실제 추가한 종목 수.
    ※ universe.yaml 은 moderate 가 맨 끝에 있어야 함 (append 가 moderate 에 붙음).
    """
    from datetime import date
    if uni_path is None:
        uni_path = _REPO_ROOT / "mytrading" / "universe.yaml"

    existing = _existing_codes()
    today = date.today().strftime("%Y-%m-%d")

    to_add = []
    skipped = []
    for r in results:
        if not r.get("pass"):
            continue
        code = r["symbol"]
        if code in existing:
            skipped.append(code)
            continue
        name = _stock_name(code) or code
        debt = r.get("debt")
        debt_s = f"{debt:.0f}%" if debt is not None else "?"
        note = f"배당 {r['dividend']:.1f}% 부채 {debt_s} (필터통과)"
        line = (f'  - {{ code: "{code}", name: "{name}", style: "value_range", '
                f'added_by: "AI", confirm: "Waiting", added_date: "{today}", '
                f'note: "{note}" }}\n')
        to_add.append((code, name, line))

    if to_add:
        # 파일이 줄바꿈으로 끝나는지 확인 — 마지막 줄에 \n 없으면 먼저 추가
        # (없으면 기존 마지막 줄에 새 항목이 붙어 yaml 깨짐)
        need_newline = False
        try:
            with open(uni_path, "rb") as f:
                f.seek(-1, 2)  # 파일 끝 1바이트
                if f.read(1) != b"\n":
                    need_newline = True
        except Exception:
            need_newline = True  # 빈 파일 등은 그냥 진행
        with open(uni_path, "a", encoding="utf-8") as f:
            if need_newline:
                f.write("\n")
            for _code, _name, line in to_add:
                f.write(line)

    # 결과 출력
    if to_add:
        print(f"\n✅ universe 에 {len(to_add)}개 추가 (confirm: Waiting):")
        for code, name, _ in to_add:
            print(f"    {code} {name}")
        print("  → Owner 가 거래량·백테스트 확인 후 confirm 을 Approval 로 바꾸면 매매 시작.")
    if skipped:
        print(f"  건너뜀(이미 등록/Rejected): {', '.join(skipped)}")
    return len(to_add)


def main():
    args = sys.argv[1:]
    init(require_confirm=False)
    cfg = _filter_cfg()

    if "--universe" in args:
        from mytrading.portfolio import load_portfolio
        pf = load_portfolio()
        codes = pf.symbols()  # universe 전체
    else:
        codes = [a for a in args if not a.startswith("--")]

    if not codes:
        print("사용: find_dividend_stocks.py 049720 009680 ...  또는  --universe")
        print(f"기준: 배당률≥{cfg['rate_threshold']}% / 영업이익흑자 {cfg["profit_years"]}년 / 부채≤{cfg['debt_max']}%")
        return

    print(f"배당주 기준: 시가배당률≥{cfg['rate_threshold']:.0f}% · "
          f"영업이익흑자 최근{cfg["profit_years"]}년 · 부채비율≤{cfg['debt_max']:.0f}%")
    print("=" * 72)

    results = []
    for code in codes:
        try:
            r = screen(code, cfg)
            mark = "✅ 배당주" if r["pass"] else "❌"
            print(f"{code}  {mark}")
            print(f"    {' | '.join(r['reasons'])}")
            results.append(r)
        except Exception as e:
            print(f"{code}  오류: {str(e)[:50]}")

    passed = [r for r in results if r["pass"]]
    if passed:
        print("\n" + "=" * 72)
        print(f"배당주 후보 {len(passed)}개:",
              ", ".join(f"{r['symbol']}(배당{r['dividend']:.1f}%)" for r in passed))
        # --add: universe 에 자동 추가 (confirm: Waiting)
        if "--add" in args:
            add_to_universe(results)
        else:
            print("※ 후보일 뿐 — 거래량(find_stock_code --vol)·백테스트 확인 후 universe 추가.")
            print("  자동 추가하려면 --add 옵션 (confirm: Waiting 으로 들어감).")
    elif results:
        print("\n통과한 배당주 없음.")


if __name__ == "__main__":
    main()