"""
배당(moderate) 종목 스코어링 — 시가총액·배당률·부채비율 순위 기반 100점 만점 3항목
+ auto_confirm(자동 판정) 산출.

universe_ko.yaml 의 moderate 종목 전체를 순회하며
  시가총액 / 배당률(배당액÷현재가) / 부채비율
세 항목을 각각 순위 기반으로 채점(1등=100, 꼴등=0)해 합산(0~300)한다.

배당률 0%(최근 1년 배당 이력 없음)인 종목은 순위 계산에서 제외하고
auto_confirm="Rejected"로 따로 처리한다(배당주 취지에 안 맞으므로).
남은(배당 있는) 종목만으로 총점 순위를 다시 매겨 상위 100 = auto_confirm="Approval",
101등 이하 = auto_confirm="Paused" 로 판정한다.

⚠️ 기본은 --dry-run 이다. 점수·auto_confirm 예정값만 출력하고 universe_ko.yaml 은
   건드리지 않는다. --save 를 붙여야 실제로 파일에 저장한다(ruamel round-trip, 주석·구조 보존).
   confirm(사람) 필드는 이 스크립트가 절대 건드리지 않는다 — auto_confirm 만 쓴다.

scan_dividend.py 와는 별개다 — scan_dividend 는 "새 후보 발굴"(기존 종목 스킵),
이 스크립트는 "이미 있는 종목 재채점"(전체 순회)이라 대상 집합이 반대다.

사용:
    KIS_MODE=prod uv run python mytrading/score_dividend.py                # dry-run, 전체
    KIS_MODE=prod uv run python mytrading/score_dividend.py --limit 10     # 앞 10종목만 시험
    KIS_MODE=prod uv run python mytrading/score_dividend.py --sleep 1.0    # 종목간 대기 늘림
    KIS_MODE=prod uv run python mytrading/score_dividend.py --save         # 실제 저장
    KIS_MODE=prod uv run python mytrading/score_dividend.py --observe      # 관찰 로그만 기록

주의:
  - 종목당 KIS API 3콜(+DART 폴백 시 추가). 속도제한(EGW00201) 대비 종목간 sleep,
    실패 시 1회 재시도. 그래도 실패하면 그 종목은 채점 제외(auto_confirm 미정, None).
  - --save 는 mytrading/configs/universe_ko.yaml 을 직접 수정한다. 반드시 --dry-run 결과를
    먼저 확인한 뒤 사용할 것. confirm 필드는 절대 안 씀 — auto_confirm/score 계열만 기록.
  - --observe 는 universe_ko.yaml 을 절대 건드리지 않는다(--save 와 같이 줘도 저장 안 함 —
    관찰이 우선). 대신 ~/dividend_score_log/YYYY-MM-DD.json 에 그날 순위·점수·auto_confirm
    예정값(특히 90~110등 경계 구간)을 기록한다. hysteresis 밴드를 정하기 전 일일 변동폭을
    쌓아두기 위한 순수 관찰 모드 — cron 으로 매 거래일 돌리는 걸 전제로 한다.
"""
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]  # 저장소 루트
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

UNIVERSE_PATH = _ROOT / "mytrading" / "configs" / "universe_ko.yaml"
TOP_N = 100  # 상위 N 종목까지 auto_confirm="Approval"
OBSERVE_LOG_DIR = Path.home() / "dividend_score_log"
OBSERVE_BOUNDARY_LO = 90
OBSERVE_BOUNDARY_HI = 110

# 저장 시 보존할 ruamel round-trip 설정 (telegram_bot.py 와 동일 패턴 — 주석·따옴표·순서 보존)
from ruamel.yaml import YAML as _YAML  # noqa: E402
_RT = _YAML()
_RT.preserve_quotes = True
_RT.indent(mapping=2, sequence=4, offset=2)
_RT.allow_unicode = True


def _rt_load(path: Path):
    with open(path, encoding="utf-8") as f:
        return _RT.load(f) or {}


def _rt_dump(data, path: Path):
    with open(path, "w", encoding="utf-8") as f:
        _RT.dump(data, f)


def _retry(fn, retries: int = 1, pause: float = 0.5):
    """fn() 을 실행. 예외 또는 None 반환 시 pause 후 retries 번까지 재시도."""
    for attempt in range(retries + 1):
        try:
            result = fn()
            if result is not None:
                return result
        except Exception:
            pass
        if attempt < retries:
            time.sleep(pause)
    return None


def load_moderate():
    """universe_ko.yaml 의 moderate 종목 전체 (raw, confirm 무관)."""
    import yaml
    data = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8")) or {}
    out = []
    for it in (data.get("moderate") or []):
        if isinstance(it, dict) and str(it.get("code", "")).strip():
            out.append({
                "code": str(it["code"]).strip(),
                "name": it.get("name", ""),
                "confirm": it.get("confirm", ""),
            })
    return out


def collect_one(code: str):
    """종목 1개의 (시가총액 억원, 배당률 %, 부채비율 %) 조회. 실패 항목은 None."""
    from mytrading.find_stock_code import _market_cap
    from mytrading.find_dividend_stocks import _current_price
    from mytrading.finance_data import get_dividend_yield
    from mytrading.finance_unified import get_financials_safe

    mcap = _retry(lambda: _market_cap(code))

    price = _retry(lambda: (_current_price(code) or None))

    div_yield = None
    if price:
        def _div():
            dy = get_dividend_yield(code, price=float(price))
            return dy.get("yield_pct") if dy and dy.get("yield_pct") is not None else None
        div_yield = _retry(_div)

    def _debt():
        fin = get_financials_safe(code, init_kis=False)
        return fin.get("debt_ratio") if fin and fin.get("debt_ratio") is not None else None
    debt_ratio = _retry(_debt)

    return mcap, div_yield, debt_ratio


def categorize(rows):
    """rows 를 세 그룹으로 분류.
    - fetch_failed : mcap/div/debt 중 하나라도 조회 실패(None) → 채점 불가, auto_confirm 미정
    - div_zero     : 세 값 다 있지만 배당률 0% → auto_confirm 예정 "Rejected", 순위 계산 제외
    - rankable     : 배당률 > 0 이고 세 값 다 있음 → 순위·점수 계산 대상
    """
    fetch_failed, div_zero, rankable = [], [], []
    for r in rows:
        if r["mcap"] is None or r["div"] is None or r["debt"] is None:
            fetch_failed.append(r)
        elif r["div"] == 0:
            div_zero.append(r)
        else:
            rankable.append(r)
    return rankable, div_zero, fetch_failed


def _rank_score(values, reverse: bool):
    """values: [(idx, value), ...] value 로 정렬해 순위 기반 점수(1등=100, 꼴등=0) 산출.
    반환: {idx: score}"""
    n = len(values)
    if n <= 1:
        return {idx: 100.0 for idx, _ in values}
    ordered = sorted(values, key=lambda x: x[1], reverse=reverse)
    scores = {}
    for rank, (idx, _v) in enumerate(ordered, start=1):  # 1등부터
        scores[idx] = 100.0 * (n - rank) / (n - 1)
    return scores


def compute_scores(rows):
    """rows: 전부 mcap/div/debt 유효값 보유(호출 전 categorize 로 걸러진 상태 가정).
    순위 기반 3항목 채점 후 총점 내림차순 정렬해 반환."""
    idxs = list(range(len(rows)))
    mcap_vals = [(i, rows[i]["mcap"]) for i in idxs]
    div_vals = [(i, rows[i]["div"]) for i in idxs]
    debt_vals = [(i, rows[i]["debt"]) for i in idxs]

    mcap_scores = _rank_score(mcap_vals, reverse=True)   # 큰 시총이 좋음
    div_scores = _rank_score(div_vals, reverse=True)     # 높은 배당률이 좋음
    debt_scores = _rank_score(debt_vals, reverse=False)  # 낮은 부채비율이 좋음

    scored = []
    for i in idxs:
        r = rows[i]
        ms, ds, dbs = mcap_scores[i], div_scores[i], debt_scores[i]
        scored.append({
            **r,
            "mcap_score": round(ms, 1),
            "div_score": round(ds, 1),
            "debt_score": round(dbs, 1),
            "total": round(ms + ds + dbs, 1),
        })
    scored.sort(key=lambda r: r["total"], reverse=True)
    return scored


def assign_auto_confirm(scored, div_zero, fetch_failed, top_n=TOP_N):
    """scored(배당>0, 순위 매겨진 상태)에 rank/auto_confirm 부여.
    div_zero → Rejected, fetch_failed → None(미정). confirm(사람) 필드는 안 건드림."""
    for i, r in enumerate(scored, start=1):
        r["rank"] = i
        r["auto_confirm"] = "Approval" if i <= top_n else "Paused"
    for r in div_zero:
        r["auto_confirm"] = "Rejected"
    for r in fetch_failed:
        r["auto_confirm"] = None


def print_table(scored, top_n_marker=TOP_N):
    n = len(scored)
    print("=" * 112)
    print(f"  배당(moderate) 순위 — 배당 있는 {n}종목 대상 (auto_confirm 예정값 포함)")
    print("=" * 112)
    print(f"{'순위':>4} {'auto_confirm':>12} {'종목명(코드)':22} {'시총점':>7} {'배당점':>7} {'부채점':>7} {'총점':>7}"
          f"   {'시총(억)':>10} {'배당률%':>8} {'부채율%':>8}")
    print("-" * 112)
    for r in scored:
        rank = r["rank"]
        if rank == top_n_marker + 1:
            print("-" * 48 + f" ↑ 상위 {top_n_marker} 경계선 (Approval/Paused) " + "-" * 24)
        label = f"{r['name']}({r['code']})"
        print(f"{rank:>4} {r['auto_confirm']:>12} {label:22} {r['mcap_score']:>7.1f} {r['div_score']:>7.1f} "
              f"{r['debt_score']:>7.1f} {r['total']:>7.1f}"
              f"   {r['mcap']:>10,.0f} {r['div']:>8.2f} {r['debt']:>8.2f}")
    print("=" * 112)


def print_boundary_zone(scored, top_n=TOP_N, span=5):
    lo, hi = max(1, top_n - span), min(len(scored), top_n + span)
    print(f"\n[경계 근처 종목 — {lo}~{hi}등, 상위 {top_n} 경계 팽팽함 확인용]")
    print(f"{'순위':>4} {'auto_confirm':>12} {'종목명(코드)':22} {'총점':>7}   {'배당률%':>8} {'부채율%':>8}")
    for r in scored:
        if lo <= r["rank"] <= hi:
            mark = " ←경계" if r["rank"] == top_n else ("  " if r["rank"] == top_n + 1 else "")
            label = f"{r['name']}({r['code']})"
            print(f"{r['rank']:>4} {r['auto_confirm']:>12} {label:22} {r['total']:>7.1f}"
                  f"   {r['div']:>8.2f} {r['debt']:>8.2f}{mark}")


def print_summary(scored, div_zero, fetch_failed):
    n_approval = sum(1 for r in scored if r["auto_confirm"] == "Approval")
    n_paused = sum(1 for r in scored if r["auto_confirm"] == "Paused")
    print(f"\n[auto_confirm 분포 예정]")
    print(f"  Approval(상위 {TOP_N}) : {n_approval}종목")
    print(f"  Paused({TOP_N + 1}등↓)   : {n_paused}종목")
    print(f"  Rejected(배당 0%)   : {len(div_zero)}종목")
    print(f"  미정(데이터 조회 실패): {len(fetch_failed)}종목")

    if div_zero:
        print(f"\n[Rejected 예정 — 배당률 0%, {len(div_zero)}종목]")
        for r in div_zero:
            print(f"    {r['name']}({r['code']}) — 시총 {r['mcap']:,.0f}억, "
                  f"부채율 {r['debt']:.2f}% (배당 0%라 순위 계산에서 제외됨)")

    if fetch_failed:
        print(f"\n[미정 — 데이터 조회 실패, {len(fetch_failed)}종목]")
        for r in fetch_failed:
            missing = []
            if r["mcap"] is None:
                missing.append("시총")
            if r["div"] is None:
                missing.append("배당률")
            if r["debt"] is None:
                missing.append("부채비율")
            print(f"    {r['name']}({r['code']}) — 실패: {', '.join(missing)}")


def print_comparison(old_top100_codes, new_top100_codes, code_to_name):
    """배당0% 제외 전/후 상위 100 구성 변화."""
    dropped = old_top100_codes - new_top100_codes  # 이전엔 top100, 지금은 아님
    entered = new_top100_codes - old_top100_codes  # 이전엔 아님, 지금 top100
    print(f"\n[배당 0% 제외 전/후 — 상위 {TOP_N} 구성 변화]")
    print(f"  이전(배당0% 포함 전체 {len(old_top100_codes) + 0}종목 풀 기준) 상위 {TOP_N} "
          f"vs 이번(배당>0 {len(new_top100_codes)}종목 풀 기준) 상위 {TOP_N}")
    if dropped:
        print(f"  상위 {TOP_N}에서 빠진 종목({len(dropped)}개):")
        for code in dropped:
            print(f"    - {code_to_name.get(code, code)}({code})")
    if entered:
        print(f"  상위 {TOP_N}에 새로 들어온 종목({len(entered)}개):")
        for code in entered:
            print(f"    + {code_to_name.get(code, code)}({code})")
    if not dropped and not entered:
        print("  변화 없음 (동일 구성)")


def save_scores(scored, div_zero, fetch_failed):
    """universe_ko.yaml 의 moderate 항목에 score/mcap_score/div_score/debt_score/
    scored_date/auto_confirm 기록. confirm(사람) 필드는 절대 건드리지 않는다.
    ruamel round-trip 이라 주석·필드 순서·따옴표는 그대로 보존된다."""
    data = _rt_load(UNIVERSE_PATH)
    all_scored = list(scored) + list(div_zero)  # fetch_failed 는 auto_confirm=None, 기록 안 함
    by_code = {r["code"]: r for r in all_scored}
    today = date.today().isoformat()
    updated = 0
    for it in (data.get("moderate") or []):
        code = str(it.get("code", "")).strip()
        r = by_code.get(code)
        if r is None:
            continue
        it["auto_confirm"] = r["auto_confirm"]
        if "total" in r:  # div_zero 종목은 점수 없음(Rejected 만)
            it["score"] = r["total"]
            it["mcap_score"] = r["mcap_score"]
            it["div_score"] = r["div_score"]
            it["debt_score"] = r["debt_score"]
        it["scored_date"] = today
        updated += 1
    _rt_dump(data, UNIVERSE_PATH)
    print(f"\n저장 완료 — {updated}종목에 auto_confirm/score 필드 기록 ({UNIVERSE_PATH}).")
    if fetch_failed:
        print(f"  (데이터 조회 실패 {len(fetch_failed)}종목은 그대로 둠 — auto_confirm 미기록)")
    print("  confirm(사람) 필드는 건드리지 않았습니다.")


def write_observation_log(scored, div_zero, fetch_failed,
                          lo: int = OBSERVE_BOUNDARY_LO, hi: int = OBSERVE_BOUNDARY_HI) -> Path:
    """그날 순위·점수·auto_confirm 예정값을 ~/dividend_score_log/YYYY-MM-DD.json 에 기록.
    universe_ko.yaml 은 절대 건드리지 않는다 — 순수 관찰·기록용.
    같은 날 여러 번 실행하면 그날 파일을 덮어쓴다(마지막 실행 결과만 남음)."""
    def _row(r):
        return {"rank": r.get("rank"), "code": r["code"], "name": r["name"],
                "auto_confirm": r.get("auto_confirm"), "total": r.get("total"),
                "mcap_score": r.get("mcap_score"), "div_score": r.get("div_score"),
                "debt_score": r.get("debt_score"),
                "mcap": r.get("mcap"), "div": r.get("div"), "debt": r.get("debt")}

    today = date.today().isoformat()
    payload = {
        "date": today,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "n_total": len(scored) + len(div_zero) + len(fetch_failed),
        "n_rankable": len(scored),
        "n_approval": sum(1 for r in scored if r["auto_confirm"] == "Approval"),
        "n_paused": sum(1 for r in scored if r["auto_confirm"] == "Paused"),
        "n_rejected": len(div_zero),
        "n_fetch_failed": len(fetch_failed),
        "boundary_zone": [_row(r) for r in scored if lo <= r["rank"] <= hi],
        "full_ranking": [_row(r) for r in scored],
        "rejected": [{"code": r["code"], "name": r["name"],
                      "mcap": r["mcap"], "debt": r["debt"]} for r in div_zero],
        "fetch_failed": [{"code": r["code"], "name": r["name"]} for r in fetch_failed],
    }

    OBSERVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OBSERVE_LOG_DIR / f"{today}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return log_path


def main():
    args = sys.argv[1:]
    limit = None
    sleep_sec = 0.5
    save = "--save" in args
    observe = "--observe" in args
    if observe and save:
        print("⚠️ --observe 와 --save 를 같이 줬습니다 — 관찰이 우선이라 저장은 건너뜁니다.")
        save = False
    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            limit = int(args[i + 1])
    if "--sleep" in args:
        i = args.index("--sleep")
        if i + 1 < len(args):
            sleep_sec = float(args[i + 1])

    from mytrading.common import init
    init(require_confirm=False)

    stocks = load_moderate()
    if limit:
        stocks = stocks[:limit]

    print(f"조회 대상: {len(stocks)}종목 (sleep {sleep_sec}s/종목, 실패 시 1회 재시도)")
    mode_txt = "관찰 로그(--observe, universe_ko.yaml 안 건드림)" if observe else \
               ("저장(--save)" if save else "DRY-RUN (저장 안 함)")
    print(f"모드: {mode_txt}")
    print()

    rows = []
    for idx, s in enumerate(stocks, 1):
        code, name = s["code"], s["name"]
        mcap, div, debt = collect_one(code)
        rows.append({"code": code, "name": name, "confirm": s["confirm"],
                     "mcap": mcap, "div": div, "debt": debt})
        status = "OK" if (mcap is not None and div is not None and debt is not None) else "실패"
        print(f"  [{idx}/{len(stocks)}] {name}({code}) — {status}"
              f"  시총={mcap} 배당률={div} 부채={debt}", file=sys.stderr)
        if idx < len(stocks):
            time.sleep(sleep_sec)

    rankable, div_zero, fetch_failed = categorize(rows)
    scored = compute_scores(rankable)
    assign_auto_confirm(scored, div_zero, fetch_failed, top_n=TOP_N)

    # 비교용: 배당0% 도 포함해서 예전 방식(전체 유효종목 대상)으로도 채점 → 상위100 구성 비교
    old_valid = [r for r in rows if r["mcap"] is not None and r["div"] is not None
                 and r["debt"] is not None]
    old_scored = compute_scores(old_valid)
    old_top100 = {r["code"] for r in old_scored[:TOP_N]}
    new_top100 = {r["code"] for r in scored if r["rank"] <= TOP_N}
    code_to_name = {r["code"]: r["name"] for r in rows}

    print()
    print_table(scored)
    print_boundary_zone(scored)
    print_summary(scored, div_zero, fetch_failed)
    print_comparison(old_top100, new_top100, code_to_name)

    if observe:
        log_path = write_observation_log(scored, div_zero, fetch_failed)
        print(f"\n[관찰 로그 기록] {log_path}")
        print("  universe_ko.yaml 은 수정하지 않았습니다 — 순수 관찰·기록만 했습니다.")
    elif save:
        save_scores(scored, div_zero, fetch_failed)
    else:
        print("\n※ DRY-RUN — universe_ko.yaml 은 수정하지 않았습니다. "
              "confirm 필드는 애초에 이 스크립트가 건드리지 않습니다. "
              "저장하려면 --save 를 붙이세요.")


if __name__ == "__main__":
    main()
