"""
경제 뉴스레터 AI 분석 — '국제금융'·'한국은행' 등 경제 라벨 메일을 읽어
시장 국면(regime)과 매수 영향도를 판정한다.

원천: Gmail 라벨 → 본문 + gmail_client 가 수집한 링크(웹페이지/PDF)
모델: Groq API (기본 llama-3.3-70b-versatile)

⚠️ 판정 결과를 파일로 남길 뿐 주문은 내지 않는다.
   현재 시스템에서 slice_pct 는 실제 주문 실행 경로에 연결되어 있지 않다.
   결과를 쓰려면 trade_plan/order_pace 가 아래 출력 파일을 읽도록 연결해야 한다.

사용:
    uv run --with pypdf --with beautifulsoup4 python mytrading/newsletter_ai.py
    ... --limit 3          라벨당 3건
    ... --no-fetch         링크 추적 끄기 (토큰 절약·안전)
    ... --model <모델명>

출력:
    mytrading/reports/newsletter_ai.json
"""
import base64
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

# ── 경로: 이 파일은 mytrading/ 안에 있다 ──────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BACKTESTER = _ROOT / "backtester"
if _BACKTESTER.exists() and str(_BACKTESTER) not in sys.path:
    sys.path.insert(0, str(_BACKTESTER))

try:
    from mytrading.common import init, CONFIG_PATH as COMMON_CFG_PATH
    from mytrading.gmail_client import read_label, list_labels
    from mytrading.order_pace import compute_slice
    from mytrading import notify
except ImportError as e:
    print(f"[오류] mytrading 내부 모듈을 로드할 수 없습니다: {e}")
    sys.exit(1)


# ── 설정 ────────────────────────────────────────────────────────
DEFAULT_MODEL = "llama-3.3-70b-versatile"
API_URL = "https://api.groq.com/openai/v1/chat/completions"

# 이미지 입력이 가능한 모델. 여기 없는 모델에 image_url 을 보내면 400 에러.
# (llama-3.3-70b-versatile 은 텍스트 전용)
VISION_MODELS = {
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
}

VALID_REGIMES = ("bull", "bear", "toppish", "sideways")

MAX_BODY_CHARS = 15000     # 프롬프트에 넣을 본문 상한
MAX_PDF_PAGES = 6          # PDF 앞부분만 (요약이 몰려 있음)
OUT_PATH = _ROOT / "mytrading" / "reports" / "newsletter_ai.json"

# 따라가면 안 되는 링크.
#   response.do 는 수신거부·클릭집계 추적 URL 일 수 있어 기본 차단한다.
#   (KCIF 메일의 링크가 이 형태라 리포트가 아닐 가능성이 높다)
BYPASS_KEYWORDS = (
    "subscribe", "unsubscribe", "reject", "mailto", "optout", "preferences",
    "response.do", "unsub", "withdraw",
    "twitter.com", "x.com", "facebook.com", "instagram.com",
    "linkedin.com", "youtube.com", "pinterest.com", "kakao.com",
)

# 리포트 본문이 있을 법한 경로 힌트 (우선순위 순)
PREFER_HINTS = ("filedown", "download", ".pdf", "view.do", "report", "bbs", "pub")


def _looks_bypass(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in BYPASS_KEYWORDS)


def _kind_of(subject: str) -> str:
    """뉴스레터 제목에서 종류를 뽑는다.

    국제금융센터 제목 예:
        [7월 25일 daily] / [7.22 daily] / [Weekly] / [Brief]
    대소문자·날짜 표기가 들쭉날쭉해 소문자 포함 여부로만 판정한다.
    """
    low = (subject or "").lower()
    if "daily" in low:
        return "daily"
    if "weekly" in low:
        return "weekly"
    if "brief" in low:
        return "brief"
    return "기타"


class GroqEconomyAnalyzer:
    def __init__(self, model: str = DEFAULT_MODEL, fetch_links: bool = True):
        self.api_key = self._load_api_key()
        self.model_name = model
        self.fetch_links = fetch_links
        self.supports_vision = model in VISION_MODELS

    # ── API 키 ──────────────────────────────────────────────────
    def _load_api_key(self) -> str:
        for cfg_path in (getattr(notify, "CONFIG_PATH", None), COMMON_CFG_PATH):
            if not cfg_path:
                continue
            cfg_path = Path(cfg_path)
            if not cfg_path.exists():
                continue
            try:
                import yaml
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                key = cfg.get("my_GROQ_API_KEY") or cfg.get("GROQ_API_KEY")
                if key:
                    return str(key).strip()
            except Exception:
                continue
        return os.environ.get("GROQ_API_KEY", "").strip()

    # ── 라벨 ────────────────────────────────────────────────────
    def get_economy_labels(self) -> list:
        targets = ("국제금융",)
        fallback = ["경제/국제금융센터"]
        try:
            all_labels = list_labels() or []
        except Exception as e:
            print(f"[경고] 라벨 목록 조회 실패 — 기본값 사용: {e}")
            return fallback
        found = [l for l in all_labels if any(t in l for t in targets)]
        return found or fallback

    # ── 링크 선택 ───────────────────────────────────────────────
    def _pick_url(self, text: str, links=None):
        """추적할 URL 하나 선택.

        gmail_client 가 links 를 주면 그것을 쓴다. 본문 텍스트에는 URL 이
        남지 않기 때문(HTML 태그 제거 과정에서 href 가 사라짐).
        """
        urls = list(links or [])
        if not urls:
            urls = re.findall(r'https?://[^\s\)\"\'\>\]]+', text or "")

        seen, cleaned = set(), []
        for u in urls:
            u = u.rstrip('.,;)')
            if not u or _looks_bypass(u) or u in seen:
                continue
            seen.add(u)
            cleaned.append(u)
        if not cleaned:
            return None

        for hint in PREFER_HINTS:
            for u in cleaned:
                if hint in u.lower():
                    return u
        return cleaned[0]

    # ── 외부 원문 수집 ──────────────────────────────────────────
    @staticmethod
    def _pdf_text(content: bytes) -> str:
        try:
            import pypdf
        except ImportError:
            print("  -> pypdf 미설치 (uv run --with pypdf ...)")
            return ""
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            pages = reader.pages[:MAX_PDF_PAGES]
            txt = "\n".join((p.extract_text() or "") for p in pages)
            if txt.strip():
                print(f"  -> PDF {len(pages)}쪽 파싱")
            return txt
        except Exception as e:
            print(f"  -> PDF 파싱 실패: {e}")
            return ""

    def fetch_external_link_content(self, text: str, links=None) -> str:
        target = self._pick_url(text, links)
        if not target:
            return ""

        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120"}
        try:
            print(f"  -> 링크 추적: {target[:80]}")
            res = requests.get(target, headers=headers, timeout=20)
            if res.status_code != 200:
                print(f"  -> 응답 {res.status_code} — 건너뜀")
                return ""

            ctype = res.headers.get("Content-Type", "").lower()
            if "application/pdf" in ctype or target.lower().split("?")[0].endswith(".pdf"):
                return self._pdf_text(res.content)

            try:
                from bs4 import BeautifulSoup
            except ImportError:
                return re.sub(r"<[^>]*>", " ", res.text)

            soup = BeautifulSoup(res.text, "html.parser")

            # 랜딩 페이지 안에 리포트 PDF 가 있으면 한 단계 더 따라간다.
            #   한국은행 view.do 같은 게시물 페이지는 첨부가 안쪽에 있다.
            pdf_href = None
            for a in soup.find_all("a", href=True):
                low = a["href"].lower()
                if low.split("?")[0].endswith(".pdf") or "filedown" in low:
                    pdf_href = urljoin(res.url, a["href"])
                    break
            if pdf_href:
                print(f"  -> 내부 PDF: {pdf_href[:80]}")
                try:
                    r2 = requests.get(pdf_href, headers=headers, timeout=25)
                    if r2.status_code == 200:
                        txt = self._pdf_text(r2.content)
                        if txt.strip():
                            return txt
                except Exception as e:
                    print(f"  -> 내부 PDF 실패: {e}")

            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(separator=" ")

        except Exception as e:
            print(f"  -> 링크 추적 실패: {e}")
        return ""

    @staticmethod
    def clean_text(text: str) -> str:
        if not text:
            return ""
        return " ".join(text.split())[:MAX_BODY_CHARS]

    # ── 프롬프트 ────────────────────────────────────────────────
    @staticmethod
    def build_prompt(label_name: str, subject: str, body: str) -> str:
        return f"""너는 거시경제 리서치 애널리스트다.
아래 금융 리포트를 읽고 현재 시장 국면과 매수 영향도를 판정하라.

[출처 라벨] {label_name}
[제목] {subject}
[본문]
{body}

판정 지침
1. 본문에 실제로 언급된 내용만 근거로 삼는다. 추측이나 일반론으로 채우지 않는다.
2. 시장 국면(regime)을 다음 중 하나로 판정한다.
   - bull      : 위험자산 선호가 뚜렷하고 상방 요인이 우세
   - bear      : 침체·긴축·신용경색 등 하방 요인이 우세
   - toppish   : 상승세이나 과열·정점 신호가 함께 관찰됨
   - sideways  : 방향성이 불분명하거나 근거가 불충분
   근거가 약하면 반드시 sideways 를 선택한다. 억지로 방향을 정하지 않는다.
3. market_impact_score 는 매수 관점의 영향도다.
   0.0 = 강한 악재, 0.5 = 중립, 1.0 = 강한 호재. 애매하면 0.5 부근.
4. confidence 는 이 판정의 확신도다 (0.0~1.0).
   본문이 짧거나 원문을 못 읽었으면 낮게 준다.
5. is_market_relevant 는 이 문서가 매매 판단에 쓸 만한 시황 정보인지다.
   학술 논문 소개·제도 설계 연구·기관 소식처럼 시장 방향과 무관하면 false.

아래 JSON 형식으로만 답한다. 부연 설명 금지.
{{
  "sentiment": "POSITIVE | NEGATIVE | NEUTRAL",
  "regime": "bull | bear | toppish | sideways",
  "market_impact_score": 0.0,
  "confidence": 0.0,
  "is_market_relevant": true,
  "key_points": ["본문에서 근거가 된 사실 1", "사실 2"],
  "reason": "그렇게 판정한 핵심 근거 1줄"
}}"""

    # ── API 호출 (429 재시도) ───────────────────────────────────
    def _post_with_retry(self, payload: dict, max_retry: int = 3):
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        for attempt in range(1, max_retry + 1):
            try:
                res = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            except requests.RequestException as e:
                if attempt == max_retry:
                    return None, f"요청 실패: {e}"
                time.sleep(3 * attempt)
                continue

            if res.status_code == 200:
                return res, None

            if res.status_code == 429:
                wait = res.headers.get("retry-after")
                try:
                    wait = float(wait)
                except (TypeError, ValueError):
                    wait = 20.0 * attempt
                if attempt == max_retry:
                    return None, f"레이트리밋(429) — {max_retry}회 재시도 후 포기"
                print(f"  -> 레이트리밋, {wait:.0f}초 대기 ({attempt}/{max_retry})")
                time.sleep(wait)
                continue

            if 500 <= res.status_code < 600 and attempt < max_retry:
                time.sleep(3 * attempt)
                continue

            return None, f"API 에러 {res.status_code}: {res.text[:200]}"
        return None, "재시도 한도 초과"

    # ── 개별 메일 분석 ──────────────────────────────────────────
    def analyze_mail(self, label_name: str, mail_item: dict) -> dict:
        body_text = mail_item.get("body", "") or ""
        subject = mail_item.get("subject", "제목 없음")
        mail_links = mail_item.get("links") or []

        fetched = False
        if self.fetch_links and (mail_links or "http" in body_text):
            external = self.fetch_external_link_content(body_text, mail_links)
            if external:
                body_text += "\n\n[링크 원문]\n" + external
                fetched = True

        body = self.clean_text(body_text)
        if len(body) < 50:
            return {"error": "본문이 너무 짧아 분석하지 않음", "body_chars": len(body)}

        prompt = self.build_prompt(label_name, subject, body)

        images = mail_item.get("image_paths") or []
        if isinstance(images, str):
            images = [images]
        images = [p for p in images if Path(p).exists()]
        if images and not self.supports_vision:
            print(f"  -> 이미지 {len(images)}장 무시 ({self.model_name} 텍스트 전용)")
            images = []

        if images:
            content = [{"type": "text", "text": prompt}]
            for img_path in images:
                p = Path(img_path)
                try:
                    b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
                except Exception:
                    continue
                sfx = p.suffix.lower().lstrip(".")
                mime = f"image/{'jpeg' if sfx in ('jpg', 'jpeg') else sfx}"
                content.append({"type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"}})
        else:
            content = prompt      # 텍스트만이면 문자열이 안전

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        res, err = self._post_with_retry(payload)
        if err:
            return {"error": err}

        try:
            choices = res.json().get("choices") or []
            if not choices:
                return {"error": "응답에 choices 가 없음"}
            raw = choices[0]["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            decision = json.loads(raw)
        except Exception as e:
            return {"error": f"응답 파싱 실패: {e}"}

        # 모델이 객체 대신 배열이나 래퍼를 줄 때가 있다
        if isinstance(decision, list):
            decision = next((x for x in decision if isinstance(x, dict)), None)
        if isinstance(decision, dict) and "regime" not in decision:
            for v in decision.values():          # {"result": {...}} 형태
                if isinstance(v, dict) and "regime" in v:
                    decision = v
                    break
        if not isinstance(decision, dict):
            return {"error": "응답이 예상 형식(JSON 객체)이 아님"}

        # ── 값 검증 ──
        regime = str(decision.get("regime", "sideways")).lower().strip()
        if regime not in VALID_REGIMES:
            regime = "sideways"
        decision["regime"] = regime

        def _clamp01(v, default=0.5):
            try:
                return max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                return default

        decision["market_impact_score"] = _clamp01(decision.get("market_impact_score"))
        decision["confidence"] = _clamp01(decision.get("confidence"))
        decision["is_market_relevant"] = bool(decision.get("is_market_relevant", True))

        # ── 주문 속도 참고값 ──
        # compute_slice 가 이미 regime 을 반영하므로 score 는 완만한 가중치로만 쓴다
        # (그대로 곱하면 국면 효과가 이중으로 들어감).
        try:
            base_slice = compute_slice("accumulate", regime, "buy")
        except Exception:
            base_slice = {"bull": 5.0, "bear": 1.5}.get(regime, 3.0)
        final_slice = round(base_slice * (0.5 + decision["market_impact_score"]), 3)

        return {
            "analysis": decision,
            "link_fetched": fetched,
            "pace_hint": {
                "regime": regime,
                "base_slice_pct": base_slice,
                "adjusted_slice_pct": final_slice,
            },
        }

    # ── 배치 실행 ───────────────────────────────────────────────
    def run(self, limit: int = 1, pause: float = 3.0) -> dict:
        if not self.api_key:
            print("[오류] Groq API 키를 찾을 수 없습니다 "
                  "(설정 yaml 의 my_GROQ_API_KEY 또는 환경변수 GROQ_API_KEY).")
            return {}

        labels = self.get_economy_labels()
        print(f"[대상 라벨] {labels}")
        print(f"[모델] {self.model_name}"
              f"{' (비전)' if self.supports_vision else ' (텍스트 전용)'}"
              f" · 링크추적 {'ON' if self.fetch_links else 'OFF'}")

        results = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "model": self.model_name, "labels": {}}

        for lbl in labels:
            print(f"\n>>> '{lbl}'")
            try:
                mails = read_label(lbl, limit=limit) or []
            except Exception as e:
                print(f"  -> 라벨 읽기 실패: {e}")
                results["labels"][lbl] = {"error": str(e)}
                continue
            if not mails:
                print("  -> 메일 없음")
                continue

            items = []
            for mail in mails:
                print(f"  - {mail.get('subject', '')[:60]}")
                ev = self.analyze_mail(lbl, mail)
                items.append({"subject": mail.get("subject"),
                              "date": mail.get("date"),
                              "kind": _kind_of(mail.get("subject")),
                              "evaluation": ev})
                if ev.get("error"):
                    print(f"    ! {ev['error']}")
                else:
                    a = ev["analysis"]
                    rel = "" if a["is_market_relevant"] else " [시황무관]"
                    print(f"    regime={a['regime']} "
                          f"score={a['market_impact_score']:.2f} "
                          f"conf={a['confidence']:.2f}{rel}")
                time.sleep(pause)

            results["labels"][lbl] = {"items": items}
            self._save(results)

        results["summary"] = self._summarize(results)
        results["summary_by_kind"] = {
            k: self._summarize(results, kind=k)
            for k in ("daily", "weekly", "brief")
        }
        self._save(results)
        return results

    @staticmethod
    def _summarize(results: dict, kind: str = None) -> dict:
        """시황 관련 문서만 모아 확신도 가중으로 종합한다.

        kind 를 주면 그 종류(daily/weekly/brief)만 골라 종합한다.
        """
        regimes, weighted, weights, n, skipped = [], 0.0, 0.0, 0, 0
        for lbl_data in results.get("labels", {}).values():
            for item in lbl_data.get("items", []):
                if kind is not None and item.get("kind") != kind:
                    continue
                a = (item.get("evaluation") or {}).get("analysis")
                if not a:
                    continue
                if not a.get("is_market_relevant", True):
                    skipped += 1
                    continue
                n += 1
                regimes.append(a["regime"])
                w = max(0.1, float(a.get("confidence", 0.5)))
                weighted += float(a["market_impact_score"]) * w
                weights += w
        if not n:
            return {"count": 0, "skipped": skipped, "regime": "sideways",
                    "score": 0.5, "note": "시황 관련 문서 없음"}
        counts = Counter(regimes)
        top, top_n = counts.most_common(1)[0]
        return {"count": n, "skipped": skipped, "regime": top,
                "regime_votes": dict(counts),
                "agreement": round(top_n / n, 2),
                "score": round(weighted / weights, 3) if weights else 0.5}

    @staticmethod
    def _save(results: dict) -> None:
        try:
            OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        except Exception as e:
            print(f"[경고] 결과 저장 실패: {e}")


def main():
    args = sys.argv[1:]

    def opt(name, default=None, cast=str):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                try:
                    return cast(args[i + 1])
                except ValueError:
                    pass
        return default

    try:
        init(require_confirm=False)
    except Exception:
        pass

    analyzer = GroqEconomyAnalyzer(model=opt("--model", DEFAULT_MODEL),
                                   fetch_links=("--no-fetch" not in args))
    results = analyzer.run(limit=opt("--limit", 10, int),
                           pause=opt("--pause", 3.0, float))

    s = results.get("summary")
    if s:
        extra = f", 시황무관 {s['skipped']}건 제외" if s.get("skipped") else ""
        print("\n" + "=" * 58)
        print(f"  종합 국면: {s['regime']}  "
              f"(동의율 {s.get('agreement', 0):.0%}, {s['count']}건{extra})")
        print(f"  영향도 점수: {s['score']:.2f}  (0=악재 · 0.5=중립 · 1=호재)")
        print(f"  저장: {OUT_PATH}")
        print("=" * 58)
        bk = results.get("summary_by_kind") or {}
        shown = [(k, v) for k, v in bk.items() if v.get("count")]
        if shown:
            print("  ── 종류별 ──")
            for k, v in shown:
                print(f"    {k:6} {v['regime']:8} "
                      f"score={v['score']:.2f} ({v['count']}건)")
        print("  ※ 참고용입니다. slice_pct 는 실제 주문 실행에 연결돼 있지 않습니다.")


if __name__ == "__main__":
    main()