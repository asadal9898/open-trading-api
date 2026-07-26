"""
한국은행 경제전망 요약표 파서.

bok/eor/경제전망_YYYY-MM.pdf 의 '경제전망 요약표'에서 전망 값과
**전월(직전 전망) 대비 수정폭**을 뽑는다. 핵심 신호는 연간 전망 옆 대괄호
수정폭(예: 2026 연간 2.9 [-0.2] = 전월 대비 0.2 하향). KCIF 그림2와 같은
성격 — 발행처가 직접 준 값이라 파싱 오차가 없다.

호별 열 구조가 다르다:
    2025-05:  24연 │ 25상 25하 25연[Δ] │ 26상 26하 26연[Δ]            (연간 2개)
    2025-11:  24연 │ 25상 25하 25연[Δ] │ 26상 26하 26연[Δ] │ 27연      (+다음연도)
    2026-02:  25연 │ 26상 26하 26연[Δ] │ 27상 27하 27연[Δ] │ 28연[Δ]  (연간 3개)
항목명 띄어쓰기도 다르다: '세계경제성장률' vs '세계경제 성장률'.

그래서 값을 고정 위치로 세지 않는다. 연간 컬럼은 항상 **수정폭 [Δ]이 바로
뒤에 붙은 값**이므로, 수정폭 위치를 기준으로 (전망연도, 연간값, Δ) 쌍을
순서대로 뽑는다. 열이 몇 개든 안 밀린다. 페이지·항목 매칭은 공백을 무시한다.

간이판(Indigo Book)처럼 요약표 없는 호는 null 로 둔다.

반기값은 호별 열이 유동적이라 저장하지 않는다 — 연간값+수정폭이 신호다.

사용:
    uv run python mytrading/bok_outlook_parser.py
    uv run python mytrading/bok_outlook_parser.py --file 2025-11 --debug
    uv run python mytrading/bok_outlook_parser.py --dry-run
    uv run python mytrading/bok_outlook_parser.py --show

출력:
    mytrading/reports/investment_checklist/bok_economic_outlook_history.yaml
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pdfplumber
import yaml

PDF_DIR = _ROOT / "mytrading" / "reports" / "bok" / "eor"
OUT_PATH = (_ROOT / "mytrading" / "reports" / "investment_checklist"
            / "bok_economic_outlook_history.yaml")

# (매칭 라벨 — 공백 제거형, 저장 키, 섹션)
ITEMS = [
    ("세계경제성장률", "world_growth", "global"),
    ("미국", "us_growth", "global"),
    ("유로지역", "euro_growth", "global"),
    ("중국", "china_growth", "global"),
    ("일본", "japan_growth", "global"),
    ("세계교역신장률", "world_trade", "global"),
    ("브렌트유가", "brent_oil", "global"),
    ("GDP성장률", "gdp_growth", "domestic"),
    ("민간소비", "private_consumption", "domestic"),
    ("건설투자", "construction_invest", "domestic"),
    ("설비투자", "facility_invest", "domestic"),
    ("지식재산생산물투자", "ip_invest", "domestic"),
    ("재화수출", "goods_export", "domestic"),
    ("재화수입", "goods_import", "domestic"),
    ("소비자물가상승률", "cpi", "domestic"),
    ("근원물가", "core_cpi", "domestic"),
    ("경상수지", "current_account", "domestic"),
    ("상품수지", "goods_balance", "domestic"),
    ("서비스수지", "service_balance", "domestic"),
    ("본원·이전소득수지", "primary_income_balance", "domestic"),
    ("본원‧이전소득수지", "primary_income_balance", "domestic"),
    ("취업자수증감", "employment_change", "domestic"),
    ("실업률", "unemployment", "domestic"),
    ("고용률", "employment_rate", "domestic"),
]

# 수정폭 붙은 연간값:  3.0 [+0.2] / -8.7 [-0.4] / 1,150 [+50] / 2.6[ - ]
_RE_ANNUAL = re.compile(r"(-?[\d,]+\.?\d*)\s*\[\s*([+-]?[\d.]+|-)\s*\]")


def _num(s):
    s = s.replace(",", "").lstrip(".")
    try:
        return float(s)
    except ValueError:
        return None


def _delta(s):
    s = s.strip()
    if s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _nospace(s):
    return (s or "").replace(" ", "")


def _find_page(pdf):
    """요약표 페이지 텍스트.

    보통은 항목명(세계경제성장률·GDP성장률·민간소비)으로 찾는다.
    다만 항목명이 이미지인 호(2025-11)는 그 텍스트가 없으므로,
    수정폭 대괄호([+0.2] 등)가 여러 개 있는 페이지도 요약표로 인정한다.
    (요약표는 문서 내에서 수정폭 대괄호가 몰려 있는 유일한 페이지다.)
    """
    fallback = None
    for pg in pdf.pages:
        t = pg.extract_text() or ""
        ns = _nospace(t)
        # 요약표 = '세계경제성장률' 뒤에 숫자값이 붙은 줄이 있는 페이지.
        # (본문 '요약' 서술 페이지에도 항목명은 나오지만 값이 안 붙는다)
        has_value_row = bool(re.search(
            r"세계경제\s*성장률[^\n]*\d+\.\d", t))
        if has_value_row and "GDP" in ns and ("민간소비" in ns or "경상수지" in ns):
            return t
        if fallback is None and len(_RE_ANNUAL.findall(t)) >= 15:
            fallback = t
    return fallback


def _parse_years(text, report_year):
    """전망연도 = report_year 부터. 헤더의 연도 중 report_year 이상만 취한다.

    헤더 표기가 호마다 달라(e) 개수가 1~2개) e) 로는 못 가린다.
        2·5·11월:  '2023 2024 2025e) 2026e)'
        8월:       '2024 2025 2026e)'
    파일명 연도(전망 발표 연도)가 곧 첫 전망연도이므로 그걸 기준으로 삼는다.
    """
    for line in text.splitlines()[:6]:
        allyears = [int(y) for y in re.findall(r"20\d{2}", line)]
        if len(allyears) >= 3:
            fy = [y for y in sorted(set(allyears)) if y >= report_year]
            return fy or sorted(set(allyears))[-2:]
    return []


def _match_item(line):
    ns = _nospace(line).lstrip("•·-")
    for label, key, section in sorted(ITEMS, key=lambda x: -len(x[0])):
        if ns.startswith(label):
            return key, section, label
    return None


def _parse_row(line, forecast_years):
    """(연간값+수정폭) 쌍들을 순서대로 뽑아 전망연도에 대응."""
    pairs = _RE_ANNUAL.findall(line)
    if not pairs:
        return None
    annual, revision = {}, {}
    for idx, (v, d) in enumerate(pairs):
        if idx >= len(forecast_years):
            break
        y = str(forecast_years[idx])
        val = _num(v)
        dv = _delta(d)
        if val is not None:
            annual[y] = val
        if dv is not None:
            revision[y] = dv
    if not annual:
        return None
    return {"annual": annual, "revision": revision}


def _parse_by_order(text, forecast_years):
    """항목명이 이미지라 없는 호(예: 2025-11) — 값 줄 순서로 ITEMS 에 대응.

    수정폭 대괄호가 있는 줄만 값 줄로 보고, 등장 순서를 ITEMS 순서와
    1:1 매칭한다. 값 줄 수가 ITEMS 수와 맞지 않으면 None(안전상 포기).
    """
    rows = [l for l in text.splitlines() if _RE_ANNUAL.search(l)]
    # ITEMS 에는 구분점 변형(본원·/본원‧) 중복이 있으니 키 기준으로 유일화
    uniq = []
    seen = set()
    for label, key, section in ITEMS:
        if key not in seen:
            uniq.append((key, section))
            seen.add(key)
    if len(rows) < len(uniq):
        return None
    rows = rows[:len(uniq)]
    items = {}
    for (key, section), line in zip(uniq, rows):
        row = _parse_row(line, forecast_years)
        if row is None:
            return None
        row["section"] = section
        items[key] = row
    return items


def _annual_positions(text):
    """구분줄('연간 상반 하반 연간 …')에서 '연간' 토큰의 위치(0-index) 목록.

    이 위치가 값 줄에서 연간값이 있는 인덱스와 일치한다.
    반환 예: [0, 3, 6]  (실적연간 + 전망연간들)
    """
    for line in text.splitlines()[:6]:
        toks = line.split()
        if toks.count("연간") >= 2 or any("연간" in t for t in toks):
            pos = [i for i, t in enumerate(toks) if "연간" in t]
            if len(pos) >= 2:
                return pos
    return []


def _parse_row_positional(line, label, ann_pos, forecast_years):
    """수정폭 없는 호: 헤더 연간위치로 연간값을 뽑는다.

    ann_pos 뒤에서 len(forecast_years) 개가 전망 연간값.
    revision 은 비운다(원본에 수정폭 없음).
    """
    rest = line[line.find(label) + len(label):]
    toks = [_num(t) for t in rest.split()]
    toks = [t for t in toks if t is not None] if any(
        c.isdigit() for c in rest) else []
    # 값 토큰만 다시 (숫자 파싱)
    raw = rest.split()
    vals = []
    for t in raw:
        v = _num(t)
        if v is not None:
            vals.append(v)
    if len(vals) <= max(ann_pos):
        return None
    # 전망연도에 해당하는 연간위치 = ann_pos 뒤에서 len(fy)개
    fy_pos = ann_pos[-len(forecast_years):]
    annual = {}
    for y, p in zip(forecast_years, fy_pos):
        if p < len(vals):
            annual[str(y)] = vals[p]
    if not annual:
        return None
    return {"annual": annual, "revision": {}}


def parse_pdf(pdf_path, verbose=True, debug=False):
    m = re.search(r"(\d{4})-(\d{2})", pdf_path.name)
    if not m:
        return None, None
    key = f"{m.group(1)}-{m.group(2)}"

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = _find_page(pdf)
    except Exception as e:
        if verbose:
            print(f"  [오류] {pdf_path.name}: {str(e)[:60]}")
        return None, None

    if not text:
        if verbose:
            print(f"  {key}  요약표 없음 (간이판/구형식)")
        return key, None

    forecast_years = _parse_years(text, int(m.group(1)))
    if not forecast_years:
        if verbose:
            print(f"  {key}  연도 헤더 못 읽음")
        return key, None

    # 수정폭 대괄호가 표에 있는지 (없으면 옛 형식 — 위치 기반 파싱)
    has_delta = _RE_ANNUAL.search(text) is not None
    ann_pos = _annual_positions(text) if not has_delta else []

    items = {}
    for line in text.splitlines():
        hit = _match_item(line)
        if not hit:
            continue
        pkey, section, label = hit
        if pkey in items:
            continue
        if has_delta:
            row = _parse_row(line, forecast_years)
        elif ann_pos:
            row = _parse_row_positional(line, label, ann_pos, forecast_years)
        else:
            row = None
        if row is None:
            if debug:
                print(f"      [{pkey}] 값 없음: {line[:70]}")
            continue
        row["section"] = section
        items[pkey] = row

    # 항목명이 이미지인 호: 라벨 매칭이 과반 미만이면 순서 기반으로 재파싱
    if len(items) < len(ITEMS) // 2:
        ordinal = _parse_by_order(text, forecast_years)
        if ordinal:
            items = ordinal
            if debug:
                print(f"      (항목명 없음 → 값 줄 순서로 파싱)")

    if not items:
        if verbose:
            print(f"  {key}  항목 파싱 실패")
        return key, None

    entry = {
        "source_file": pdf_path.name,
        "forecast_years": forecast_years,
        "items": items,
    }
    if verbose:
        rev = sum(len(v["revision"]) for v in items.values())
        print(f"  {key}  항목 {len(items)}개 · 전망연도 {forecast_years} · "
              f"수정폭 {rev}개")
    if debug:
        for k, v in items.items():
            print(f"      {k:22} {v['annual']}  Δ{v['revision']}")
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
    print("한국은행 경제전망 — 첫 전망연도 GDP·물가 전망과 수정폭\n")
    print(f"{'전망월':9}{'대상':>6}{'GDP':>7}{'Δ':>7}{'물가':>7}{'Δ':>7}")
    print("-" * 44)
    for k in sorted(hist):
        e = hist[k]
        if not e:
            print(f"{k:9}  (요약표 없음)")
            continue
        fy = str(e["forecast_years"][0])
        it = e["items"]
        gdp = it.get("gdp_growth", {})
        cpi = it.get("cpi", {})
        gv = gdp.get("annual", {}).get(fy)
        gd = gdp.get("revision", {}).get(fy)
        cv = cpi.get("annual", {}).get(fy)
        cd = cpi.get("revision", {}).get(fy)

        def g(x):
            return f"{x:.1f}" if isinstance(x, (int, float)) else "-"

        def f(x):
            return f"{x:+.1f}" if isinstance(x, (int, float)) else "-"
        print(f"{k:9}{fy:>6}{g(gv):>7}{f(gd):>7}{g(cv):>7}{f(cd):>7}")


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
    print("\n확인:  uv run python mytrading/bok_outlook_parser.py --show")


if __name__ == "__main__":
    main()