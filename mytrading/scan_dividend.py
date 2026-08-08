"""
배당주 전체 스캔 배치 — 코스피/코스닥 시장 전체에서 배당주 후보 발견.

설계: DIVIDEND_FILTER_DESIGN.md 3-3.
  토요일 새벽: --market kospi
  일요일 새벽: --market kosdaq (소외 배당주 본진)

흐름:
  1. 마스터에서 시장 전체 종목 로드 (download_master/parse_master 재사용)
  2. [1차 압축] 거래량 하한 미달 제외 — 잡주 컷 (재무 API 호출 최소화)
  3. [2차] 남은 종목 배당 3조건 검사 (find_dividend_stocks.screen 재사용)
  4. 통과 → universe 에 confirm:Waiting 추가 (add_to_universe 재사용)
  5. 텔레그램 알림 (notify.send_message)

⚠️ 재무 API 실전(prod) 전용 → KIS_MODE=prod. 무거우니 주말 새벽 cron 권장.

실행:
  KIS_MODE=prod uv run python mytrading/scan_dividend.py --market kospi
  KIS_MODE=prod uv run python mytrading/scan_dividend.py --market kosdaq
  KIS_MODE=prod uv run python mytrading/scan_dividend.py --market kospi --limit 50   # 테스트(50개만)
  KIS_MODE=prod uv run python mytrading/scan_dividend.py --market kospi --dry-run     # 추가 안 함(판정만)
"""
import sys
import time
from pathlib import Path
from datetime import date, timedelta

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mytrading.common import init, CONFIG, get_data_provider
from mytrading.find_stock_code import download_master, parse_master
from mytrading.find_dividend_stocks import screen, _filter_cfg, add_to_universe, _existing_codes


# ETF 운용사 브랜드 (종목명 앞에 옴) — 국내 상장 ETF 전부 (해외추종 ETF 포함)
_ETF_BRANDS = (
    "KODEX", "TIGER", "RISE", "ACE", "SOL", "PLUS", "KBSTAR", "ARIRANG",
    "HANARO", "KOSEF", "TIMEFOLIO", "WOORI", "히어로즈", "마이티", "FOCUS",
    "BNK", "TREX", "KCGI", "VITA", "1Q", "DAISHIN343", "에셋플러스",
)
# 펀드·리츠·ETN 등 비사업회사 키워드 (종목명 어디든)
_NON_BIZ_KEYWORDS = ("리츠", "ETN", "ETF", "스팩", "인프라", "맥쿼리")


def _is_normal_stock(code: str, name: str = "") -> bool:
    """배당주 스캔 대상인 '일반 사업회사'인지 판정.
    제외 대상:
      - 6자리 숫자 코드 아님 (펀드·신탁: 70100026 같은 8자리)
      - ETF (KODEX·TIGER 등 운용사 브랜드) — 국내 상장 해외추종 ETF 포함
      - 리츠·ETN·스팩·인프라펀드 (사업회사 아님, 배당 성격 다름)
    ※ 리츠/ETF는 배당 구조가 사업회사와 달라(법적 배당의무·분배금) 3조건 부적합.
       ETF·채권은 universe safe 에 수동 관리하므로 스캔서 제외.
    """
    c = str(code).strip()
    if not (len(c) == 6 and c.isdigit()):
        return False
    nm = str(name).strip()
    # ETF 브랜드 (보통 이름 앞)
    for b in _ETF_BRANDS:
        if nm.startswith(b) or nm.startswith(b.upper()):
            return False
    # 리츠·ETN·스팩 등
    for kw in _NON_BIZ_KEYWORDS:
        if kw in nm:
            return False
    return True


def _recent_volume(dp, code: str) -> int:
    """최근 거래일 평균 거래량(주). 1차 압축용. 실패 시 0.
    휴장(거래량 0)은 제외하고 실제 거래일만 평균 → 주말·하루변동에 안흔들림."""
    try:
        end = date.today()
        start = end - timedelta(days=14)
        bars = dp.get_history(code, start, end)
        if bars:
            vols = [int(getattr(b, "volume", 0) or 0) for b in bars]
            vols = [v for v in vols if v > 0]
            if vols:
                return sum(vols) // len(vols)
    except Exception:
        pass
    return 0


def scan(market: str, limit: int = None, dry_run: bool = False) -> dict:
    """
    시장 전체 배당주 스캔.
    market: "kospi" / "kosdaq"
    limit: 검사할 종목 수 제한 (테스트용, None=전체)
    dry_run: True 면 universe 추가 안 함 (판정만)
    반환: {scanned, passed, added, candidates:[...]}
    """
    # 시장별 기준 (config dividend_filter.kospi/kosdaq)
    fcfg = _filter_cfg(market)
    min_vol = fcfg.get("min_volume", 10000)
    top_n = fcfg.get("top_n", 20)
    pause = (CONFIG.get("dividend_filter", {}) or {}).get("rate_limit_sec", 0.5)

    init(require_confirm=False)
    dp = get_data_provider()

    # 1. 마스터 로드
    rows = parse_master(download_master(market))
    print(f"[{market}] 마스터 {len(rows)}개 종목")

    # 이미 universe 에 있는 종목은 스캔에서 제외 (중복·Rejected)
    existing = _existing_codes()

    # 2. 1차 압축 (거래량 하한) + 2차 (배당 3조건)
    passed = []
    scanned = 0
    skipped_vol = 0
    skipped_nonstock = 0
    targets = rows if limit is None else rows[:limit]

    for i, (code6, _std, name) in enumerate(targets):
        # 일반 사업회사만 (펀드·신탁·ETF·리츠·ETN 제외)
        if not _is_normal_stock(code6, name):
            skipped_nonstock += 1
            continue
        if code6 in existing:
            continue  # 이미 등록/Rejected — 스캔 불필요

        # 1차: 거래량 컷
        vol = _recent_volume(dp, code6)
        time.sleep(pause)
        if vol < min_vol:
            skipped_vol += 1
            continue

        # 2차: 배당 3조건
        scanned += 1
        try:
            r = screen(code6, fcfg)
            time.sleep(pause)
            if r["pass"]:
                r["name"] = name
                passed.append(r)
                print(f"  ✅ {code6} {name}: 배당 {r['dividend']:.1f}% "
                      f"부채 {r['debt']:.0f}% (거래량 {vol:,})")
        except Exception as e:
            print(f"  ⚠️ {code6} {name}: 오류 {str(e)[:30]}")

        # 진행 로그 (100개마다)
        if (i + 1) % 100 == 0:
            print(f"  ... {i+1}/{len(targets)} 진행 (검사 {scanned}, 통과 {len(passed)})")

    # 배당률 높은 순 정렬 → 상위 top_n 개만 (검토량 조절)
    passed.sort(key=lambda r: r["dividend"], reverse=True)
    top = passed[:top_n]

    # 3. universe 추가 (상위 top_n 만)
    added = 0
    if top and not dry_run:
        added = add_to_universe(top)

    print(f"\n[{market}] 완료: 비주식 {skipped_nonstock}개 + 거래량컷 {skipped_vol}개 제외, "
          f"{scanned}개 검사, {len(passed)}개 통과 → 상위 {len(top)}개 추가 대상, {added}개 추가")

    return {
        "market": market, "scanned": scanned, "passed": len(passed),
        "added": added, "candidates": top, "skipped_vol": skipped_vol,
        "total_passed": len(passed),
    }


def _notify(result: dict):
    """스캔 결과 텔레그램 알림."""
    try:
        from mytrading.telegram.notify import send_message
    except Exception:
        return
    market = result["market"]
    mk = "코스피" if market == "kospi" else "코스닥"
    today = date.today().strftime("%m/%d")
    cand = result["candidates"]

    if cand:
        total = result.get("total_passed", len(cand))
        lines = [f"💰 <b>배당주 발견 — {mk}</b> ({today})",
                 f"통과 {total}개 중 상위 {result['added']}개 추가 (confirm: Waiting)", ""]
        for r in cand[:15]:  # 최대 15개
            lines.append(f"  {r['symbol']} {r.get('name', '')} 배당 {r['dividend']:.1f}% 부채 {r['debt']:.0f}%")
        lines.append("")
        lines.append("→ universe.yaml 에서 확인 후 Approval 로 승인하세요.")
        send_message("\n".join(lines))
    else:
        send_message(f"💰 <b>배당주 스캔 — {mk}</b> ({today})\n조건 통과 종목 없음.")


# ── 주당 1회 마커 (KIS 점검으로 토요일 막히면 일요일 보충, 중복 방지) ──
from pathlib import Path as _Path
from datetime import date as _date

_MARKER = _Path.home() / "KIS" / "cache" / "scan_marker.txt"

def _week_key(market: str) -> str:
    """올해-ISO주차-시장 (토·일은 같은 주차로 묶임). 예: 2026-W27-kospi"""
    y, w, _ = _date.today().isocalendar()
    return f"{y}-W{w:02d}-{market}"

def _already_scanned(market: str) -> bool:
    """이번 주 이 시장을 이미 스캔했는지."""
    if not _MARKER.exists():
        return False
    try:
        done = _MARKER.read_text(encoding="utf-8").split()
        return _week_key(market) in done
    except Exception:
        return False

def _mark_scanned(market: str):
    """이번 주 이 시장 스캔 완료 기록. 최근 8주만 유지."""
    try:
        _MARKER.parent.mkdir(parents=True, exist_ok=True)
        done = []
        if _MARKER.exists():
            done = _MARKER.read_text(encoding="utf-8").split()
        key = _week_key(market)
        if key not in done:
            done.append(key)
        done = done[-16:]
        _MARKER.write_text(" ".join(done), encoding="utf-8")
    except Exception:
        pass

def main():
    args = sys.argv[1:]
    market = "kospi"
    if "--market" in args:
        idx = args.index("--market")
        if idx + 1 < len(args):
            market = args[idx + 1]
    limit = None
    if "--limit" in args:
        idx = args.index("--limit")
        if idx + 1 < len(args):
            limit = int(args[idx + 1])
    dry_run = "--dry-run" in args
    no_notify = "--no-notify" in args

    if market not in ("kospi", "kosdaq"):
        print("사용: scan_dividend.py --market kospi|kosdaq [--limit N] [--dry-run] [--no-notify]")
        return

# 주당 1회 — 이번 주 이미 스캔했으면 건너뜀 (dry_run 제외)
    if not dry_run and _already_scanned(market):
        print(f"[{market}] 이번 주 이미 스캔 완료 — 건너뜀 ({_week_key(market)})")
        return

    result = scan(market, limit=limit, dry_run=dry_run)

    # 스캔 정상 완료 → 마커 기록 (dry_run 제외)
    if not dry_run:
        _mark_scanned(market)

    if not no_notify and not dry_run:
        _notify(result)

if __name__ == "__main__":
    main()