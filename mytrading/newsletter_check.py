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


def _seen_file():
    """이미 알린 점검공지 기록 파일 경로."""
    from pathlib import Path
    return Path.home() / "KIS" / "cache" / "kis_maint_seen.json"


def _load_seen() -> set:
    import json
    f = _seen_file()
    if f.exists():
        try:
            return set(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_seen(seen: set):
    import json
    f = _seen_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")


def _extract_maint_schedule(text: str) -> str:
    """OCR 텍스트에서 점검 일시 추출. 예: '7월 4일 08:00 ~ 7월 5일 12:00'."""
    import re
    pat = (r"(\d{1,2})월\s*(\d{1,2})일.*?(\d{1,2}):(\d{2})\s*~\s*"
           r"(\d{1,2})월\s*(\d{1,2})일.*?(\d{1,2}):(\d{2})")
    m = re.search(pat, text)
    if not m:
        return ""
    g = m.groups()
    return f"{g[0]}월 {g[1]}일 {g[2]}:{g[3]} ~ {g[4]}월 {g[5]}일 {g[6]}:{g[7]}"


def check_kis_maintenance() -> bool:
    """한국투자증권 서버점검 공지 감시.
    라벨 메일의 이미지를 OCR 해 점검 일시 추출 → 새 공지면 텔레그램 알림.
    새 알림을 보냈으면 True."""
    import re
    import yaml
    cfg_all = yaml.safe_load(open(
        _REPO_ROOT / "mytrading" / "mytrading_config.yaml", encoding="utf-8")) or {}
    cfg = cfg_all.get("kis_maintenance_watch", {}) or {}
    if not cfg:
        return False

    label = cfg.get("label", "경제/한국투자증권")
    kw = cfg.get("subject_keyword", "중단 안내")
    img_host = cfg.get("img_host", "securities.koreainvestment.com")

    # 라벨 메일 읽기 (본문 원본 HTML 필요 → gmail_client 저수준 재사용)
    import imaplib, email
    from mytrading.gmail_client import _creds, _utf7_encode, _dec_header
    try:
        addr, pw = _creds()
        M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        M.login(addr, pw)
        M.select('"' + _utf7_encode(label) + '"', readonly=True)
        typ, data = M.search(None, "ALL")
        ids = data[0].split() if data[0] else []
    except Exception as e:
        print(f"[maint] 라벨 읽기 실패: {e}")
        return False

    seen = _load_seen()
    new_alert = False

    for i in reversed(ids[-10:]):  # 최근 10개
        typ, msg = M.fetch(i, "(RFC822)")
        mail = email.message_from_bytes(msg[0][1])
        subj = _dec_header(mail.get("Subject", ""))
        if kw not in subj:
            continue
        # HTML에서 점검 이미지 URL 추출
        img_url = None
        for part in mail.walk():
            if part.get_content_type() == "text/html":
                h = part.get_payload(decode=True).decode("utf-8", errors="replace")
                for src in re.findall(r'<img[^>]+src="([^"]+)"', h):
                    if img_host in src:
                        img_url = src
                        break
        if not img_url or img_url in seen:
            continue

        # 이미지 다운로드 → OCR → 일시 추출
        schedule = ""
        try:
            import requests, pytesseract, io
            from PIL import Image
            r = requests.get(img_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                img = Image.open(io.BytesIO(r.content))
                ocr = pytesseract.image_to_string(img, lang="kor")
                schedule = _extract_maint_schedule(ocr)
        except Exception as e:
            print(f"[maint] OCR 실패: {e}")

        # 텔레그램 알림
        lines = ["🔧 <b>한국투자증권 서버점검 공지</b>", ""]
        lines.append(f"제목: {subj[:55]}")
        if schedule:
            lines.append(f"⏰ 점검 일시: <b>{schedule}</b>")
        else:
            lines.append("⚠️ 일시 자동추출 실패 — 메일 이미지를 직접 확인하세요.")
        lines.append("")
        lines.append("점검 중엔 KIS API가 막혀요. 봇 운영·주문에 주의하세요.")
        ok = notify.send_message("\n".join(lines))
        print(f"[maint] 점검공지 알림 {'성공' if ok else '실패'} — {schedule or '일시추출실패'}")

        seen.add(img_url)
        new_alert = True

    M.logout()
    if new_alert:
        _save_seen(seen)
    else:
        print("[maint] 새 점검공지 없음")
    return new_alert



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
    # 한국투자증권 서버점검 공지 감시
    check_kis_maintenance()


if __name__ == "__main__":
    main()
