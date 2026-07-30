"""한국은행 보고서 자동 다운로드.

목록(listCont.do)에서 최신 보고서 감지 → 상세(view.do)에서 첨부ID 추출
→ 개관 PDF(fileDown.do fileSn=1) 다운로드 → reports 폴더 저장.

- 반기~분기 발간이라 자주 안 돌림 (cron 주1회 정도)
- 중복 방지: 이미 받은 nttId는 마커로 skip
- 저장 경로: mytrading_config.yaml 의 storage.reports_dir

사용:
  uv run python -m mytrading.download_reports          # 전체
  uv run python -m mytrading.download_reports 금융안정   # 하나만
"""
import re
import sys
import urllib.request
from pathlib import Path

import yaml

_THIS = Path(__file__).resolve().parent
_REPO = _THIS.parent
_CONFIG = _THIS.parent / "configs" / "mytrading_config.yaml"

BASE = "https://www.bok.or.kr/portal/singl/newsData"

# 보고서 3종 (menuNo 로 구분)
REPORTS = {
    "금융안정": {
        "name": "금융안정보고서",
        "menuNo": "200068",
        "depth2": "200699",
        "depth3": "200068",
        "bbs_id": "P0000593",
        "pdf_style": "fileDown",
        "out_subdir": "bok/fsr",
        "fname_prefix": "금융안정",
    },
    "통화신용정책": {
        "name": "통화신용정책보고서",
        "menuNo": "201150",
        "depth2": "200699",
        "depth3": "200067",
        "bbs_id": "B0000156",
        "pdf_style": "fileSrc",
        "out_subdir": "bok/mcpr",
        "fname_prefix": "통화신용정책",
    },
    "경제전망": {
        "name": "경제전망보고서",
        "menuNo": "201263",          # 목록용
        "view_menuNo": "201265",     # 상세 페이지용
        "depth2": "200038",
        "depth3": "201263",
        "bbs_id": "B0000502",
        "pdf_style": "fileSrc",
        "search_kwd": "경제전망",
        "out_subdir": "bok/eor",     # 저장 폴더 (name 대신)
        "fname_prefix": "경제전망",  # 파일명 접두 (_개관 제거)
    },
}

_UA = {"User-Agent": "Mozilla/5.0 (report-downloader)"}


def _load_reports_dir() -> Path:
    """config 의 storage.reports_dir. 절대경로면 그대로, 상대면 repo 기준."""
    try:
        from mytrading.common import load_config
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    raw = (cfg.get("storage") or {}).get("reports_dir") or "mytrading/reports"
    p = Path(raw)
    if not p.is_absolute():
        p = _REPO / p
    return p


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def get_latest(report: dict) -> dict:
    """목록에서 최신 보고서 1건. 반환 {nttId, year, month} 또는 None."""
    import urllib.parse as _up
    kwd = report.get("search_kwd")
    kwd_q = f"&searchKwd={_up.quote(kwd)}" if kwd else ""
    url = (f"{BASE}/listCont.do?pageIndex=1&targetDepth=3"
           f"&menuNo={report['menuNo']}&syncMenuChekKey=1"
           f"&searchCnd=1{kwd_q}&depth2={report['depth2']}&depth3={report['depth3']}")
    html = _fetch(url)
    # 첫 nttId = 최신
    ids = re.findall(r"nttId=([0-9]+)", html)
    if not ids:
        return None
    ntt = ids[0]
    # 제목에서 년월 (금융안정보고서(2026년 6월))
    title_kw = report.get("search_kwd") or report["name"]
    m = re.search(rf"{title_kw}\(([0-9]+)년\s*([0-9]+)월\)", html)
    year = m.group(1) if m else "0000"
    month = m.group(2).zfill(2) if m else "00"
    return {"nttId": ntt, "year": year, "month": month}


def get_pdf_url(nttId: str, report: dict) -> str:
    """상세 페이지에서 PDF 다운로드 URL. 스타일별 분기 (fileDown/fileSrc). 없으면 None."""
    bbs = report["bbs_id"]
    view_menu = report.get("view_menuNo", report["menuNo"])
    url = f"https://www.bok.or.kr/portal/bbs/{bbs}/view.do?nttId={nttId}&menuNo={view_menu}"
    html = _fetch(url)
    style = report.get("pdf_style", "fileDown")
    if style == "fileDown":
        m = re.search(r"fileDown\.do\?atchFileId=([a-f0-9]+)&(?:amp;)?fileSn=1", html)
        if not m:
            return None
        return f"https://www.bok.or.kr/portal/cmmn/file/fileDown.do?atchFileId={m.group(1)}&fileSn=1"
    if style == "fileSrc":
        m = re.search(r'/fileSrc/portal/([a-f0-9]{32})/\d+/([^"]+?\.pdf)', html)
        if not m:
            return None
        return f"https://www.bok.or.kr{m.group(0)}"
    return None



def download_one(key: str, report: dict, base_dir: Path) -> str:
    """보고서 1종 최신 개관 다운로드. 반환: 상태 메시지."""
    latest = get_latest(report)
    if not latest:
        return f"[{key}] 목록에서 최신 못 찾음"

    out_dir = base_dir / report.get("out_subdir", report["name"])
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / ".downloaded"
    done = set()
    if marker.exists():
        done = set(marker.read_text(encoding="utf-8").split())

    if latest["nttId"] in done:
        return (f"[{key}] 이미 최신 ({latest['year']}-{latest['month']}), "
                f"skip")

    pdf_url = get_pdf_url(latest["nttId"], report)
    if not pdf_url:
        return f"[{key}] PDF 링크 못 찾음 (nttId={latest['nttId']})"

    prefix = report.get("fname_prefix", report["name"])
    suffix = "" if report.get("fname_prefix") else "_개관"
    fname = f"{prefix}_{latest['year']}-{latest['month']}{suffix}.pdf"
    fpath = out_dir / fname
    data = _fetch_bytes(pdf_url)
    if not data.startswith(b"%PDF"):
        return f"[{key}] PDF 아님 (다운로드 실패)"
    fpath.write_bytes(data)

    # 마커 갱신
    done.add(latest["nttId"])
    marker.write_text(" ".join(sorted(done)), encoding="utf-8")

    kb = len(data) // 1024
    return f"[{key}] 다운로드 완료: {fname} ({kb}KB)"


def main():
    base_dir = _load_reports_dir()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"저장 경로: {base_dir}")
    for key, report in REPORTS.items():
        if only and key != only:
            continue
        try:
            print(download_one(key, report, base_dir))
        except Exception as e:
            print(f"[{key}] 오류: {e}")


if __name__ == "__main__":
    main()
