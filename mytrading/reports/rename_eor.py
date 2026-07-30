"""
bok/eor 경제전망 PDF 파일명 통일.

수동으로 받은 파일들이 두 형식으로 섞여 있다.
    [보도자료] 경제전망(2026.5월)_F.pdf
    경제전망(2023.8월).pdf
자동 다운로드 형식으로 통일한다.
    경제전망_YYYY-MM.pdf

기본은 미리보기(dry-run). 실제로 바꾸려면 --apply.
같은 대상 이름이 이미 있고 내용이 같으면(크기 동일) 원본을 삭제,
크기가 다르면 건드리지 않고 경고한다.

사용:
    uv run python mytrading/rename_eor.py           # 미리보기
    uv run python mytrading/rename_eor.py --apply    # 실제 적용
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
EOR = _ROOT / "mytrading" / "reports" / "bok" / "eor"

# 경제전망(2026.5월) / 경제전망(2026.05월) 에서 연·월 추출
_RE = re.compile(r"경제전망\((\d{4})\.\s*(\d{1,2})월\)")


def target_name(fname: str):
    m = _RE.search(fname)
    if not m:
        return None
    y, mo = m.group(1), int(m.group(2))
    return f"경제전망_{y}-{mo:02d}.pdf"


def main():
    apply = "--apply" in sys.argv[1:]
    if not EOR.exists():
        print(f"폴더 없음: {EOR}")
        return

    pdfs = sorted(EOR.glob("*.pdf"))
    print(f"대상 폴더: {EOR}")
    print(f"PDF {len(pdfs)}개\n")

    plan = []
    for p in pdfs:
        # 이미 목표 형식이면 건너뜀
        if re.fullmatch(r"경제전망_\d{4}-\d{2}\.pdf", p.name):
            continue
        tgt = target_name(p.name)
        if not tgt:
            print(f"  [건너뜀] 연월 추출 불가: {p.name}")
            continue
        plan.append((p, EOR / tgt))

    if not plan:
        print("바꿀 파일이 없습니다.")
        return

    for src, dst in plan:
        if dst.exists():
            same = dst.stat().st_size == src.stat().st_size
            if same:
                action = "원본 삭제(중복, 대상 이미 존재·크기 동일)"
            else:
                action = "⚠️ 대상 존재하나 크기 다름 → 건너뜀(수동 확인)"
        else:
            action = "이름 변경"
        print(f"  {src.name}")
        print(f"    → {dst.name}  [{action}]")

    if not apply:
        print("\n(미리보기. 실제 적용: --apply)")
        return

    print("\n적용 중...")
    for src, dst in plan:
        if dst.exists():
            if dst.stat().st_size == src.stat().st_size:
                src.unlink()
                print(f"  삭제: {src.name} (중복)")
            else:
                print(f"  건너뜀: {src.name} (대상과 크기 다름)")
        else:
            src.rename(dst)
            print(f"  변경: {src.name} → {dst.name}")

    print("\n완료. 결과:")
    for p in sorted(EOR.glob("경제전망_*.pdf")):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()