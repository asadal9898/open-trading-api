"""
KCIF 인사이트 — '주요국 성장률 전망치'(그림2) 수집기.

왜 이 표인가
    값과 함께 **전월 대비 수정폭이 괄호로** 들어 있다.

        '26년  '27년 │ 26.2Q   3Q   '26년  '27년
         3.1    3.2  │  2.1   1.9    2.2    2.0
        (-0.1)  (-)  │ (-0.2) (-)  (-0.3)   (-)

    report_trend.py 는 월별 값을 빼서 수정 방향을 계산했는데, 이 표는 그 값을
    원본이 직접 준다. 파싱 오차가 없고 발행처가 확인한 값이다.

    ⚠️ 뒤쪽 주요지표의 '세계 주요국 경제지표 전망'과는 다른 표다.
       그쪽은 kcif_insight_parser.py 담당. 이건 앞쪽 동향&전망의 그림2.

실측에서 확인한 함정들
    1) 캡션 오탐 — 본문에도 "성장률 전망치가 …" 문구가 나온다.
       -> 같은 줄에 `그림N` 토큰이 있어야 캡션으로 인정한다.
    2) 단 분리가 안 되는 페이지가 있어 왼쪽 차트 축 숫자가 표 행에 섞인다.
       -> **국가 헤더의 x 범위** 밖 토큰은 버린다.
    3) 국가줄과 기간·값줄 사이에 다른 줄이 낀다(3줄·5줄 뒤).
       -> 탐색 창을 넉넉히 잡는다.
    4) 옛 호는 연도가 한 토큰으로 붙는다(`'21년'22년'23년`). 쪼갠다.
    5) 수정폭이 `(-0.3)(-0.3)`처럼 붙어 나온다. 줄을 이어붙여 정규식으로 뽑는다.

국가 배정 — x좌표가 아니라 표 구조로 나눈다
    국가 라벨은 그룹 중앙에 있는데 열 폭이 균일하지 않아, 가장 가까운 국가로
    붙이면 그룹 가장자리 열이 옆 국가로 넘어간다(실측 확인).
    대신 확실한 규칙이 있다: **연간 라벨 뒤에 분기 라벨이 오면 새 국가**다.

        '26년 '27년 │ 26.2Q 3Q '26년 '27년 │ 26.2Q 3Q '26년 '27년   -> 2/4/4
        '21년'22년'23년 │ 4Q Q1 '21년'22년 │ 4Q Q1 '21년'22년         -> 3/4/4

    이 규칙으로 나눈 그룹 수가 국가 수와 같으면 순서대로 대응시키고,
    다르면 x 최근접으로 물러난다.

사용
    uv run python mytrading/kcif_growth.py                  전체 수집
    uv run python mytrading/kcif_growth.py --dry-run        저장 없이 확인
    uv run python mytrading/kcif_growth.py --file 26.5월호 --debug
    uv run python mytrading/kcif_growth.py --show           월별 수정폭 요약

출력
    mytrading/reports/investment_checklist/kcif_growth_forecast_history.yaml
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pdfplumber
import yaml

from mytrading.reports.kcif_ib_rates import _columns, report_yearmonth

PDF_DIR = _ROOT / "mytrading" / "reports" / "kcif" / "insight"
OUT_PATH = (_ROOT / "mytrading" / "reports" / "investment_checklist"
            / "kcif_growth_forecast_history.yaml")

MAX_PAGES = 12
ROW_TOL = 3.0
WIN = 9

CAPTION_KEYS = ("주요국성장률전망", "주요국경제성장률전망", "성장률전망치")
COUNTRIES = ("글로벌", "세계경제", "세계", "미국", "유로존", "신흥국",
             "중국", "일본", "한국", "아시아")

_APOS = "'\u2018\u2019`\u00b4"

_RE_YEAR = re.compile(rf"^[{_APOS}]?(\d{{2}})년$")
_RE_Q1 = re.compile(rf"^[{_APOS}]?(?:(\d{{2}})\.)?(\d)[Qq]$")
_RE_Q2 = re.compile(rf"^[{_APOS}]?(?:(\d{{2}})\.)?[Qq](\d)$")
_RE_NUM_IN = re.compile(r"-?\d+\.\d+")
_RE_PAREN_IN = re.compile(r"\(([^)]*)\)")
_RE_ANY_TP = re.compile(
    rf"[{_APOS}]?\d{{2}}년"
    rf"|[{_APOS}]?\d{{2}}\.\d[Qq]|[{_APOS}]?\d{{2}}\.[Qq]\d"
    rf"|[{_APOS}]?\d{{2}}[Qq]\d|\d[Qq]|[Qq]\d"
)


def _norm_period(tok, base_year, prev_year):
    s = (tok or "").strip().replace(" ", "")
    if not s:
        return None
    m = _RE_YEAR.match(s)
    if m:
        return f"{2000 + int(m.group(1))}", "annual"
    for rx in (_RE_Q1, _RE_Q2):
        m = rx.match(s)
        if m:
            yy, q = m.groups()
            year = 2000 + int(yy) if yy else (prev_year or base_year)
            return f"{year}-Q{q}", "quarter"
    return None


def _to_delta(s):
    s = (s or "").strip().replace("△", "-").replace("▲", "-")
    if s in ("-", "", "–", "—"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _expand(text, x):
    found = _RE_ANY_TP.findall(text or "")
    if len(found) > 1:
        return [(f, x) for f in found]
    return [(text, x)]


# ── 줄 묶기 ─────────────────────────────────────────────────────
def _lines_xy(words, x0, x1):
    sel = [w for w in words if x0 - 2 <= (w["x0"] + w["x1"]) / 2 <= x1 + 2]
    sel.sort(key=lambda w: (w["top"], w["x0"]))
    lines, cur, top0 = [], [], None
    for w in sel:
        if top0 is None or abs(w["top"] - top0) <= ROW_TOL:
            cur.append(w)
            top0 = w["top"] if top0 is None else top0
        else:
            cur.sort(key=lambda x: x["x0"])
            lines.append([(x["text"], (x["x0"] + x["x1"]) / 2) for x in cur])
            cur, top0 = [w], w["top"]
    if cur:
        cur.sort(key=lambda x: x["x0"])
        lines.append([(x["text"], (x["x0"] + x["x1"]) / 2) for x in cur])
    return lines


def _is_caption(line):
    if not any(t.strip().startswith("그림") for t, _ in line):
        return False
    joined = "".join(t for t, _ in line).replace(" ", "")
    return any(k in joined for k in CAPTION_KEYS)


def _countries_in(line):
    return [(t.strip(), x) for t, x in line if t.strip() in COUNTRIES]


# ── 국가 배정 ───────────────────────────────────────────────────
def _split_groups(periods):
    """연간 뒤 분기가 오면 새 그룹. periods=[(label, kind, x)]"""
    groups, cur, prev_kind = [], [], None
    for p in periods:
        if cur and p[1] == "quarter" and prev_kind == "annual":
            groups.append(cur)
            cur = []
        cur.append(p)
        prev_kind = p[1]
    if cur:
        groups.append(cur)
    return groups


def _assign(periods, countries, debug=False):
    """[(country, label, kind)] 순서대로."""
    groups = _split_groups(periods)
    if len(groups) == len(countries):
        out = []
        for (cname, _), g in zip(countries, groups):
            for label, kind, _x in g:
                out.append((cname, label, kind))
        return out, "구조"
    # 물러나기: x 최근접
    out = []
    for label, kind, px in periods:
        cname = min(countries, key=lambda a: abs(px - a[1]))[0]
        out.append((cname, label, kind))
    return out, f"최근접(그룹 {len(groups)}≠국가 {len(countries)})"


def _read_block(lines, i, base_year, debug=False):
    countries = _countries_in(lines[i])
    if len(countries) < 2:
        return None, i + 1

    xs = [x for _, x in countries]
    xmin, xmax = min(xs) - 35, max(xs) + 45

    def txt(line):
        return " ".join(t for t, x in line if xmin <= x <= xmax)

    # 기간줄
    periods = pj = None
    for j in range(i + 1, min(i + WIN, len(lines))):
        toks = []
        for t, x in lines[j]:
            if xmin <= x <= xmax:
                toks.extend(_expand(t, x))
        got, prev_year = [], None
        for t, x in toks:
            n = _norm_period(t, base_year, prev_year)
            if n:
                got.append((n[0], n[1], x))
                prev_year = int(n[0].split("-")[0])
        if len(got) >= 2:
            periods, pj = got, j
            break
    if not periods:
        if debug:
            print(f"      기간줄 못 찾음 ({[c for c,_ in countries]})")
        return None, i + 1

    n = len(periods)

    # 값줄 — 괄호가 많은 줄(수정폭)은 건너뛴다
    vals = vj = None
    for j in range(pj + 1, min(pj + WIN, len(lines))):
        s = txt(lines[j])
        if s.count("(") >= 2:
            continue
        nums = [float(x) for x in _RE_NUM_IN.findall(s)]
        if len(nums) >= n:
            vals, vj = nums[:n], j
            break
    if not vals:
        if debug:
            print(f"      값줄 못 찾음 (기간 {n}개)")
        return None, pj + 1

    # 수정폭줄 — (-0.3)(-0.3) 처럼 붙어 나오므로 줄 전체에서 뽑는다
    deltas = []
    for j in range(vj + 1, min(vj + 4, len(lines))):
        found = _RE_PAREN_IN.findall(txt(lines[j]))
        if len(found) >= n:
            deltas = [_to_delta(x) for x in found[:n]]
            break

    pairs, how = _assign(periods, countries, debug)
    out = {}
    for k, (cname, label, _kind) in enumerate(pairs):
        out.setdefault(cname, {})[label] = {
            "value": vals[k],
            "revision": deltas[k] if k < len(deltas) else None,
        }

    if debug:
        print(f"      {[c for c,_ in countries]}  기간 {n}  값 {len(vals)}  "
              f"수정폭 {len(deltas)}  배정={how}")
        for c, d in out.items():
            print(f"        {c}: {d}")
    return out, vj + 2


def parse_pdf(pdf_path: Path, verbose=True, debug=False):
    year, month = report_yearmonth(pdf_path)
    if year is None:
        if verbose:
            print(f"  [건너뜀] 년월 추출 실패: {pdf_path.name}")
        return None, None

    result, page_no = {}, None
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pno, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
                cols, words = _columns(page)
                if not cols:
                    continue
                for (cx0, cx1) in cols:
                    lines = _lines_xy(words, cx0, cx1)
                    cap_i = next((i for i, l in enumerate(lines)
                                  if _is_caption(l)), None)
                    if cap_i is None:
                        continue
                    if debug:
                        print(f"    [{pno}쪽] 캡션: "
                              f"{' '.join(t for t,_ in lines[cap_i])[:60]}")
                    k, guard = cap_i + 1, 0
                    while k < len(lines) and guard < 14:
                        block, k = _read_block(lines, k, year, debug)
                        guard += 1
                        if block:
                            for c, d in block.items():
                                result.setdefault(c, {}).update(d)
                    if result:
                        page_no = pno
                        break
                if result:
                    break
    except Exception as e:
        if verbose:
            print(f"  [오류] {pdf_path.name}: {str(e)[:70]}")
        return None, None

    key = f"{year:04d}-{month:02d}"
    entry = {"source_file": pdf_path.name, "page": page_no,
             "countries": result or None}
    if verbose:
        if result:
            print(f"  {key}  " +
                  ", ".join(f"{c}({len(v)})" for c, v in result.items()))
        else:
            print(f"  {key}  표 없음")
    return key, entry


# ── 저장·조회 ───────────────────────────────────────────────────
def load_history():
    if not OUT_PATH.exists():
        return {}
    try:
        return (yaml.safe_load(OUT_PATH.read_text(encoding="utf-8")) or {}
                ).get("history") or {}
    except Exception:
        return {}


def save_history(hist):
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        yaml.safe_dump({"history": hist}, allow_unicode=True,
                       sort_keys=True, default_flow_style=False),
        encoding="utf-8")


def show():
    hist = load_history()
    if not hist:
        print("저장된 결과가 없습니다.")
        return
    print("월별 전망 수정폭 (원본 괄호값, 음수=하향)\n")
    print(f"{'월':10}{'국가':>6}{'항목':>6}{'하향':>6}{'상향':>6}{'수정합':>9}")
    print("-" * 46)
    for k in sorted(hist):
        cs = hist[k].get("countries") or {}
        dn = up = cnt = 0
        tot = 0.0
        for per in cs.values():
            for d in per.values():
                r = d.get("revision")
                if r is None:
                    continue
                cnt += 1
                tot += r
                if r < -0.01:
                    dn += 1
                elif r > 0.01:
                    up += 1
        print(f"{k:10}{len(cs):>6}{cnt:>6}{dn:>6}{up:>6}{tot:>9.1f}")


def main():
    args = sys.argv[1:]
    if "--show" in args:
        show()
        return

    dry = "--dry-run" in args
    debug = "--debug" in args
    only = None
    if "--file" in args:
        i = args.index("--file")
        if i + 1 < len(args):
            only = args[i + 1]

    files = sorted(PDF_DIR.glob("*.pdf"))
    if only:
        files = [f for f in files if only in f.name]
    if not files:
        print(f"PDF 없음: {PDF_DIR}")
        return

    hist = load_history() if only else {}
    added = 0
    print(f"대상 {len(files)}개\n")
    for f in files:
        key, entry = parse_pdf(f, debug=debug)
        if key:
            hist[key] = entry
            added += 1

    print(f"\n처리 {added}건")
    if dry:
        print("(--dry-run: 저장하지 않음)")
        return
    save_history(hist)
    print(f"저장: {OUT_PATH}")
    print("\n확인:  uv run python mytrading/kcif_growth.py --show")


if __name__ == "__main__":
    main()