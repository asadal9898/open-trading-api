"""
배당주 3조건 스크리너 — 종목들이 배당주 조건을 만족하는지 판정.

조건 (config order_pace 옆 dividend_filter, 없으면 기본값):
  1. 시가배당률 >= rate_threshold (%)   — 금리보다 높은 배당
  2. ROE>0 최근 profit_years 년 모두    — 영업이익 꾸준 (흑자 지속)
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
    "profit_years": 5,       # ROE>0 이어야 하는 최근 년 수
}


def _filter_cfg() -> dict:
    cfg = CONFIG.get("dividend_filter", {}) or {}
    out = dict(_DEFAULTS)
    out.update({k: cfg[k] for k in _DEFAULTS if k in cfg})
    return out


def _current_price(symbol: str) -> float:
    """현재가 — 52주 range 의 last 종가 사용 (캐시 CSV)."""
    if get_52w_range is None:
        return 0.0
    r = get_52w_range(symbol)
    return float(r["last"]) if r else 0.0


def screen(symbol: str, cfg: dict) -> dict:
    """한 종목 배당주 판정. 반환: {symbol, pass, dividend, debt, roe_years, reasons, ...}"""
    price = _current_price(symbol)
    fin = get_financials(symbol, years=cfg["profit_years"])
    dy = get_dividend_yield(symbol, price) if price > 0 else None

    div_yield = dy["yield_pct"] if dy else 0.0
    debt = fin["debt_ratio"] if fin else None
    roe_pos = fin["roe_positive_years"] if fin else 0

    # 조건 판정
    c1 = div_yield >= cfg["rate_threshold"]
    c2 = roe_pos >= cfg["profit_years"]
    c3 = (debt is not None) and (debt <= cfg["debt_max"])
    passed = c1 and c2 and c3

    reasons = []
    reasons.append(f"배당 {div_yield:.2f}%{'≥' if c1 else '<'}{cfg['rate_threshold']:.0f}% {'✓' if c1 else '✗'}")
    reasons.append(f"ROE>0 {roe_pos}/{cfg['profit_years']}년 {'✓' if c2 else '✗'}")
    dtxt = f"{debt:.1f}%" if debt is not None else "?"
    reasons.append(f"부채 {dtxt}{'≤' if c3 else '>'}{cfg['debt_max']:.0f}% {'✓' if c3 else '✗'}")

    return {
        "symbol": symbol, "price": price,
        "dividend": div_yield, "debt": debt, "roe_years": roe_pos,
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
        note = f"배당 {r['dividend']:.1f}% 부채 {debt_s} (AI {today})"
        line = (f'  - {{ code: "{code}", name: "{name}", style: "value_range", '
                f'added_by: "AI", confirm: "Waiting", added_date: "{today}", '
                f'note: "{note}" }}\n')
        to_add.append((code, name, line))

    if to_add:
        with open(uni_path, "a", encoding="utf-8") as f:
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
        print(f"기준: 배당률≥{cfg['rate_threshold']}% / ROE>0 {cfg['profit_years']}년 / 부채≤{cfg['debt_max']}%")
        return

    print(f"배당주 기준: 시가배당률≥{cfg['rate_threshold']:.0f}% · "
          f"ROE>0 최근{cfg['profit_years']}년 · 부채비율≤{cfg['debt_max']:.0f}%")
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