"""
유니버스 value_range 종목 전수 판정.

universe.yaml 의 value_range 종목을 하나씩 평가해
  부채비율 / 영업이익 / 배당 / 국면 / 차트(52주 저점)
조건을 통과하는지 보고, 매수 후보인지 탈락인지 판정한다.

⚠️ 조회만 한다. 주문·파일 수정 없음.

사용:
    KIS_MODE=prod uv run python check_universe.py            # 전체
    KIS_MODE=prod uv run python check_universe.py --limit 10 # 앞 10종목만
    KIS_MODE=prod uv run python check_universe.py --pass-only # 통과 종목만 출력

주의:
  - 종목당 DART 호출이 많아 (국면 판정 30회) 전체 실행은 수 분 걸린다.
  - 레이트리밋 회피용 sleep 포함. --sleep 으로 조절.
"""
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]      # 저장소 루트
sys.path.insert(0, str(_ROOT))

import yaml

from mytrading.common import init


def load_universe_value_range():
    """universe.yaml 에서 value_range 종목 추출 (카테고리 무관)."""
    p = (_ROOT / "mytrading" / "universe.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for cat, items in data.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("style") != "value_range":
                continue
            out.append({
                "code": str(it.get("code", "")).strip(),
                "name": it.get("name", ""),
                "category": cat,
                "industry": it.get("industry"),
                "confirm": it.get("confirm", ""),
                "note": it.get("note", ""),
            })
    # 코드 기준 중복 제거 (같은 종목이 여러 카테고리에 있을 경우)
    seen, uniq = set(), []
    for it in out:
        if it["code"] and it["code"] not in seen:
            seen.add(it["code"])
            uniq.append(it)
    return uniq


def mark(ok):
    if ok is True:
        return "O"
    if ok is False:
        return "X"
    return "?"


def short(txt, n):
    if not txt:
        return ""
    txt = str(txt).replace("\n", " ")
    return txt[:n]


def main():
    args = sys.argv[1:]
    limit = None
    sleep_sec = 0.5
    pass_only = False
    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            limit = int(args[i + 1])
    if "--sleep" in args:
        i = args.index("--sleep")
        if i + 1 < len(args):
            sleep_sec = float(args[i + 1])
    if "--pass-only" in args:
        pass_only = True

    init(require_confirm=False)

    from mytrading.finance_data import evaluate_financials, _judge_phase, is_buyable_phase
    from mytrading.chart_eval import evaluate_chart

    stocks = load_universe_value_range()
    if limit:
        stocks = stocks[:limit]

    print("=" * 108)
    print(f"  유니버스 value_range 전수 판정 — {len(stocks)}종목")
    print("=" * 108)
    print(f"{'종목':14}{'코드':8}{'부채':>5}{'영익':>5}{'배당':>5}{'국면':>10}"
          f"{'차트':>5}{'판정':>8}  상세")
    print("-" * 108)

    stat = {"buy": 0, "fail_fin": 0, "fail_phase": 0, "fail_chart": 0, "error": 0}
    rows = []

    for idx, s in enumerate(stocks, 1):
        code, name = s["code"], s["name"]
        try:
            fin = evaluate_financials(code, style="value_range",
                                      industry=s.get("industry"))
            debt = (fin or {}).get("debt") or {}
            op = (fin or {}).get("op") or {}
            div = (fin or {}).get("dividend") or {}

            phase = op.get("phase")
            if phase is None:
                try:
                    phase = (_judge_phase(code) or {}).get("phase")
                except Exception:
                    phase = None
            phase_ok = is_buyable_phase(phase)

            cht = evaluate_chart(code, style="value_range")
            cht_ok = (cht or {}).get("passed")

            fin_ok = (fin or {}).get("passed")

            if not fin_ok:
                verdict, key = "탈락(재무)", "fail_fin"
            elif not phase_ok:
                verdict, key = "탈락(국면)", "fail_phase"
            elif not cht_ok:
                verdict, key = "대기(가격)", "fail_chart"
            else:
                verdict, key = "★매수", "buy"
            stat[key] += 1

            detail = short(div.get("note") or "", 46)
            row = (f"{short(name,14):14}{code:8}"
                   f"{mark(debt.get('passed')):>5}"
                   f"{mark(op.get('passed')):>5}"
                   f"{mark(div.get('passed')):>5}"
                   f"{short(phase or '판정불가',10):>10}"
                   f"{mark(cht_ok):>5}"
                   f"{verdict:>8}  {detail}")
            rows.append((key, row))
            if not pass_only or key == "buy":
                print(row)

        except Exception as e:
            stat["error"] += 1
            msg = short(str(e), 50)
            row = f"{short(name,14):14}{code:8}{'':>25}{'':>5}{'에러':>8}  {msg}"
            rows.append(("error", row))
            if not pass_only:
                print(row)

        if idx % 10 == 0:
            print(f"  ... {idx}/{len(stocks)} 진행", file=sys.stderr)
        time.sleep(sleep_sec)

    print("-" * 108)
    print(f"  ★매수 후보 {stat['buy']} · 대기(가격조건 미충족) {stat['fail_chart']} · "
          f"탈락(재무) {stat['fail_fin']} · 탈락(국면) {stat['fail_phase']} · "
          f"에러 {stat['error']}")
    print()
    print("  판정 기준")
    print("    부채 : 부채비율 상한 (업종별 완화 규칙 적용)")
    print("    영익 : 5년 흑자 + 최근분기 추세")
    print("    배당 : 시가배당률 >= 국고채 3년물 + 프리미엄")
    print("    국면 : 침체·판정불가면 매수 배제")
    print("    차트 : 52주 저점 +buy_zone% 이내여야 매수 시점")
    print()
    print("  ※ '대기(가격)' 는 종목 자체는 합격이나 아직 저점 근처가 아니라는 뜻입니다.")

    if pass_only:
        print()
        print("  [매수 후보 외 종목 요약]")
        from collections import Counter
        c = Counter(k for k, _ in rows)
        for k, v in c.items():
            if k != "buy":
                print(f"    {k}: {v}종목")


if __name__ == "__main__":
    main()