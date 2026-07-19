"""
종목 평가 — 재무 + 차트 결합 (AND).

원칙 (차영석 확정):
  매수 = 재무 좋음 AND 차트 좋음
  - 재무만 좋고 차트 나쁘면 → 매수 안 함 (아직 시장이 인정 안 함)
  - 차트만 좋고 재무 나쁘면 → 매수 안 함 (수급만의 상승, 위험)

스타일별로 기준이 다름 (config style_rules / chart_rules):
  value_range  (배당주) — 재무: 부채+영업이익+배당 / 차트: 52주 저점 근처 (싸게 사기)
  momentum     (공격적) — 재무: 부채+영업이익(+10%↑) / 차트: 정배열 (추세 따라가기)
  accumulate   (ETF)    — 재무: 스킵 / 차트: 20일선 위
  free_holdings(자유투자) — 알림만, 사람이 판단

두 층 구조:
  1. 관문(필터): 재무 통과 AND 차트 통과 → 매수 후보
  2. 점수(순위): 후보들 중 뭐가 더 좋은가 (재무점수 + 차트점수)

사용:
    from mytrading.stock_eval import evaluate_stock
    r = evaluate_stock("005930", style="momentum", industry="반도체 제조업")
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def evaluate_stock(symbol, style="momentum", industry=None,
                   market="kospi", init_kis=False):
    """종목 종합 평가 = 재무 AND 차트.

    반환: {
      symbol, style, buy(매수 후보 여부), score(재무+차트),
      financial(재무 평가), chart(차트 평가), note
    }
    """
    from mytrading.finance_data import evaluate_financials
    from mytrading.chart_eval import evaluate_chart

    fin = evaluate_financials(symbol, style=style, industry=industry,
                              market=market, init_kis=init_kis)
    cht = evaluate_chart(symbol, style=style)

    fin_ok = fin.get("passed") if fin else None
    cht_ok = cht.get("passed") if cht else None
    fin_skip = bool(fin and fin.get("skipped"))
    advisory = bool(fin and fin.get("advisory"))

    # 국면 게이트 (value_range 전용) — 침체·판정불가면 매수 보류.
    #   백테스트 검증: "52주 저점+5%(차트) AND 국면OK" 조합이 승률 92%.
    #   passed(종목 품질) 는 건드리지 않고 buy(매수 판단) 에만 적용 → 관심사 분리.
    from mytrading.finance_data import is_buyable_phase
    phase = ((fin or {}).get("op") or {}).get("phase")
    phase_ok = True
    if style == "value_range":
        phase_ok = is_buyable_phase(phase)

    # 재무 스킵(ETF) → 차트만으로 판단
    if fin_skip:
        buy = bool(cht_ok)
    elif advisory:
        buy = None                      # 자유투자 — 사람이 판단
    else:
        buy = bool(fin_ok and cht_ok and phase_ok)   # AND

    fs = fin.get("score") if fin else None
    cs = cht.get("score") if cht else None
    score = None
    if fs is not None or cs is not None:
        score = (fs or 0) + (cs or 0)

    f_mark = "스킵" if fin_skip else ("O" if fin_ok else "X")
    c_mark = "O" if cht_ok else "X"
    head = ("[참고]" if advisory else
            ("★매수후보" if buy else "-"))
    p_mark = ""
    if style == "value_range" and phase:
        p_mark = f" / 국면 {phase}" + ("" if phase_ok else " ✗매수보류")
    note = (f"{head} 재무 {f_mark} / 차트 {c_mark}{p_mark}"
            + (f" (점수 {score:+d})" if score is not None else ""))

    return {
        "symbol": symbol, "style": style,
        "buy": buy, "score": score,
        "financial": fin, "chart": cht,
        "note": note,
    }
