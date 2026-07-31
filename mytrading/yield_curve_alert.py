# -*- coding: utf-8 -*-
"""
장단기 금리차(기간스프레드) 경고 — 리스크 관리용.

장단기 금리 역전(장기<단기)은 경기침체 선행지표로 해석된다(정설).
단, 예측력은 "제한적"이므로(전귀환·윤선중 2024), 매매 타이밍이 아니라
"위험 국면 경계" 참고 지표로만 사용한다.

데이터: KIS comp_interest [국내주식-155] 금리종합
  - output1: 미국·일본·독일 금리 (미국 10년·1년)
  - output2(cls='3'): 한국 국고채 전만기 (1년·3년·10년 등)
  ※ 현재 스냅샷만 제공(시계열 아님) → 과거 백테스트는 별도 소스 필요.

사용:
  from mytrading.yield_curve_alert import build_yield_curve_alert, send_yield_curve_alert
  msg = build_yield_curve_alert()          # 메시지 텍스트만
  send_yield_curve_alert()                 # 텔레그램 전송까지

  # 단독 실행
  uv run python mytrading/yield_curve_alert.py          # 현황 출력
  uv run python mytrading/yield_curve_alert.py --send   # 텔레그램 전송
"""
import sys
from pathlib import Path
from datetime import date

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# comp_interest 예제 경로 추가
_CI_DIR = _REPO_ROOT / "examples_llm" / "domestic_stock" / "comp_interest"
if str(_CI_DIR) not in sys.path:
    sys.path.insert(0, str(_CI_DIR))


# ── 판정 기준 (%p) ──────────────────────────────
NORMAL_MIN = 0.5     # 이 이상이면 정상
# 0 ~ 0.5 : 경계(평탄화)
# < 0     : 역전(경고)

def _grade(spread: float) -> tuple:
    """금리차(%p) → (이모지, 라벨)"""
    if spread is None:
        return ("⚪", "데이터없음")
    if spread < 0:
        return ("🔴", "역전")
    if spread < NORMAL_MIN:
        return ("🟡", "경계")
    return ("🟢", "정상")


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def fetch_rates() -> dict:
    """
    comp_interest 호출 → 필요한 금리만 추출.
    반환: {'us_10y':4.68, 'us_1y':4.04, 'kr_10y':4.249, 'kr_3y':3.776, 'kr_1y':3.372, 'date':'20260731'}
    실패 시 해당 키는 None.
    """
    from comp_interest import comp_interest
    out = {'us_10y': None, 'us_1y': None,
           'kr_10y': None, 'kr_3y': None, 'kr_1y': None, 'date': None}

    df1, df2 = comp_interest(fid_cond_mrkt_div_code='I',
                             fid_cond_scr_div_code='20702',
                             fid_div_cls_code='3', fid_div_cls_code1='')

    # output1: 미국 금리 (종목명 매칭)
    if df1 is not None and not df1.empty:
        for _, r in df1.iterrows():
            name = str(r.get('hts_kor_isnm', ''))
            val = _to_float(r.get('bond_mnrt_prpr'))
            if '미국' in name and '10년' in name:
                out['us_10y'] = val
            elif '미국' in name and '1년' in name:
                out['us_1y'] = val
            if out['date'] is None:
                out['date'] = str(r.get('stck_bsop_date', '')) or None

    # output2: 한국 국고채 (종목명 매칭)
    if df2 is not None and not df2.empty:
        for _, r in df2.iterrows():
            name = str(r.get('hts_kor_isnm', ''))
            val = _to_float(r.get('bond_mnrt_prpr'))
            if '국고채' in name:
                if '10년' in name:
                    out['kr_10y'] = val
                elif '3년' in name:
                    out['kr_3y'] = val
                elif '1년' in name:
                    out['kr_1y'] = val
            d = str(r.get('stck_bsop_date', ''))
            if d and d.isdigit() and out['date'] is None:
                out['date'] = d

    return out


def _spread_line(label: str, long_r, short_r) -> str:
    """한 줄: '10년-3년: +0.47%p 🟢 정상'"""
    if long_r is None or short_r is None:
        return f"  {label}: 데이터없음 ⚪"
    sp = long_r - short_r
    emoji, tag = _grade(sp)
    sign = "+" if sp >= 0 else ""
    return f"  {label}: {sign}{sp:.2f}%p {emoji} {tag}"


def build_yield_curve_alert() -> str:
    """장단기 금리차 현황 메시지 생성 (HTML)."""
    try:
        r = fetch_rates()
    except Exception as e:
        return f"<b>📊 장단기 금리차</b>\n조회 실패: {e}"

    d = r.get('date') or date.today().strftime("%Y%m%d")
    dstr = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(str(d)) == 8 else str(d)

    # 금리차 계산
    kr_10_3 = (r['kr_10y'] - r['kr_3y']) if (r['kr_10y'] and r['kr_3y']) else None
    kr_10_1 = (r['kr_10y'] - r['kr_1y']) if (r['kr_10y'] and r['kr_1y']) else None
    us_10_1 = (r['us_10y'] - r['us_1y']) if (r['us_10y'] and r['us_1y']) else None

    lines = [f"<b>📊 장단기 금리차</b> ({dstr})", ""]

    lines.append("🇰🇷 <b>한국 국고채</b>")
    lines.append(_spread_line("10년-3년", r['kr_10y'], r['kr_3y']))
    lines.append(_spread_line("10년-1년", r['kr_10y'], r['kr_1y']))
    if r['kr_10y'] and r['kr_3y'] and r['kr_1y']:
        lines.append(f"    (10년 {r['kr_10y']:.2f} · 3년 {r['kr_3y']:.2f} · 1년 {r['kr_1y']:.2f})")
    lines.append("")

    lines.append("🇺🇸 <b>미국 국채</b>")
    lines.append(_spread_line("10년-1년", r['us_10y'], r['us_1y']))
    if r['us_10y'] and r['us_1y']:
        lines.append(f"    (10년 {r['us_10y']:.2f} · 1년 {r['us_1y']:.2f})")
    lines.append("")

    # 종합 판정 — 역전이 하나라도 있으면 경고
    spreads = {'한국 10-3': kr_10_3, '한국 10-1': kr_10_1, '미국 10-1': us_10_1}
    inverted = [k for k, v in spreads.items() if v is not None and v < 0]
    watch = [k for k, v in spreads.items() if v is not None and 0 <= v < NORMAL_MIN]

    if inverted:
        lines.append(f"🔴 <b>역전 발생: {', '.join(inverted)}</b>")
        lines.append("→ 경기침체 선행 신호. 리스크 경계 모드 권고")
        lines.append("  (신규매수 신중·현금비중 점검. 단, 예측력은 제한적)")
    elif watch:
        lines.append(f"🟡 <b>평탄화 진행: {', '.join(watch)}</b>")
        lines.append("→ 금리차 축소 중. 추이 관찰")
    else:
        lines.append("🟢 역전 없음. 정상 국면")

    return "\n".join(lines)


def send_yield_curve_alert(silent: bool = False) -> bool:
    """금리차 현황을 텔레그램으로 전송."""
    from mytrading import notify
    msg = build_yield_curve_alert()
    return notify.send_message(msg, silent=silent, html_mode=True)


if __name__ == "__main__":
    # 인증 초기화
    from mytrading import common
    common.init()

    if "--send" in sys.argv:
        ok = send_yield_curve_alert()
        print("텔레그램 전송:", "성공" if ok else "실패")
    else:
        # 현황 출력 (HTML 태그 제거해서 콘솔용)
        import re
        msg = build_yield_curve_alert()
        print(re.sub(r"</?b>", "", msg))

def build_yield_curve_section() -> str:
    """
    주간 리포트용 섹션. 조회 실패 시 빈 문자열(리포트 전체는 유지).
    weekly_report.build_message 에서 호출.
    """
    try:
        return build_yield_curve_alert()
    except Exception as e:
        print(f"[yield_curve] 섹션 생성 실패: {e}")
        return ""
