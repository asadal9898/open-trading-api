"""
KCIF 인사이트 — 주요 IB 정책금리 전망표 수집기 (미국 / 유로존 / 일본).

왜 좌표 기반인가
    이 표들은 격자선이 아니라 **행 음영**으로 구분된다. 그래서 pdfplumber 의
    find_tables() 는 표를 통째로 못 잡고 조각낸다(은행 1곳짜리 조각 등).

    반대로 페이지 텍스트를 통째로 뽑으면 2단 레이아웃이 한 줄로 합쳐져
    미국 표와 유로존 표가 섞인다(은행 18~21곳).

    -> 단어 x좌표로 **단(column)을 먼저 나눈 뒤** 각 단 안에서 텍스트로 읽는다.
       PDF 한 페이지에 인쇄 2쪽 × 2단 = 최대 4단이 들어있다.
       단을 쪼개면 격자선이 필요 없고, 한 단에 표가 하나뿐이라 섞이지도 않는다.

읽는 순서 (각 단 안에서)
    캡션(`그림4 주요 IB 미 정책금리 전망`)  ->  머리글(시점 라벨)  ->  은행 행

정규화
    열 라벨 형식이 호마다 다르다: '24.5월 / 5월 / '25.1Q / '24.Q1 / '23Q3 / 2Q
    절대 시점(YYYY-MM)으로 바꾼다. 분기는 시작월로 본다.
    은행 구성이 매달 달라지므로 비교 기준값은 **중간값**을 쓴다(없으면 계산).
    표가 없는 달은 실패가 아니라 null 로 기록한다.

사용
    uv run python mytrading/kcif_ib_rates.py                  전체 수집
    uv run python mytrading/kcif_ib_rates.py --dry-run        저장 없이 확인
    uv run python mytrading/kcif_ib_rates.py --file 26.6월호   한 파일만
    uv run python mytrading/kcif_ib_rates.py --file 26.6월호 --debug
    uv run python mytrading/kcif_ib_rates.py --show           저장 결과 요약

출력
    mytrading/reports/investment_checklist/kcif_ib_rates_history.yaml
"""
import re
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pdfplumber
import yaml

PDF_DIR = _ROOT / "mytrading" / "reports" / "kcif" / "insight"
OUT_PATH = (_ROOT / "mytrading" / "reports" / "investment_checklist"
            / "kcif_ib_rates_history.yaml")

MAX_PAGES = 20
COL_GAP = 18          # 이만큼 비어 있으면 단 경계로 본다 (pt)
ROW_TOL = 3.0         # 같은 줄로 묶는 y 허용치

REGION_KEYS = {
    "us": ("미정책금리", "미국정책금리", "미국금리", "미금리",
           "정책금리(상단)", "정책금리상단", "연준정책금리"),
    "ez": ("유로존정책금리", "ecb정책금리", "유로존수신금리",
           "ecb정책금리수신금리"),
    "jp": ("일본은행정책금리", "boj정책금리", "일본정책금리"),
}
REGION_LABEL = {"us": "미국", "ez": "유로존", "jp": "일본"}

IB_NAMES = (
    "barclays", "boa", "bofa", "bank of america", "citi", "deutsche bank",
    "deutsche", "db", "goldman sachs", "goldman", "gs", "hsbc", "jpmorgan",
    "jpm", "morgan stanley", "ms", "nomura", "ubs", "bnp", "societe",
    "credit suisse", "cs", "ing", "capital eco", "ce", "dbs", "wells",
    "mizuho", "daiwa", "smbc", "natixis", "rbc", "td", "scotiabank",
)
MEDIAN_KEYS = ("중간값", "중앙값", "median")
_APOS = "'\u2018\u2019`\u00b4"


# ── 시점 라벨 정규화 ────────────────────────────────────────────
_RE_MONTH = re.compile(rf"^[{_APOS}]?(?:(\d{{2}})\.)?(\d{{1,2}})월$")
_RE_Q_NUM_FIRST = re.compile(rf"^[{_APOS}]?(?:(\d{{2}})\.)?(\d)[Qq]$")   # '25.1Q
_RE_Q_NUM_LAST = re.compile(rf"^[{_APOS}]?(?:(\d{{2}})\.)?[Qq](\d)$")    # '24.Q1
_RE_Q_YEAR = re.compile(rf"^[{_APOS}]?(\d{{2}})[Qq](\d)$")               # '23Q3


def _norm_timepoint(raw, base_year, prev):
    s = (raw or "").strip().replace(" ", "").replace("\n", "")
    if not s:
        return None
    year = month = kind = None

    m = _RE_MONTH.match(s)
    if m:
        yy, mm = m.groups()
        month, kind = int(mm), "month"
        year = 2000 + int(yy) if yy else None
    else:
        for rx in (_RE_Q_NUM_FIRST, _RE_Q_NUM_LAST):
            m = rx.match(s)
            if m:
                yy, q = m.groups()
                month, kind = (int(q) - 1) * 3 + 1, "quarter"
                year = 2000 + int(yy) if yy else None
                break
        if month is None:
            m = _RE_Q_YEAR.match(s)
            if m:
                yy, q = m.groups()
                month, kind = (int(q) - 1) * 3 + 1, "quarter"
                year = 2000 + int(yy)

    if month is None or not (1 <= month <= 12):
        return None
    if year is None:
        if prev:
            py, pm = prev
            year = py + 1 if month < pm else py
        else:
            year = base_year
    return f"{year:04d}-{month:02d}", kind


def _norm_series(tokens, base_year):
    tps, kinds, prev = [], [], None
    for t in tokens:
        got = _norm_timepoint(t, base_year, prev)
        if got:
            tps.append(got[0])
            kinds.append(got[1])
            y, mo = got[0].split("-")
            prev = (int(y), int(mo))
    return tps, kinds


_RE_NUM = re.compile(r"^-?\d+\.\d+$")


def _num_tokens(tokens):
    """앞쪽 이름 토큰을 건너뛰고 숫자(또는 결측 -)만 순서대로."""
    out = []
    for t in tokens:
        s = t.strip().replace(",", "")
        if _RE_NUM.match(s):
            out.append(float(s))
        elif s in ("-", "–", "—") and out:
            out.append(None)
    return out


def _strip_name(s):
    return re.sub(r"[*※\u00b9\u00b2\u00b3]+$", "", (s or "").strip()).strip()


def _match_ib(tokens):
    """줄 앞부분에서 IB 이름을 찾는다. (이름, 남은 토큰 인덱스) 또는 None."""
    for take in (2, 1):                    # 'Goldman Sachs' 같은 두 단어 우선
        if len(tokens) < take:
            continue
        name = _strip_name(" ".join(tokens[:take]))
        low = name.lower()
        if any(low == n or low.startswith(n) for n in IB_NAMES):
            return name, take
    first = _strip_name(tokens[0]) if tokens else ""
    if any(k in first for k in MEDIAN_KEYS):
        return first, 1
    return None


# ── 단 분리 ─────────────────────────────────────────────────────
def _columns(page):
    """단어 x 분포에서 빈 구간을 찾아 단 경계를 만든다."""
    try:
        words = page.extract_words()
    except Exception:
        return [], []
    if not words:
        return [], []

    width = int(page.width) + 2
    occ = [False] * (width + 1)
    for w in words:
        a, b = int(max(0, w["x0"])), int(min(width, w["x1"]))
        for x in range(a, b + 1):
            occ[x] = True

    spans, start = [], None
    for x in range(width + 1):
        if occ[x] and start is None:
            start = x
        elif not occ[x] and start is not None:
            spans.append((start, x - 1))
            start = None
    if start is not None:
        spans.append((start, width))

    # 좁은 빈틈은 무시하고 이어붙인다
    merged = []
    for s in spans:
        if merged and s[0] - merged[-1][1] < COL_GAP:
            merged[-1] = (merged[-1][0], s[1])
        else:
            merged.append(s)
    return [m for m in merged if m[1] - m[0] > 40], words


def _column_lines(words, x0, x1):
    """단 안의 단어를 줄 단위로 묶는다."""
    sel = [w for w in words if x0 - 2 <= (w["x0"] + w["x1"]) / 2 <= x1 + 2]
    sel.sort(key=lambda w: (w["top"], w["x0"]))
    lines, cur, cur_top = [], [], None
    for w in sel:
        if cur_top is None or abs(w["top"] - cur_top) <= ROW_TOL:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            cur.sort(key=lambda x: x["x0"])
            lines.append([x["text"] for x in cur])
            cur, cur_top = [w], w["top"]
    if cur:
        cur.sort(key=lambda x: x["x0"])
        lines.append([x["text"] for x in cur])
    return lines


def _classify(text):
    low = (text or "").lower().replace(" ", "")
    for region, keys in REGION_KEYS.items():
        if any(k in low for k in keys):
            return region
    return None


# ── 단 하나에서 표 읽기 ─────────────────────────────────────────
def _read_column(lines, base_year, debug=False):
    """[(region, parsed)] — 한 단 안에서 발견한 표들."""
    found = []
    for i, toks in enumerate(lines):
        region = _classify(" ".join(toks))
        if not region:
            continue

        # 캡션 다음 몇 줄 안에서 머리글 찾기
        tps = kinds = None
        head_i = None
        for j in range(i, min(i + 5, len(lines))):
            cand = lines[j]
            t, k = _norm_series(cand, base_year)
            if len(t) >= 3:
                tps, kinds, head_i = t, k, j
                break
        if not tps:
            if debug:
                print(f"      [{region}] 머리글 못 찾음 "
                      f"(캡션={' '.join(toks)[:40]!r})")
            continue

        n = len(tps)
        banks, median, miss = {}, None, 0
        for j in range(head_i + 1, len(lines)):
            toks2 = lines[j]
            hit = _match_ib(toks2)
            if not hit:
                miss += 1
                if banks and miss >= 3:
                    break
                continue
            miss = 0
            name, take = hit
            vals = _num_tokens(toks2[take:])[:n]
            if len([v for v in vals if v is not None]) < 2:
                continue
            vals += [None] * (n - len(vals))
            if any(k in name for k in MEDIAN_KEYS):
                median = vals
            else:
                banks[name] = vals

        if not banks:
            if debug:
                print(f"      [{region}] 은행 행 없음")
            continue

        if median is None:
            median = []
            for c in range(n):
                col = [v[c] for v in banks.values() if v[c] is not None]
                median.append(round(statistics.median(col), 3) if col else None)

        found.append((region, {"timepoints": tps, "kinds": kinds,
                               "banks": banks, "median": median}))
    return found


# ── PDF 하나 처리 ───────────────────────────────────────────────
_RE_YM = re.compile(rf"[{_APOS}]?(\d{{2}})\.(\d{{1,2}})월호")


def report_yearmonth(pdf_path: Path):
    m = _RE_YM.search(pdf_path.name)
    if m:
        return 2000 + int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{2})(\d{2})\d{2}-", pdf_path.name)
    if m:
        return 2000 + int(m.group(1)), int(m.group(2))
    return None, None


def parse_pdf(pdf_path: Path, verbose=True, debug=False):
    year, month = report_yearmonth(pdf_path)
    if year is None:
        if verbose:
            print(f"  [건너뜀] 년월 추출 실패: {pdf_path.name}")
        return None, None

    out = {r: None for r in REGION_KEYS}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pno, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
                cols, words = _columns(page)
                if not cols:
                    continue
                if debug and pno <= 8:
                    print(f"    [{pno}쪽] 단 {len(cols)}개 "
                          f"{[(int(a), int(b)) for a, b in cols]}")
                for (cx0, cx1) in cols:
                    lines = _column_lines(words, cx0, cx1)
                    for region, parsed in _read_column(lines, year, debug):
                        if out[region] is None:
                            parsed["table_page"] = pno
                            out[region] = parsed
                            if debug:
                                print(f"      -> {REGION_LABEL[region]} "
                                      f"{len(parsed['banks'])}곳 "
                                      f"{parsed['timepoints']}")
    except Exception as e:
        if verbose:
            print(f"  [오류] {pdf_path.name}: {str(e)[:70]}")
        return None, None

    key = f"{year:04d}-{month:02d}"
    entry = {"source_file": pdf_path.name, "regions": out}
    if verbose:
        got = ", ".join(f"{REGION_LABEL[r]}({len(out[r]['banks'])}곳)"
                        for r in ("us", "ez", "jp") if out[r])
        print(f"  {key}  {got or '표 없음'}")
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
    print(f"{'월':10}{'미국':>24}{'유로존':>24}{'일본':>24}")
    print("-" * 84)
    for k in sorted(hist):
        cells = []
        for r in ("us", "ez", "jp"):
            d = (hist[k].get("regions") or {}).get(r)
            if not d:
                cells.append("-")
            else:
                t = d["timepoints"]
                cells.append(f"{t[0]}~{t[-1]} ({len(d['banks'])})")
        print(f"{k:10}{cells[0]:>24}{cells[1]:>24}{cells[2]:>24}")
    n = len(hist)
    print()
    for r in ("us", "ez", "jp"):
        c = sum(1 for v in hist.values() if (v.get("regions") or {}).get(r))
        print(f"  {REGION_LABEL[r]}: {c}/{n}개월")


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

    hist = load_history()
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
    print("\n확인:  uv run python mytrading/kcif_ib_rates.py --show")


if __name__ == "__main__":
    main()