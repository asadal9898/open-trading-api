"""
뉴스레터 구독 만료 감시 (크론: 매월 초 주말).
Gmail 라벨의 최근 메일에서 만료/재신청 키워드를 감지하면 텔레그램 알림.

설정: mytrading_config.yaml 의 newsletter_watch (keywords, targets)
실행: uv run python mytrading/newsletter_check.py [--days N]
  --days N : 최근 N일 이내 메일만 검사 (기본 35 — 월 1회 실행 여유)
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mytrading.gmail_client import read_label
from mytrading import notify


def _load_config() -> dict:
    import yaml
    p = _REPO_ROOT / "mytrading" / "mytrading_config.yaml"
    cfg = yaml.safe_load(open(p, encoding="utf-8")) or {}
    return cfg.get("newsletter_watch", {}) or {}


def _imap_since(days: int) -> str:
    """IMAP SINCE 형식 날짜 (DD-Mon-YYYY)."""
    d = datetime.now() - timedelta(days=days)
    return d.strftime("%d-%b-%Y")


def check(days: int = 35) -> list:
    """감시 대상 라벨을 검사. 키워드 매칭된 메일 목록 반환."""
    cfg = _load_config()
    keywords = cfg.get("keywords", [])
    targets = cfg.get("targets", [])
    if not keywords or not targets:
        print("[newsletter] 설정(keywords/targets) 비어있음 — 건너뜀")
        return []

    since = _imap_since(days)
    hits = []
    for t in targets:
        label = t.get("label", "")
        name = t.get("name", label)
        try:
            mails = read_label(label, limit=30, since=since)
        except Exception as e:
            print(f"[newsletter] '{label}' 읽기 실패: {e}")
            continue
        for m in mails:
            text = (m.get("subject", "") + " " + m.get("body", ""))
            matched = [k for k in keywords if k in text]
            if matched:
                hits.append({
                    "name": name, "label": label,
                    "subject": m.get("subject", ""),
                    "date": m.get("date", ""),
                    "matched": matched,
                })
    return hits


def _is_last_sunday(d=None) -> bool:
    """오늘이 이번 달 마지막 주 일요일인지. (일요일 + 7일 뒤엔 다음 달)"""
    from datetime import date, timedelta
    d = d or date.today()
    if d.weekday() != 6:  # 6 = 일요일
        return False
    return (d + timedelta(days=7)).month != d.month


def _month_tag(d=None) -> str:
    """이번 달 태그. 예: 2026년 7월 → \'26.7월"""
    from datetime import date
    d = d or date.today()
    return f"\'{d.year % 100}.{d.month}월"


def check_kcif() -> bool:
    """이번 달 KCIF 리스크워치 메일 수신 여부. 미수신이면 알림 후 True 반환.
    (마지막 주 일요일에만 실제 체크. 그 외엔 아무것도 안 함.)"""
    import yaml
    cfg_all = yaml.safe_load(open(
        _REPO_ROOT / "mytrading" / "mytrading_config.yaml", encoding="utf-8")) or {}
    cfg = cfg_all.get("kcif_watch", {}) or {}
    if not cfg:
        return False

    if not _is_last_sunday():
        print("[kcif] 마지막 주 일요일 아님 — 체크 건너뜀")
        return False

    label = cfg.get("label", "경제/국제금융")
    keyword = cfg.get("subject_keyword", "리스크 워치")
    tag = _month_tag()

    try:
        mails = read_label(label, limit=30, since=_imap_since(35))
    except Exception as e:
        print(f"[kcif] '{label}' 읽기 실패: {e}")
        return False

    # 이번 달 리스크워치 메일 있나 (제목에 키워드 + 이번달 태그)
    found = False
    for m in mails:
        subj = m.get("subject", "")
        if keyword in subj and tag in subj:
            found = True
            break

    if found:
        print(f"[kcif] 이번 달({tag}) 리스크워치 수신 확인 — 정상")
        return False

    # 미수신 → 알림
    msg = f"📊 <b>KCIF 리스크워치 미수신</b>\n\n{cfg.get('message', '')}"
    ok = notify.send_message(msg)
    print(f"[kcif] 이번 달({tag}) 미수신 → 텔레그램 알림 {'성공' if ok else '실패'}")
    return True


def main():
    days = 35
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except (ValueError, IndexError):
            pass

    hits = check(days)
    if not hits:
        print(f"[newsletter] 최근 {days}일 내 만료/재신청 키워드 메일 없음")
    else:

        # 텔레그램 알림 구성
        lines = ["📬 <b>뉴스레터 재신청 확인 필요</b>", ""]
        for h in hits:
            kw = ", ".join(h["matched"])
            lines.append(f"• <b>{h['name']}</b>")
            lines.append(f"  제목: {h['subject'][:50]}")
            lines.append(f"  감지 키워드: {kw}")
            lines.append("")
        lines.append("메일을 확인하고 필요 시 재신청하세요.")
        msg = "\n".join(lines)
        ok = notify.send_message(msg)
        print(f"[newsletter] {len(hits)}건 감지 → 텔레그램 알림 {'성공' if ok else '실패'}")

    # KCIF 리스크워치 미수신 체크 (마지막 주 일요일에만 동작)
    check_kcif()


if __name__ == "__main__":
    main()
