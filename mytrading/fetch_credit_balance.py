# -*- coding: utf-8 -*-
"""
금융투자협회(KOFIA FreeSIS) 신용공여 잔고 추이 자동 수집.
기존 mytrading/reports/credit/market_credit_balance.csv 를 최신으로 갱신.

데이터: TMPV1=날짜, TMPV2=전체, TMPV3=유가증권(코스피), TMPV4=코스닥 (신용거래융자)
출처: https://freesis.kofia.or.kr  (STATSCU0100000070 신용공여 잔고 추이)
세션: requests.Session 으로 페이지 방문 → 쿠키 자동 획득 (하드코딩 없음)

실행: uv run python mytrading/fetch_credit_balance.py
      uv run python mytrading/fetch_credit_balance.py --days 90   # 조회 기간
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import requests

_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = _ROOT / "mytrading" / "reports" / "credit" / "market_credit_balance.csv"

PAGE_URL = ("https://freesis.kofia.or.kr/stat/FreeSIS.do"
            "?parentDivId=MSIS10000000000000&serviceId=STATSCU0100000070")
DATA_URL = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": PAGE_URL,
    "Origin": "https://freesis.kofia.or.kr",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json; charset=UTF-8",
}


def fetch_rows(start_yyyymmdd: str, end_yyyymmdd: str):
    """FreeSIS 에서 신용잔고 데이터 수집. [(date, total, kospi, kosdaq), ...]"""
    s = requests.Session()
    s.headers.update(HEADERS)
    # 1) 페이지 방문 → 세션 쿠키
    s.get(PAGE_URL, timeout=15)
    # 2) 데이터 요청
    payload = {"dmSearch": {
        "tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
        "tmpV45": start_yyyymmdd, "tmpV46": end_yyyymmdd,
        "OBJ_NM": "STATSCU0100000070BO"}}
    r = s.post(DATA_URL, data=json.dumps(payload), timeout=15)
    r.raise_for_status()
    rows = r.json().get("ds1", [])
    out = []
    for row in rows:
        d = str(row.get("TMPV1", ""))
        if len(d) != 8 or not d.isdigit():
            continue
        total = int(row.get("TMPV2", 0) or 0)
        kospi = int(row.get("TMPV3", 0) or 0)
        kosdaq = int(row.get("TMPV4", 0) or 0)
        out.append((d, total, kospi, kosdaq))
    return out


def load_existing():
    """기존 CSV → {date: line}. 없으면 빈 dict."""
    existing = {}
    header = "date,credit_total,credit_kospi,credit_kosdaq"
    if CSV_PATH.exists():
        lines = CSV_PATH.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            header = lines[0]
            for ln in lines[1:]:
                d = ln.split(",")[0]
                existing[d] = ln
    return header, existing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                    help="오늘부터 며칠 전까지 조회 (기본 30)")
    args = ap.parse_args()

    end = datetime.now()
    start = end - timedelta(days=args.days)
    s_str, e_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    print(f"[신용잔고] {s_str} ~ {e_str} 수집...")
    try:
        rows = fetch_rows(s_str, e_str)
    except Exception as e:
        print(f"[신용잔고] 수집 실패: {e}")
        sys.exit(1)

    if not rows:
        print("[신용잔고] 데이터 없음 (주말/휴일이거나 응답 이상).")
        return

    header, existing = load_existing()
    before = len(existing)
    added = 0
    for d, total, kospi, kosdaq in rows:
        if d not in existing:
            added += 1
        existing[d] = f"{d},{total},{kospi},{kosdaq}"

    # 날짜순 정렬 후 저장
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ordered = [existing[d] for d in sorted(existing.keys())]
    CSV_PATH.write_text(header + "\n" + "\n".join(ordered) + "\n", encoding="utf-8")

    latest = sorted(existing.keys())[-1]
    print(f"[신용잔고] 완료 — 신규 {added}일 추가 (총 {len(existing)}일, "
          f"최신 {latest}). 이전 {before}일.")
    # 최근 3일 표시
    for d in sorted(existing.keys())[-3:]:
        print(f"  {existing[d]}")


if __name__ == "__main__":
    main()
