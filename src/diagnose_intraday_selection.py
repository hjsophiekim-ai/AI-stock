"""장중 매수 Top20 필터 선정 결과 진단 CLI.

실행 예시:
  python src/diagnose_intraday_selection.py
  python src/diagnose_intraday_selection.py --date 20260615
  python src/diagnose_intraday_selection.py --show-filtered
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

_SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SRC_DIR.parent
sys.path.insert(0, str(_SRC_DIR))

from utils import get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/diagnose_intraday_selection.log")
cfg = load_config(str(PROJECT_ROOT / "config.yaml"))


def diagnose(date_str: str, show_filtered: bool = False) -> dict:
    preds_dir = PROJECT_ROOT / "reports" / "predictions"
    buy20_path = preds_dir / f"buy_top20_{date_str}.csv"
    top100_path = preds_dir / f"top100_{date_str}.csv"

    print(f"\n{'='*60}", flush=True)
    print(f"  장중 매수 Top20 선정 진단 — {date_str}", flush=True)
    print(f"{'='*60}", flush=True)

    top100_exists = top100_path.exists()
    buy20_exists = buy20_path.exists()

    print(f"\n[파일 상태]", flush=True)
    print(f"  top100_{date_str}.csv: {'✅ 존재' if top100_exists else '❌ 없음'}", flush=True)
    print(f"  buy_top20_{date_str}.csv: {'✅ 존재' if buy20_exists else '❌ 없음'}", flush=True)

    result = {
        "date": date_str,
        "top100_exists": top100_exists,
        "buy20_exists": buy20_exists,
    }

    if top100_exists:
        df100 = pd.read_csv(top100_path)
        print(f"\n[Top100 정보]", flush=True)
        print(f"  종목 수: {len(df100)}", flush=True)
        code_col = "stock_code" if "stock_code" in df100.columns else "ticker"
        print(f"  코드 컬럼: {code_col}", flush=True)
        cols = list(df100.columns)
        print(f"  컬럼 목록: {cols}", flush=True)
        result["top100_count"] = len(df100)

    if buy20_exists:
        df20 = pd.read_csv(buy20_path)
        print(f"\n[buy_top20 정보]", flush=True)
        print(f"  선정 종목 수: {len(df20)}", flush=True)

        code_col = "stock_code" if "stock_code" in df20.columns else "ticker"
        name_col = "stock_name" if "stock_name" in df20.columns else None

        for i, row in df20.iterrows():
            code = str(row.get(code_col, "?"))
            name = str(row.get(name_col, "")) if name_col else ""
            score = row.get("final_buy_score", "?")
            rank = row.get("final_buy_rank", i + 1)
            cr = row.get("change_rate", "?")
            hp = row.get("high_proximity", "?")
            tv = row.get("trading_value_intraday", row.get("trading_value", "?"))
            src = row.get("_data_source", "?")
            fail = row.get("filter_fail_reason", "")
            print(
                f"  [{rank:2d}] {code} {name:12s} | score={score:6.1f} "
                f"| cr={cr:5.1f}% hp={hp:.3f} tv={int(tv or 0):,.0f} | src={src} | fail={fail}",
                flush=True,
            )

        result["buy20_count"] = len(df20)
        result["allocation_version"] = df20["allocation_version"].iloc[0] if "allocation_version" in df20.columns else ""

        if show_filtered and top100_exists:
            df100 = pd.read_csv(top100_path)
            code_col100 = "stock_code" if "stock_code" in df100.columns else "ticker"
            df100[code_col100] = df100[code_col100].astype(str).str.zfill(6)
            df20[code_col] = df20[code_col].astype(str).str.zfill(6)
            selected = set(df20[code_col])
            filtered_out = df100[~df100[code_col100].isin(selected)]
            print(f"\n[필터 제외 종목 ({len(filtered_out)}개)]", flush=True)
            for i, row in filtered_out.iterrows():
                print(f"  - {str(row.get(code_col100,'?')).zfill(6)} {row.get('stock_name','')}", flush=True)

    intra_cfg = cfg.get("intraday_filter", {})
    print(f"\n[활성 필터 설정]", flush=True)
    for k, v in intra_cfg.items():
        if k != "score_weights":
            print(f"  {k}: {v}", flush=True)

    print(f"\n{'='*60}\n", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description="장중 Top20 선정 결과 진단")
    parser.add_argument("--date", default=None, help="YYYYMMDD (기본: 오늘)")
    parser.add_argument("--show-filtered", action="store_true", help="필터 제외 종목도 표시")
    args = parser.parse_args()

    date_str = args.date or get_today_str("%Y%m%d")
    result = diagnose(date_str, show_filtered=args.show_filtered)

    if not result["buy20_exists"]:
        print("buy_top20 파일이 없습니다. 먼저 실행:", flush=True)
        print(f"  python src/select_intraday_buy_candidates.py --mode paper --date {date_str}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
