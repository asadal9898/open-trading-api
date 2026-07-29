"""
차트 평가 — 종목 평가의 '차트' 절반.

원칙 (차영석 확정): 같은 차트를 스타일별로 정반대로 해석한다.
  momentum    (공격적) — 추세를 따라간다      → 정배열(5>20>60)이 좋음
  value_range (배당주) — 소외되어 싸질 때 산다 → 52주 저점 근처가 좋음
  accumulate  (ETF)    — 20일선 위 (적립식이라 느슨). 섹터 전망은 별도(미구현).

기준은 config chart_rules 에서 조정 가능.

사용:
    from mytrading.chart_eval import evaluate_chart
    r = evaluate_chart("005930", style="momentum")
    print(r["passed"], r["score"], r["note"])
"""
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_chart_rules():
    """config의 chart_rules 로드."""
    default = {
        "momentum": {
            "require_perfect_order": True,
            "score_perfect_order": 2,
            "score_slope_up": 1,
            "score_above_ma20": 1,
            "score_below_ma20": -1,
        },
        "value_range": {"require_buy_zone": True},
        "accumulate": {"skip_chart": False, "require_above_ma20": True},
    }
    try:
        import yaml
        cfg_path = _REPO_ROOT / "mytrading" / "configs" / "mytrading_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        r = cfg.get("chart_rules")
        if r and isinstance(r, dict):
            return r
    except Exception:
        pass
    return default


_CHART_RULES = _load_chart_rules()


def _perfect_order(t: dict) -> Optional[bool]:
    """정배열 판정: 5일선 > 20일선 > 60일선.
    상승 추세의 전형. 이미 상당 기간 오른 뒤에 나타남 (늦지만 확실).
    """
    ma5, ma20, ma60 = t.get("ma5"), t.get("ma20"), t.get("ma60")
    if None in (ma5, ma20, ma60):
        return None
    return ma5 > ma20 > ma60


def evaluate_chart(symbol: str, style: str = "momentum") -> Optional[dict]:
    """차트 평가 (스타일별 기준).

    momentum:
      필수 — 정배열 (5 > 20 > 60)
      점수 — 정배열 +2 / 기울기 상승 +1 (총 +3) / 20일선 위 +1 / 아래 -1
      ※ 정배열이어도 기울기가 꺾이면 통과하되 점수가 낮음 (우선순위 밀림)

    value_range:
      필수 — 52주 저점 근처 (value_range_signal == "buy")
      "소외되어 싸질 때 산다"

    accumulate (ETF):
      필수 — 20일선 위 (적립식이라 느슨)
      ※ 섹터 전망은 별도 판단 필요 (미구현)

    반환: {symbol, style, passed, score, detail, note} | None(데이터 없음)
    """
    rules = _CHART_RULES.get(style, {}) or {}

    # --- value_range: 52주 저점 근처인가 ---
    if style == "value_range":
        try:
            from mytrading.order_pace import value_range_signal
            sig = value_range_signal(symbol)
        except Exception as e:
            return {"symbol": symbol, "style": style, "passed": False,
                    "score": None, "detail": None,
                    "note": f"차트 데이터 없음 ({e})"}
        action = (sig or {}).get("action")
        passed = (action == "buy")
        rng = (sig or {}).get("range") or {}
        pct_low = rng.get("pct_from_low")
        score = None
        if pct_low is not None:
            # 저점에 가까울수록 높은 점수 (저점 +0% → +3, 멀수록 감소)
            score = max(-1, round(3 - pct_low / 5))
        note = f"{(sig or {}).get('reason', '판단 불가')}"
        return {"symbol": symbol, "style": style, "passed": passed,
                "score": score, "detail": sig, "note": note}

    # --- momentum / accumulate: 이동평균 기반 ---
    try:
        from mytrading.data_manager import get_trend
        t = get_trend(symbol)
    except Exception as e:
        t = None
    if not t:
        return {"symbol": symbol, "style": style, "passed": False,
                "score": None, "detail": None,
                "note": "차트 데이터 없음 (일봉 CSV 미보유 — daily_update 필요)"}

    po = _perfect_order(t)
    above20 = t.get("above_ma20")
    slope = t.get("ma5_slope")
    slope_up = (slope is not None and slope > 0)

    if style == "accumulate":
        passed = bool(above20)
        score = 1 if above20 else -1
        note = (f"20일선 {'위' if above20 else '아래'} "
                f"(적립식 — 느슨한 기준). ※섹터 전망 별도 판단 필요")
        return {"symbol": symbol, "style": style, "passed": passed,
                "score": score, "detail": t, "note": note}

    # momentum
    score = 0
    if po:
        score += rules.get("score_perfect_order", 2)
        if slope_up:
            score += rules.get("score_slope_up", 1)
    elif above20:
        score += rules.get("score_above_ma20", 1)
    else:
        score += rules.get("score_below_ma20", -1)

    passed = bool(po) if rules.get("require_perfect_order", True) else bool(above20)

    parts = []
    parts.append("정배열(5>20>60)" if po else
                 ("20일선 위" if above20 else "20일선 아래"))
    if slope is not None:
        parts.append(f"5일선 기울기 {'상승' if slope_up else '하락/횡보'}")
    note = " · ".join(parts)

    return {"symbol": symbol, "style": style, "passed": passed,
            "score": score, "detail": t, "note": note}


if __name__ == "__main__":
    import time
    tests = [
        ("005930", "삼성전자", "momentum"),
        ("009680", "모토닉", "value_range"),
        ("379800", "KODEX 미국S&P500", "accumulate"),
    ]
    print("=== 차트 평가 ===")
    for code, name, style in tests:
        r = evaluate_chart(code, style=style)
        if r:
            mark = "통과" if r["passed"] else "탈락"
            sc = f" {r['score']:+d}" if r.get("score") is not None else ""
            print(f"  [{name}] ({style}) {mark}{sc}")
            print(f"     {r['note']}")
        time.sleep(0.5)
