"""
리포트 전망 추적 — 리스크 인식과 전망 수정 방향이 '나쁜 해'를 구분하는지 검증한다.

핵심 질문:
    전망 하향은 하락장 직전에만 나타나는가, 아니면 늘 나타나는가?
    늘 하향이면 구분력이 없어 신호로 쓸 수 없다.

내는 것:
    1) 리스크 총점        Σ(probability × impact) 월별·연도별
    2) 전망 수정 방향     같은 시점(2026f 등)에 대한 전망이 전월 대비 오르내린 정도
                          -1.0(전부 하향) ~ +1.0(전부 상향)
    3) 연도별 대조표      위 두 지표를 전략 연간 수익률과 나란히

    ⚠️ 전략 수익률은 75종목·현금하한 0% 백테스트 기준 참고값이다.

사용:
    uv run python mytrading/report_trend.py              연도별 요약
    uv run python mytrading/report_trend.py --monthly    월별 상세
    uv run python mytrading/report_trend.py --dump       YAML 구조 훑기
"""
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml

DOCS = _ROOT / "mytrading" / "reports" / "investment_checklist"
RISK_FILE = DOCS / "kcif_risk_watch_history.yaml"
WORLD_FILE = DOCS / "kcif_insight_world_economic_history.yaml"

# 참고용 — value_range 전략 연간 수익률 (75종목 풀, 현금하한 0%)
STRATEGY = {
    "2021": +10.6, "2022": -17.6, "2023": +8.7,
    "2024": -6.6, "2025": +23.8, "2026": +15.2,
}


def load(path: Path) -> dict:
    if not path.exists():
        print(f"  (없음: {path.name})")
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"  (읽기 실패 {path.name}: {e})")
        return {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 1. 리스크 총점 ──────────────────────────────────────────────
def risk_scores():
    """{월: (총점, 리스크수, risks)}"""
    hist = (load(RISK_FILE) or {}).get("history") or {}
    out = {}
    for m in sorted(hist):
        risks = (hist[m] or {}).get("risks") or []
        total = 0.0
        for r in risks:
            p, i = _num(r.get("probability")), _num(r.get("impact"))
            if p is not None and i is not None:
                total += p * i
        out[m] = (total, len(risks), risks)
    return out


# ── 2. 전망 수정 방향 ───────────────────────────────────────────
def revision_scores():
    """{월: (수정지수, 상향, 하향, 유지)}

    전월 리포트와 **같은 시점 라벨**을 가진 값만 비교한다.
    시점이 굴러가면(2022f→2023f) 겹치는 것만 쓴다.
    """
    hist = (load(WORLD_FILE) or {}).get("history") or {}
    months = sorted(hist)

    flat = {}
    for m in months:
        e = hist[m] or {}
        tps = [str(t).strip().lstrip("'\u2018\u2019")
               for t in (e.get("timepoints") or [])]
        d = {}
        for c, data in (e.get("countries") or {}).items():
            vals = (data or {}).get("values") or []
            for i, tp in enumerate(tps):
                v = _num(vals[i]) if i < len(vals) else None
                if v is not None:
                    d[(c, tp)] = v
        flat[m] = d

    out = {}
    for prev, cur in zip(months, months[1:]):
        a, b = flat.get(prev) or {}, flat.get(cur) or {}
        keys = set(a) & set(b)
        if not keys:
            continue
        up = dn = same = 0
        for k in keys:
            d = b[k] - a[k]
            if d > 0.05:
                up += 1
            elif d < -0.05:
                dn += 1
            else:
                same += 1
        n = up + dn + same
        out[cur] = ((up - dn) / n if n else 0.0, up, dn, same)
    return out


def _bar(v, width=10):
    n = int(round(abs(v) * width))
    if v < 0:
        return ("<" * n).rjust(width) + "|" + " " * width
    return " " * width + "|" + (">" * n).ljust(width)


def yearly_view():
    risks = risk_scores()
    revs = revision_scores()

    by_year_risk = defaultdict(list)
    for m, (total, _, _) in risks.items():
        by_year_risk[m[:4]].append(total)

    by_year_rev = defaultdict(list)
    for m, (score, _, _, _) in revs.items():
        by_year_rev[m[:4]].append(score)

    years = sorted(set(by_year_risk) | set(by_year_rev))

    print("=" * 76)
    print("  연도별 대조 — 지표가 '나쁜 해'를 구분하는가")
    print("=" * 76)
    print(f"\n{'연도':6}{'리스크총점':>12}{'전망수정':>10}{'하향/상향':>12}"
          f"{'전략수익':>10}   판정")
    print("-" * 76)

    for y in years:
        r = by_year_risk.get(y) or []
        v = by_year_rev.get(y) or []
        r_avg = sum(r) / len(r) if r else None
        v_avg = sum(v) / len(v) if v else None

        dn = sum(1 for x in v if x < -0.05)
        up = sum(1 for x in v if x > 0.05)

        strat = STRATEGY.get(y)
        r_s = f"{r_avg:>12.1f}" if r_avg is not None else f"{'-':>12}"
        v_s = f"{v_avg:>10.2f}" if v_avg is not None else f"{'-':>10}"
        c_s = f"{dn}down/{up}up".rjust(12) if v else f"{'-':>12}"
        s_s = f"{strat:>+9.1f}%" if strat is not None else f"{'-':>10}"

        warned = v_avg is not None and v_avg < -0.15
        bad = strat is not None and strat < 0
        if strat is None or v_avg is None:
            verdict = ""
        elif warned and bad:
            verdict = "적중 (경고·손실)"
        elif warned and not bad:
            verdict = "오경보 (경고·수익)"
        elif not warned and bad:
            verdict = "놓침 (무경고·손실)"
        else:
            verdict = "정상 (무경고·수익)"

        print(f"{y:6}{r_s}{v_s}{c_s}{s_s}   {verdict}")

    print("\n  전망수정: -1.0 전부 하향 ~ +1.0 전부 상향 (경고 기준 -0.15)")
    print("  ※ 전략수익은 75종목·현금하한 0% 백테스트 참고값")


def monthly_view():
    risks = risk_scores()
    revs = revision_scores()
    months = sorted(set(risks) | set(revs))

    print("\n" + "=" * 76)
    print("  월별 상세")
    print("=" * 76)
    print(f"\n{'월':10}{'리스크':>8}{'수정지수':>10}{'하향':>6}{'상향':>6}   방향")
    print("-" * 76)
    for m in months:
        r = risks.get(m)
        v = revs.get(m)
        r_s = f"{r[0]:>8.0f}" if r else f"{'-':>8}"
        if v:
            v_s = f"{v[0]:>10.2f}"
            d_s, u_s = f"{v[2]:>6}", f"{v[1]:>6}"
            bar = _bar(v[0])
        else:
            v_s, d_s, u_s, bar = f"{'-':>10}", f"{'-':>6}", f"{'-':>6}", ""
        print(f"{m:10}{r_s}{v_s}{d_s}{u_s}   {bar}")


def dump_structure():
    def walk(node, depth=0):
        pad = "  " * depth
        if isinstance(node, dict):
            for k in list(node)[:6]:
                print(f"{pad}{k}")
                if depth < 3:
                    walk(node[k], depth + 1)
            if len(node) > 6:
                print(f"{pad}... (총 {len(node)}개)")
        elif isinstance(node, list):
            print(f"{pad}[리스트 {len(node)}] 예: {node[:3]}")
        else:
            print(f"{pad}{node!r}")

    for f in sorted(DOCS.glob("*.yaml")):
        print("\n" + "=" * 70)
        print(f"  {f.name}")
        print("=" * 70)
        walk(load(f))


def main():
    args = sys.argv[1:]
    if "--dump" in args:
        dump_structure()
        return
    yearly_view()
    if "--monthly" in args:
        monthly_view()
    print("\n" + "=" * 76)
    print("  ※ 관찰이지 판단이 아닙니다. 표본은 4~5년입니다.")
    print("=" * 76)


if __name__ == "__main__":
    main()