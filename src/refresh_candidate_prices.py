"""AI 후보 리스트 현재가 갱신 스크립트.

top100/top50/top20 CSV의 close 컬럼을 KIS API 현재가로 갱신합니다.

사용법:
    python refresh_candidate_prices.py
    python refresh_candidate_prices.py --date 20260609
    python refresh_candidate_prices.py --date 20260609 --top 100
    python refresh_candidate_prices.py --dry-run    # API 호출 없이 파일 상태만 확인
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd
from utils import load_config, setup_logger
from kis_api import KISApiClient

logger = setup_logger(__name__, "logs/refresh_candidate_prices.log")

CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")
PREDICTIONS_DIR = PROJECT_ROOT / "reports" / "predictions"


def get_today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def find_candidate_file(date_str: str, top_n: int) -> Path | None:
    path = PREDICTIONS_DIR / f"top{top_n}_{date_str}.csv"
    return path if path.exists() else None


def refresh_prices(date_str: str, top_n: int, dry_run: bool = False) -> dict:
    """후보 CSV의 현재가를 KIS API로 갱신.

    Args:
        date_str: 날짜 (YYYYMMDD)
        top_n: 100 / 50 / 20
        dry_run: True이면 API 호출 없이 파일 상태만 확인

    Returns:
        결과 dict
    """
    path = find_candidate_file(date_str, top_n)
    if path is None:
        return {"success": False, "message": f"파일 없음: top{top_n}_{date_str}.csv", "updated": 0}

    df = pd.read_csv(path)
    if df.empty:
        return {"success": False, "message": "빈 파일", "updated": 0}

    code_col = "stock_code" if "stock_code" in df.columns else ("ticker" if "ticker" in df.columns else None)
    if code_col is None:
        return {"success": False, "message": "종목코드 컬럼(stock_code/ticker) 없음", "updated": 0}

    print(f"\n현재가 갱신 시작: {path.name}  ({len(df)}개 종목)")

    if dry_run:
        print("[dry-run] API 호출 없이 파일 상태만 확인합니다.")
        for _, row in df.head(5).iterrows():
            code = str(row[code_col])
            name = row.get("stock_name", row.get("name", code))
            close = row.get("close", 0)
            print(f"  {code}  {name}  현재 close={close:,}")
        return {"success": True, "message": "dry-run 완료", "updated": 0, "total": len(df)}

    api = KISApiClient(CONFIG_PATH)
    updated = 0
    errors = 0
    for idx, row in df.iterrows():
        code = str(row[code_col])
        try:
            info = api.get_current_price(code)
            price = info.get("current_price", 0)
            if price and price > 0:
                df.at[idx, "close"] = price
                if "current_price" in df.columns:
                    df.at[idx, "current_price"] = price
                updated += 1
        except Exception as e:
            logger.warning("현재가 조회 실패 %s: %s", code, e)
            errors += 1
        time.sleep(0.05)  # API 과호출 방지

    # 백업 후 저장
    backup = path.with_suffix(".csv.bak")
    path.rename(backup)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  갱신 완료: {updated}개 성공, {errors}개 실패 → {path.name} 저장")
    print(f"  백업: {backup.name}")

    return {
        "success": True,
        "message": f"{updated}개 현재가 갱신 완료",
        "updated": updated,
        "errors": errors,
        "total": len(df),
        "file": str(path),
    }


def main():
    parser = argparse.ArgumentParser(description="AI 후보 리스트 현재가 갱신")
    parser.add_argument("--date", default=get_today_str(), help="날짜 (YYYYMMDD, 기본: 오늘)")
    parser.add_argument("--top", type=int, default=100, choices=[20, 50, 100], help="top N (기본: 100)")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 파일 상태만 확인")
    args = parser.parse_args()

    result = refresh_prices(date_str=args.date, top_n=args.top, dry_run=args.dry_run)
    print(f"\n결과: {result}")


if __name__ == "__main__":
    main()
