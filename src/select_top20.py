"""Top 20 종목 선정 모듈.

select_top_candidates.py를 호출하여 top20을 생성합니다.
기존 하위 호환성 유지.

실행:
    python src/select_top20.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from utils import get_today_str, load_config, setup_logger
from select_top_candidates import select_top_n

logger = setup_logger(__name__, "logs/select_top20.log")
cfg = load_config("config.yaml")


def main() -> None:
    today = get_today_str("%Y%m%d")
    predictions_dir = cfg["paths"]["predictions_dir"]
    predictions_path = os.path.join(predictions_dir, f"predictions_{today}.csv")

    logger.info("=== Top 20 선정 시작 ===")

    if not os.path.exists(predictions_path):
        logger.error(f"예측 파일 없음: {predictions_path}")
        logger.error("먼저 실행: python src/predict_candidates.py")
        sys.exit(1)

    top20 = select_top_n(predictions_path, 20, today, predictions_dir)

    if not top20.empty:
        logger.info(f"Top 20 선정 완료: {len(top20)}개")
        print(f"\nTop 20 후보 ({len(top20)}개):")
        code_col = "stock_code" if "stock_code" in top20.columns else "ticker"
        name_col = "stock_name" if "stock_name" in top20.columns else "name"
        show_cols = [c for c in [code_col, name_col, "close", "probability_2pct"] if c in top20.columns]
        print(top20[show_cols].head(20).to_string(index=False))

    logger.info("=== Top 20 선정 완료 ===")


if __name__ == "__main__":
    main()
