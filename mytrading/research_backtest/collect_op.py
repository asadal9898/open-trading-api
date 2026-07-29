"""59종목 × 7년 분기 영업이익 수집 → JSON 저장 (백테스트용)"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

import yaml
from mytrading.dart_data import _get_dart

d = _get_dart()
OUT = Path("/tmp/op_history.json")

uni = yaml.safe_load(open("mytrading/universe.yaml", encoding="utf-8"))
stocks = []
for cat in ("safe", "moderate", "aggressive"):
    for it in (uni.get(cat) or []):
        stocks.append((it.get("code"), it.get("name"), it.get("style", "momentum")))

def quarterly_op(code, year):
    out = {}
    for reprt, q in [("11013","Q1"), ("11012","Q2"), ("11014","Q3"), ("11011","FY")]:
        try:
            df = d.finstate_all(code, year, reprt_code=reprt, fs_div="CFS")
            if df is None or df.empty:
                out[q] = None; continue
            op = df[df["account_nm"].str.strip() == "영업이익"]
            if op.empty:
                op = df[df["account_nm"].str.contains("영업이익", na=False)]
            v = str(op.iloc[0].get("thstrm_amount","")).replace(",","") if not op.empty else ""
            out[q] = float(v)/1e8 if v else None      # 억원
        except Exception:
            out[q] = None
        time.sleep(0.2)
    if all(out.get(k) is not None for k in ("Q1","Q2","Q3","FY")):
        out["Q4"] = round(out["FY"] - (out["Q1"]+out["Q2"]+out["Q3"]), 1)
    else:
        out["Q4"] = None
    return out

data = {}
if OUT.exists():
    data = json.loads(OUT.read_text())      # 이어받기

for i, (code, name, style) in enumerate(stocks, 1):
    if code in data:
        print(f"[{i}/{len(stocks)}] {name} — 이미 있음, 건너뜀", flush=True)
        continue
    print(f"[{i}/{len(stocks)}] {name} 수집 중...", flush=True)
    hist = {}
    for year in range(2019, 2026):
        hist[str(year)] = quarterly_op(code, year)
    data[code] = {"name": name, "style": style, "op": hist}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))

print()
print(f"완료: {len(data)}종목 → {OUT}")
