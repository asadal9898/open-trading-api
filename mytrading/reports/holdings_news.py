# -*- coding: utf-8 -*-
"""
자유종목(free_holdings) 뉴스 브리핑 — 구글 뉴스 RSS.
각 자유종목의 최근 뉴스 헤드라인을 모아 출력 (참고용, 매매신호 아님).

실행: uv run --with feedparser python mytrading/holdings_news.py
      uv run --with feedparser python mytrading/holdings_news.py --per 5   # 종목당 개수
"""
import sys
import argparse
from pathlib import Path
from urllib.parse import quote

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _free_symbols():
    """allocations.yaml 에서 자유종목 목록 → [(code, name), ...]."""
    import yaml
    path = _ROOT / "mytrading" / "configs" / "allocations.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    fh = data.get("free_holdings") or {}
    for user, accts in fh.items():
        for acc, lst in (accts or {}).items():
            if not isinstance(lst, list):
                continue
            for it in lst:
                if isinstance(it, dict) and it.get("code"):
                    out.append((str(it["code"]), it.get("name", it["code"])))
    return out


def _news(stock_name, n=5):
    """종목명으로 구글 뉴스 RSS → [(pub, title), ...]."""
    import feedparser
    url = (f"https://news.google.com/rss/search?q={quote(stock_name)}"
           f"&hl=ko&gl=KR&ceid=KR:ko")
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries[:n]:
        pub = e.get("published", "")[:16]
        title = e.get("title", "")
        out.append((pub, title))
    return out, len(feed.entries)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=5, help="종목당 뉴스 개수")
    ap.add_argument("--keep-days", type=int, default=100,
                    help="이 일수보다 오래된 뉴스 파일 삭제 (기본 30일)")
    args = ap.parse_args()

    symbols = _free_symbols()
    if not symbols:
        print("자유종목이 없습니다 (free_holdings 비어있음).")
        return

    # 한 번만 수집
    collected = []
    for code, name in symbols:
        try:
            items, total = _news(name, args.per)
        except Exception as e:
            print(f"■ {name}({code}) — 뉴스 조회 실패: {e}")
            items, total = [], 0
        collected.append((code, name, items, total))

    # 출력
    print(f"📰 자유종목 뉴스 브리핑 ({len(symbols)}개 종목)")
    print("=" * 60)
    for code, name, items, total in collected:
        print(f"\n■ {name}({code}) — 총 {total}건")
        if not items:
            print("  (뉴스 없음)")
        for pub, title in items:
            print(f"  [{pub}] {title}")
    print("\n" + "=" * 60)
    print("※ 참고용입니다. 뉴스는 매매 신호가 아니라 리스크 관찰용입니다.")

    # 날짜별 파일 저장
    import json
    from datetime import datetime, timedelta
    out_dir = _ROOT / "mytrading" / "reports" / "news"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now()
    result = {"generated_at": today.isoformat(timespec="seconds"),
              "count": len(symbols), "holdings": [
                  {"code": c, "name": n, "total": t,
                   "news": [{"published": p, "title": ti} for p, ti in items]}
                  for c, n, items, t in collected]}
    fname = f"holdings_news_{today.strftime('%Y%m%d')}.json"
    out_path = out_dir / fname
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"저장: {out_path.name}")

    # 오래된 파일 삭제 (keep-days 초과)
    cutoff = today - timedelta(days=args.keep_days)
    removed = 0
    for p in out_dir.glob("holdings_news_*.json"):
        try:
            ds = p.stem.replace("holdings_news_", "")
            fdate = datetime.strptime(ds, "%Y%m%d")
            if fdate < cutoff:
                p.unlink()
                removed += 1
        except Exception:
            continue
    if removed:
        print(f"오래된 파일 {removed}개 삭제 ({args.keep_days}일 초과).")


if __name__ == "__main__":
    main()
