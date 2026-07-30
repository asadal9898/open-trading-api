"""KCIF 글로벌 리스크 워치 PDF 파싱.

- PDF에서 리스크 7개(순위/이름/발생가능성/영향력) 추출
- 여러 PDF를 순회하며 년월별로 yaml 축적
- 실행: uv run python -m mytrading.kcif_risk_parser
"""
import re
from pathlib import Path

import pdfplumber
from ruamel.yaml import YAML

# 리스크 표 한 줄 매칭 정규식
# 예: '1 중동전쟁 장기화 ★★★ ★★★'
_RISK_PATTERN = re.compile(r"^(\d+)\s+(.+?)\s+(★+)\s+(★+)(?:\s+(\S+))?$")

# 파일명에서 년월 추출: '260605-...' → '2026-06'
_DATE_PATTERN = re.compile(r"^(\d{2})(\d{2})(\d{2})")


def parse_risk_watch_pdf(pdf_path: Path) -> dict | None:
    """리스크 워치 PDF 하나를 파싱해서 dict 반환.

    반환 형태:
      {
        "source_file": "260605-...pdf",
        "risks": [
          {"rank": 1, "name": "...", "probability": 3, "impact": 3},
          ...
        ]
      }
    실패 시 None.
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
    except Exception as e:
        print(f"[parser] PDF 열기 실패: {pdf_path.name} - {e}")
        return None

    risks = []
    for line in text.split("\n"):
        line = line.strip()
        m = _RISK_PATTERN.match(line)
        if not m:
            continue
        rank, name, prob, impact, _change = m.groups()
        risks.append({
            "rank": int(rank),
            "name": name.strip(),
            "probability": len(prob),
            "impact": len(impact),
        })

    if not risks:
        print(f"[parser] 리스크 표 못 찾음: {pdf_path.name}")
        return None

    return {
        "source_file": pdf_path.name,
        "risks": risks,
    }


def _extract_yearmonth(filename: str) -> str | None:
    """파일명에서 년월 추출: '260605-...' → '2026-06'. 실패 시 None."""
    m = _DATE_PATTERN.match(filename)
    if not m:
        return None
    yy, mm, _dd = m.groups()
    return f"20{yy}-{mm}"


def update_history(pdf_dir: Path, yaml_path: Path) -> dict:
    """폴더 내 리스크 워치 PDF를 모두 파싱해서 yaml 에 축적.

    - 년월 키(예: '2026-06')로 저장
    - 이미 있는 년월은 덮어쓰지 않음 (skip)
    - 새로 추가된 년월만 카운트
    반환: {"added": [...], "skipped": [...], "failed": [...]}
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    # 기존 yaml 로드 (없으면 새로)
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
            print(f"[parser] 년월 추출 실패: {pdf.name}")
            failed.append(pdf.name)
            continue
        if ym in history:
            skipped.append(ym)
            continue
        parsed = parse_risk_watch_pdf(pdf)
        if not parsed:
            failed.append(pdf.name)
            continue
        history[ym] = parsed
        added.append(ym)
        print(f"[parser] 추가: {ym} ({len(parsed['risks'])}개 리스크) ← {pdf.name}")

    # 년월 정렬해서 저장
    sorted_history = {k: history[k] for k in sorted(history.keys())}
    data["history"] = sorted_history

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    return {"added": added, "skipped": skipped, "failed": failed}


if __name__ == "__main__":
    pdf_dir = Path("mytrading/reports/kcif/risk_watch")
    yaml_path = Path("mytrading/reports/investment_checklist/kcif_risk_watch_history.yaml")
    result = update_history(pdf_dir, yaml_path)
    print(f"\n결과: 추가 {len(result['added'])}건, 스킵 {len(result['skipped'])}건, 실패 {len(result['failed'])}건")
    print(f"저장: {yaml_path}")
