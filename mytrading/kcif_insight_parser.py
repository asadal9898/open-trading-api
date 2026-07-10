"""KCIF 국제금융 INSIGHT PDF 파싱 - IB 미 정책금리 전망.

- INSIGHT 리포트 페이지 6 근처의 '주요 IB 미 정책금리 전망' 표 추출
- IB 10개 × 5시점 (예: 6/7/9/10/12월)
- 여러 호를 순회하며 년월별로 yaml 축적
- 실행: uv run python -m mytrading.kcif_insight_parser
"""
import re
from pathlib import Path

import pdfplumber
from ruamel.yaml import YAML

# 헤더 패턴: '6월 7월 9월 10월 12월'
_HEADER_PATTERN = re.compile(
    r"^(\d+월)\s+(\d+월)\s+(\d+월)\s+(\d+월)\s+(\d+월)$",
    re.MULTILINE,
)

# IB 행 패턴: 'Barclays 3.75 3.75 3.75 3.75 3.75'
_IB_PATTERN = re.compile(
    r"^([A-Z][A-Za-z ]+?)\s+(\d\.\d{2})\s+(\d\.\d{2})\s+(\d\.\d{2})\s+(\d\.\d{2})\s+(\d\.\d{2})$",
    re.MULTILINE,
)

# 파일명에서 년월 추출: '국제금융+인사이트+`26.6월호.pdf' → '2026-06'
_DATE_PATTERN = re.compile(r"`?(\d{2})[.\s]*(\d{1,2})월호")


def _find_ib_rates_page(pdf) -> int | None:
    """PDF에서 IB 정책금리 표가 있는 페이지 번호 찾기 (0-indexed).
    페이지 6 근처를 우선 확인, 없으면 전체 스캔."""
    for i in range(min(15, len(pdf.pages))):
        text = pdf.pages[i].extract_text() or ""
        if _HEADER_PATTERN.search(text) and _IB_PATTERN.search(text):
            return i
    return None


def parse_ib_us_rates(pdf_path: Path) -> dict | None:
    """INSIGHT PDF에서 IB 미 정책금리 전망 표 파싱.

    반환 형태:
      {
        "source_file": "국제금융+인사이트+`26.6월호.pdf",
        "table_page": 6,
        "months": ["6월", "7월", "9월", "10월", "12월"],
        "forecasts": [
          {"ib": "Barclays", "rates": [3.75, 3.75, 3.75, 3.75, 3.75]},
          ...
        ]
      }
    실패 시 None.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_idx = _find_ib_rates_page(pdf)
            if page_idx is None:
                print(f"[insight] IB 표 못 찾음: {pdf_path.name}")
                return None
            text = pdf.pages[page_idx].extract_text() or ""
    except Exception as e:
        print(f"[insight] PDF 열기 실패: {pdf_path.name} - {e}")
        return None

    # 헤더 (월들)
    header_match = _HEADER_PATTERN.search(text)
    if not header_match:
        return None
    months = list(header_match.groups())

    # IB 값들
    forecasts = []
    for line in text.split("\n"):
        line = line.strip()
        m = _IB_PATTERN.match(line)
        if not m:
            continue
        ib_name = m.group(1).strip()
        rates = [float(x) for x in m.groups()[1:]]
        forecasts.append({"ib": ib_name, "rates": rates})

    if not forecasts:
        print(f"[insight] IB 데이터 못 찾음: {pdf_path.name}")
        return None

    return {
        "source_file": pdf_path.name,
        "table_page": page_idx + 1,  # 1-indexed
        "months": months,
        "forecasts": forecasts,
    }


# 세계 주요국 표 값 행 패턴
# 예: '미국 2.5 1.9 1.9 2.0 2.2 2.0' (6개 값) 또는 '세계경제 3.1 3.1' (2개 값)
_WORLD_ROW_PATTERN = re.compile(
    r"^(세계경제|미국|유로존|중국|일본)\s+((?:-?\d+\.\d+\s+){1,5}-?\d+\.\d+)$",
    re.MULTILINE,
)

_WORLD_TIMEPOINTS = ["'26.2Q", "'26.3Q", "'26.4Q", "'27.1Q", "2026f", "2027f"]


def parse_world_economic_forecast(pdf_path: Path) -> dict | None:
    """INSIGHT PDF에서 '세계 주요국 경제지표 전망' 표 파싱.

    - 5개국 (세계경제, 미국, 유로존, 중국, 일본) 경제성장률 예측
    - 세계경제: 연간 2개만 (2026f, 2027f)
    - 나머지: 분기 4개 + 연간 2개

    반환 형태:
      {
        "source_file": "...",
        "table_page": 9,
        "timepoints": ["'26.2Q", "'26.3Q", ..., "2026f", "2027f"],
        "countries": {
          "세계경제": {"values": [None, None, None, None, 3.1, 3.1]},
          "미국": {"values": [2.5, 1.9, 1.9, 2.0, 2.2, 2.0]},
          ...
        }
      }
    실패 시 None.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_idx = None
            for i in range(len(pdf.pages)):
                text = pdf.pages[i].extract_text() or ""
                if "세계 주요국 경제지표 전망" in text:
                    page_idx = i
                    break
            if page_idx is None:
                print(f"[insight] 세계 경제지표 표 못 찾음: {pdf_path.name}")
                return None
            text = pdf.pages[page_idx].extract_text() or ""
    except Exception as e:
        print(f"[insight] PDF 열기 실패: {pdf_path.name} - {e}")
        return None

    countries = {}
    for line in text.split("\n"):
        m = _WORLD_ROW_PATTERN.match(line.strip())
        if not m:
            continue
        country = m.group(1)
        values = [float(x) for x in m.group(2).split()]
        # 세계경제(2개)면 앞 4개를 None으로 채움
        if len(values) == 2:
            values = [None, None, None, None] + values
        countries[country] = {"values": values}

    if len(countries) < 5:
        print(f"[insight] 세계 경제지표 국가 부족 ({len(countries)}/5): {pdf_path.name}")
        return None

    return {
        "source_file": pdf_path.name,
        "table_page": page_idx + 1,
        "timepoints": _WORLD_TIMEPOINTS,
        "countries": countries,
    }


# 아시아 주요국 표 값 행 패턴
# 예: '한국 1.0 2.8 2.1 2.1 2.6 2.0 6.6 10.8 10.2' (9개 값)
_ASIA_COUNTRIES = ["한국", "대만", "홍콩", "인도", "인도네시아", "말레이시아", "필리핀", "싱가포르", "태국", "베트남"]
_ASIA_ROW_PATTERN = re.compile(
    r"^(한국|대만|홍콩|인도|인도네시아|말레이시아|필리핀|싱가포르|태국|베트남)"
    r"\s+((?:-?\d+\.\d+\s+){8}-?\d+\.\d+)$",
    re.MULTILINE,
)

_ASIA_TIMEPOINTS = ["2025f", "2026f", "2027f"]
_ASIA_INDICATORS = ["growth", "cpi", "current_account"]


def parse_asia_economic_forecast(pdf_path: Path) -> dict | None:
    """INSIGHT PDF에서 '아시아 주요국 경제지표 전망' 표 파싱.

    - 10개국 (한국~베트남)
    - 3지표: 경제성장률(growth), 물가(cpi), 경상수지(current_account)
    - 3시점: 2025f, 2026f, 2027f

    반환 형태:
      {
        "source_file": "...",
        "table_page": 9,
        "timepoints": ["2025f", "2026f", "2027f"],
        "indicators": ["growth", "cpi", "current_account"],
        "countries": {
          "한국": {
            "growth": [1.0, 2.8, 2.1],
            "cpi": [2.1, 2.6, 2.0],
            "current_account": [6.6, 10.8, 10.2]
          },
          ...
        }
      }
    실패 시 None.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_idx = None
            for i in range(len(pdf.pages)):
                text = pdf.pages[i].extract_text() or ""
                if "아시아 주요국 경제지표 전망" in text:
                    page_idx = i
                    break
            if page_idx is None:
                print(f"[insight] 아시아 경제지표 표 못 찾음: {pdf_path.name}")
                return None
            text = pdf.pages[page_idx].extract_text() or ""
    except Exception as e:
        print(f"[insight] PDF 열기 실패: {pdf_path.name} - {e}")
        return None

    countries = {}
    for line in text.split("\n"):
        m = _ASIA_ROW_PATTERN.match(line.strip())
        if not m:
            continue
        country = m.group(1)
        values = [float(x) for x in m.group(2).split()]
        # 9개 값을 3개씩 세 지표로 분배
        countries[country] = {
            "growth": values[0:3],
            "cpi": values[3:6],
            "current_account": values[6:9],
        }

    if len(countries) < 10:
        print(f"[insight] 아시아 경제지표 국가 부족 ({len(countries)}/10): {pdf_path.name}")
        return None

    return {
        "source_file": pdf_path.name,
        "table_page": page_idx + 1,
        "timepoints": _ASIA_TIMEPOINTS,
        "indicators": _ASIA_INDICATORS,
        "countries": countries,
    }
def _find_key_indicators_page(pdf):
    """주요 지표 시계열 표 시작 페이지 (0-indexed).
    '국제금융시장' + 월 헤더 4개 연속 패턴으로 탐지."""
    pat = re.compile(r"\d{1,2}월\s+\d{1,2}월\s+\d{1,2}월\s+\d{1,2}월")
    for i, page in enumerate(pdf.pages):
        t = page.extract_text() or ""
        if "국제금융시장" in t and pat.search(t):
            return i
    return None


def _parse_month_header(line):
    """'국제금융시장 \'25.5월 6월 ... \'26.1월 ... 5월' → 연월 리스트 (13개)."""
    toks = re.findall(r"[\u2018\u2019\']?(\d{2})?\.?(\d{1,2})월", line)
    months, cur_yy = [], None
    for yy, mm in toks:
        if yy:
            cur_yy = yy
        if cur_yy is None:
            continue
        months.append(f"'{cur_yy}.{int(mm)}월")
    return months


def _extract_values(s):
    """'880 (5.5%) 1,383.1 ...' → [880.0, 1383.1, ...].
    괄호 변동률 제거 + 천단위 콤마 제거."""
    no_paren = re.sub(r"\([^)]*\)", "", s)
    # 천단위 콤마 제거: 숫자,숫자 → 숫자숫자 (1,383.1 → 1383.1)
    no_comma = re.sub(r"(?<=\d),(?=\d)", "", no_paren)
    out = []
    for tok in re.findall(r"-?\d+\.?\d*", no_comma):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def parse_key_indicators(pdf_path: Path):
    """INSIGHT PDF 뒷부분 '주요 지표' 과거 시계열 표 파싱 (금융시장 지표).
    국제/국내 금융시장만. 실물경제(경제성장률·PMI 등)는 제외.
    계층: categories > 대분류(국제/국내 접두어) > 중분류 > 지표명 > [월별 값]."""
    # 국내 대분류 판별 데이터 키워드
    DOMESTIC_KW = ("KOSPI", "KOSDAQ", "VKOSPI", "원/달러", "국고채")
    INTL_KW = ("MSCI", "달러 인덱스", "WTI", "Eurostoxx")
    # '~시장' 외에 대분류로 취급할 헤더
    EXTRA_MAJORS = ("외국인 자금", "외화 유동성")
    try:
        with pdfplumber.open(pdf_path) as pdf:
            start = _find_key_indicators_page(pdf)
            if start is None:
                return None
            months = []
            categories = {}
            cur_major = None
            cur_minor = None
            for pi in range(start, min(start + 12, len(pdf.pages))):
                text = pdf.pages[pi].extract_text() or ""
                # 페이지 구간 판별
                is_dom = any(k in text for k in DOMESTIC_KW)
                is_intl = any(k in text for k in INTL_KW)
                if not is_dom and not is_intl:
                    # 실물경제 등 → 금융시장 구간 끝. 이미 데이터 모았으면 중단.
                    if categories:
                        break
                    continue
                section = "국내" if is_dom else "국제"

                for raw in text.split("\n"):
                    line = raw.strip()
                    if not line:
                        continue
                    if "국제금융시장" in line and "월" in line:
                        if not months:
                            months = _parse_month_header(line)
                        continue
                    if "국제 및 국내 금융시장 지표" in line or "주요 지표" in line:
                        continue
                    n_exp = len(months) if months else 13
                    all_vals = _extract_values(line)
                    # 데이터 행: 값이 월 개수 이상 (지표명 속 숫자 포함해 +2까지 허용)
                    if n_exp <= len(all_vals) <= n_exp + 2:
                        vals = all_vals[-n_exp:]  # 끝에서 n_exp개 = 실제 월별 값
                        # 지표명: 마지막 n_exp개 값 블록이 시작하기 전까지
                        # 괄호·콤마 정리 후, 끝 n_exp개 숫자 앞부분을 지표명으로
                        clean = re.sub(r"\([^)]*\)", "", line)
                        clean = re.sub(r"(?<=\d),(?=\d)", "", clean)
                        nums = list(re.finditer(r"-?\d+\.?\d*", clean))
                        if len(nums) >= n_exp:
                            name = clean[:nums[len(nums) - n_exp].start()].strip()
                        else:
                            name = clean.split()[0] if clean.split() else line
                        if cur_major and cur_minor and name:
                            categories.setdefault(cur_major, {}).setdefault(
                                cur_minor, {})[name] = vals
                        continue
                    # 헤더 처리
                    if line.endswith("시장"):
                        cur_major = f"{section} {line}"
                        cur_minor = None
                    elif any(line.startswith(em) for em in EXTRA_MAJORS):
                        cur_major = f"{section} {re.sub(chr(92)+'s*'+chr(92)+'(.*$', '', line).strip()}"
                        cur_minor = "_"  # 중분류 없는 대분류
                    elif len(line) < 30:
                        cur_minor = re.sub(r"\s*\(.*$", "", line).strip()
            if not categories:
                return None
            return {
                "source_file": pdf_path.name,
                "table_page": start + 1,
                "months": months,
                "categories": categories,
            }
    except Exception as e:
        print(f"[kcif] parse_key_indicators 실패 ({pdf_path.name}): {e}")
        return None


def _extract_yearmonth(filename: str) -> str | None:
    """파일명에서 년월 추출: '국제금융+인사이트+`26.6월호.pdf' → '2026-06'. 실패 시 None."""
    m = _DATE_PATTERN.search(filename)
    if not m:
        return None
    yy, mm = m.groups()
    return f"20{yy}-{int(mm):02d}"


def update_history(pdf_dir: Path, yaml_path: Path, parser_func, label: str = "insight") -> dict:
    """폴더 내 INSIGHT PDF를 모두 파싱해서 yaml 에 축적.

    - 년월 키(예: '2026-06')로 저장
    - 이미 있는 년월은 덮어쓰지 않음 (skip)
    반환: {"added": [...], "skipped": [...], "failed": [...]}
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    # 기존 yaml 로드
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.load(f) or {}
    else:
        data = {}

    history = data.get("history") or {}
    if not isinstance(history, dict):
        history = {}

    added, skipped, failed = [], [], []

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    for pdf in pdfs:
        ym = _extract_yearmonth(pdf.name)
        if not ym:
            print(f"[{label}] 년월 추출 실패: {pdf.name}")
            failed.append(pdf.name)
            continue
        if ym in history:
            skipped.append(ym)
            continue
        parsed = parser_func(pdf)
        if not parsed:
            failed.append(pdf.name)
            continue
        history[ym] = parsed
        added.append(ym)
        print(f"[{label}] 추가: {ym} ← {pdf.name}")

    # 년월 정렬해서 저장
    sorted_history = {k: history[k] for k in sorted(history.keys())}
    data["history"] = sorted_history

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    return {"added": added, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    pdf_dir = Path("mytrading/reports/kcif/insight")
    checklist_dir = Path("mytrading/reports/investment_checklist")

    # 파서별 (파서 함수, yaml 파일명, 로그 라벨)
    tasks = [
        (parse_ib_us_rates, "kcif_insight_ib_us_rates_history.yaml", "ib_us_rates"),
        (parse_world_economic_forecast, "kcif_insight_world_economic_history.yaml", "world"),
        (parse_asia_economic_forecast, "kcif_insight_asia_economic_history.yaml", "asia"),
        (parse_key_indicators, "kcif_insight_key_indicators_history.yaml", "key_indicators"),
    ]

    for parser_func, yaml_name, label in tasks:
        yaml_path = checklist_dir / yaml_name
        result = update_history(pdf_dir, yaml_path, parser_func, label)
        print(f"[{label}] 결과: 추가 {len(result['added'])}건, 스킵 {len(result['skipped'])}건, 실패 {len(result['failed'])}건")
        print(f"[{label}] 저장: {yaml_path}\n")
