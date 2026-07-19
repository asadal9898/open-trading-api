"""
유니버스 value_range 종목의 분기 영업이익 수집 → ~/op_history.json

기존 ~/collect_op.py 는 DART 조회 로직을 자체 복제하고 있어 CFS 전용 버그가
양쪽에 존재했다. 이 스크립트는 mytrading.dart_data.get_quarterly_op 를 그대로
써서 코드 경로를 하나로 통일한다 (CFS→OFS 폴백이 자동 적용됨).

⚠️ 조회만 한다. 주문·유니버스 수정 없음.

사용:
    KIS_MODE=prod uv run python collect_op_all.py                # 없는 것만 채움
    KIS_MODE=prod uv run python collect_op_all.py --refresh      # 전부 다시 수집
    KIS_MODE=prod uv run python collect_op_all.py --limit 5      # 앞 5종목만 (시험)
    KIS_MODE=prod uv run python collect_op_all.py --years 2019 2026

주의:
  - 종목·연도당 DART 호출 4회 + 공시일 조회. 76종목 × 8년이면 수천 회다.
  - --sleep 으로 간격 조절 (기본 0.3초). 레이트리밋 걸리면 늘릴 것.
  - 기존 파일은 병합한다 (덮어쓰지 않음). --refresh 를 줘야 다시 받는다.
"""
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]      # 저장소 루트
sys.path.insert(0, str(_ROOT))

import yaml

OUT = Path.home() / "op_history.json"
BACKUP = Path.home() / "op_history.json.bak"


def load_universe():
    """universe.yaml 의 value_range 종목 (코드, 이름, 스타일)."""
    uni = yaml.safe_load(
        (_ROOT / "mytrading" / "universe.yaml").read_text(encoding="utf-8")) or {}
    out, seen = [], set()
    for cat, items in uni.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or it.get("style") != "value_range":
                continue
            code = str(it.get("code", "")).strip()
            if code and code not in seen:
                seen.add(code)
                out.append((code, it.get("name", ""), it.get("style")))
    return out


def has_any(entry):
    """이 종목에 값이 하나라도 있나."""
    for q in (entry.get("op") or {}).values():
        if any(v is not None for v in q.values()):
            return True
    return False


def main():
    args = sys.argv[1:]
    refresh = "--refresh" in args
    limit = None
    sleep_sec = 0.3
    y0, y1 = 2019, 2026

    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            limit = int(args[i + 1])
    if "--sleep" in args:
        i = args.index("--sleep")
        if i + 1 < len(args):
            sleep_sec = float(args[i + 1])
    if "--years" in args:
        i = args.index("--years")
        if i + 2 < len(args):
            y0, y1 = int(args[i + 1]), int(args[i + 2])

    from mytrading.common import init
    init(require_confirm=False)
    from mytrading.dart_data import get_quarterly_op

    data = {}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
            OUT.replace(BACKUP)          # 기존 파일 백업
            print(f"기존 {len(data)}종목 로드 (백업: {BACKUP.name})")
        except Exception as e:
            print(f"기존 파일 읽기 실패, 새로 시작: {e}")
            data = {}

    stocks = load_universe()
    if limit:
        stocks = stocks[:limit]

    print(f"수집 대상 {len(stocks)}종목 · {y0}~{y1}년 · sleep {sleep_sec}s")
    print("-" * 62)

    filled = skipped = 0
    for idx, (code, name, style) in enumerate(stocks, 1):
        entry = data.get(code) or {"name": name, "style": style, "op": {}}
        entry["name"] = name or entry.get("name")
        entry["style"] = style or entry.get("style")

        if not refresh and has_any(entry):
            skipped += 1
            data[code] = entry
            continue

        ops = entry.get("op") or {}
        got = 0
        for yr in range(y0, y1 + 1):
            try:
                q = get_quarterly_op(code, yr, with_dates=False)
            except Exception as e:
                print(f"  {name}({code}) {yr} 실패: {str(e)[:40]}")
                q = None
            if q:
                # get_quarterly_op 는 {'Q1': {'op':..,'rcept':..}} 또는 평면 dict
                row = {}
                for k in ("Q1", "Q2", "Q3", "Q4", "FY"):
                    v = q.get(k)
                    row[k] = v.get("op") if isinstance(v, dict) else v
                ops[str(yr)] = row
                if any(v is not None for v in row.values()):
                    got += 1
            time.sleep(sleep_sec)

        entry["op"] = ops
        data[code] = entry
        filled += 1
        print(f"  [{idx:>3}/{len(stocks)}] {name[:14]:14} {code}  "
              f"데이터 있는 연도 {got}/{y1-y0+1}")

        # 중간 저장 (오래 걸리므로 중단돼도 살아남게)
        if idx % 5 == 0:
            OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    print("-" * 62)
    n_ok = sum(1 for c, _, _ in stocks if has_any(data.get(c) or {}))
    print(f"수집 완료 — 전체 {len(data)}종목 저장")
    print(f"  이번에 수집 {filled} · 기존 보유로 건너뜀 {skipped}")
    print(f"  대상 {len(stocks)}종목 중 데이터 있는 종목: {n_ok}")
    print(f"  저장: {OUT}")


if __name__ == "__main__":
    main()