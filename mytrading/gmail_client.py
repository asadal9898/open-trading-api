"""
Gmail 연동 클라이언트 (IMAP 읽기 + SMTP 발송).
앱 비밀번호 방식. 표준 라이브러리(imaplib, smtplib)만 사용.

설정: ~/KIS/config/kis_devlp.yaml 에
    my_GMAIL_address: "you@gmail.com"
    my_GMAIL_app_password: "앱비밀번호16자리"

사용:
    from mytrading.gmail_client import read_label, send_mail, list_labels
    mails = read_label("경제/한국은행", limit=10)   # 최근 10개
    for m in mails:
        print(m["date"], m["subject"], m["from"])
    send_mail("제목", "본문")                        # 나에게 발송
"""
import base64
import email
import imaplib
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Optional

_CONFIG = Path.home() / "KIS" / "config" / "kis_devlp.yaml"


def _creds():
    """(주소, 앱비밀번호) 로드."""
    import yaml
    cfg = yaml.safe_load(open(_CONFIG, encoding="utf-8"))
    addr = str(cfg.get("my_GMAIL_address", "")).strip()
    pw = str(cfg.get("my_GMAIL_app_password", "") or "").replace(" ", "")
    if not addr or not pw:
        raise RuntimeError("kis_devlp.yaml 에 my_GMAIL_address/app_password 필요")
    return addr, pw


def _utf7_encode(s: str) -> str:
    """유니코드 → IMAP modified UTF-7 (라벨명용)."""
    res, buf = "", ""

    def flush(b):
        if not b:
            return ""
        x = base64.b64encode(b.encode("utf-16-be")).decode().rstrip("=")
        return "&" + x.replace("/", ",") + "-"

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            res += flush(buf)
            buf = ""
            res += ch
        else:
            buf += ch
    return res + flush(buf)


def _dec_header(s: str) -> str:
    """MIME 인코딩 헤더 → 유니코드."""
    out = ""
    for txt, enc in decode_header(s):
        if isinstance(txt, bytes):
            out += txt.decode(enc or "utf-8", errors="replace")
        else:
            out += txt
    return out


def _body_text(mail) -> str:
    """메일 본문에서 텍스트 추출 (plain 우선, 없으면 html 태그 제거)."""
    plain, html = "", ""
    for part in mail.walk():
        ct = part.get_content_type()
        disp = str(part.get("Content-Disposition", ""))
        if "attachment" in disp:
            continue
        if ct == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                plain += payload.decode(part.get_content_charset() or "utf-8",
                                        errors="replace")
        elif ct == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                html += payload.decode(part.get_content_charset() or "utf-8",
                                       errors="replace")
    if plain.strip():
        return plain
    import re
    return re.sub(r"<[^>]+>", " ", html)


def _utf7_decode(s: str) -> str:
    """IMAP modified UTF-7 → 유니코드."""
    res, i = [], 0
    while i < len(s):
        if s[i] == "&":
            j = s.find("-", i)
            if j == i + 1:
                res.append("&")
                i = j + 1
            else:
                b64 = s[i + 1:j].replace(",", "/")
                pad = "=" * (-len(b64) % 4)
                try:
                    res.append(base64.b64decode(b64 + pad).decode("utf-16-be"))
                except Exception:
                    res.append(s[i:j + 1])
                i = j + 1
        else:
            res.append(s[i])
            i += 1
    return "".join(res)


def list_labels() -> list:
    """전체 라벨 목록 (한글 디코딩)."""
    addr, pw = _creds()
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(addr, pw)
    typ, boxes = M.list()
    M.logout()
    labels = []
    for b in boxes:
        line = b.decode("utf-8", errors="replace")
        name = line.split(' "/" ')[-1].strip('"') if ' "/" ' in line else line
        try:
            labels.append(_utf7_decode(name))
        except Exception:
            labels.append(name)
    return labels


def read_label(label: str, limit: int = 20, since: str = None) -> list:
    """특정 라벨의 메일 읽기 (최근순).
    label: 한글 라벨명 (예 '경제/한국은행').
    limit: 최대 개수. since: 'DD-Mon-YYYY' (예 '01-Jul-2026') 이후만.
    반환: [{subject, from, date, body}, ...] 최근→과거."""
    addr, pw = _creds()
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    M.login(addr, pw)
    typ, _ = M.select('"' + _utf7_encode(label) + '"', readonly=True)
    if typ != "OK":
        M.logout()
        raise RuntimeError(f"라벨 선택 실패: {label}")

    criteria = ["ALL"] if not since else ["SINCE", since]
    typ, data = M.search(None, *criteria)
    ids = data[0].split() if data[0] else []
    ids = ids[-limit:]

    out = []
    for i in reversed(ids):
        typ, msg = M.fetch(i, "(RFC822)")
        mail = email.message_from_bytes(msg[0][1])
        out.append({
            "subject": _dec_header(mail.get("Subject", "")),
            "from": _dec_header(mail.get("From", "")),
            "date": mail.get("Date", ""),
            "body": _body_text(mail),
        })
    M.logout()
    return out


def send_mail(subject: str, body: str, to: str = None) -> bool:
    """메일 발송. to 없으면 자기 자신에게. 성공 시 True."""
    addr, pw = _creds()
    to = to or addr
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = addr
        msg["To"] = to
        msg["Date"] = formatdate(localtime=True)
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        s.login(addr, pw)
        s.sendmail(addr, [to], msg.as_string())
        s.quit()
        return True
    except Exception as e:
        print(f"[gmail] 발송 실패: {e}")
        return False


if __name__ == "__main__":
    print("--- 라벨 목록 (경제만) ---")
    for lb in list_labels():
        if lb.startswith("경제"):
            print("  ", lb)
    print("--- 경제/한국은행 최근 메일 ---")
    for m in read_label("경제/한국은행", limit=5):
        print(f"  [{m['date'][:16]}] {m['subject'][:40]}")
