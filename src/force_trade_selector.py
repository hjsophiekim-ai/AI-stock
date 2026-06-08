"""거래 보장 후보 선정 모듈.

후보 종목이 0개가 되는 문제를 방지합니다.
필터를 단계적으로 완화하여 예산 범위 내에서 최소 1개 이상 거래 가능한 후보를 반환합니다.
hard exclusion(거래정지, 관리종목, 우선주 등)은 절대 완화하지 않습니다.

파일 로드 우선순위: top100 → top50 → top20 → predictions

사용법:
    python src/force_trade_selector.py
    python src/force_trade_selector.py --date 20260608
    python src/force_trade_selector.py --min-candidates 3
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from typing import List, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from utils import ensure_dir, get_today_str, is_etf_etn, is_preferred_stock, is_spac, load_config, setup_logger

logger = setup_logger(__name__, "logs/force_trade_selector.log")

RELAXATION_STEPS = [
    "normal_filters",
    "relax_trading_value",
    "relax_volatility",
    "score_only_with_hard_exclusions",
]


class ForceTradeSelectorError(RuntimeError):
    pass


class ForceTradeSelector:
    """단계적 필터 완화를 통한 거래 후보 선정 클래스."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self.risk = self.cfg.get("risk", {})
        self.ft = self.cfg.get("force_trade", {})
        self._hard = self.ft.get("hard_exclusions", {})
        self._predictions_dir = self.cfg.get("paths", {}).get("predictions_dir", "reports/predictions")
        self._min_price: int = int(self._hard.get("min_price", 1000))
        ensure_dir(self._predictions_dir)
        ensure_dir("reports")

    def select(
        self,
        date_str: Optional[str] = None,
        min_candidates: int = 1,
        max_candidates: int = 100,
    ) -> pd.DataFrame:
        """단계적 필터 완화를 통해 후보 종목을 선정합니다."""
        ds = date_str or get_today_str("%Y%m%d")
        df = self._load_predictions(ds)

        if df.empty:
            raise ForceTradeSelectorError(f"예측 파일 없음 또는 비어 있음 ({ds})")

        # Hard exclusion (어느 단계에서도 유지)
        df = self._apply_hard_exclusions(df)
        if df.empty:
            raise ForceTradeSelectorError("모든 종목이 hard exclusion에 해당합니다.")

        selected = pd.DataFrame()
        used_step = ""

        for step in RELAXATION_STEPS:
            candidates = self._apply_step(df, step)
            if len(candidates) >= min_candidates:
                selected = candidates
                used_step = step
                logger.info("선정 완료: step=%s candidates=%d", step, len(candidates))
                break
            logger.info("단계 %s: 후보 %d개 — 다음 단계 시도", step, len(candidates))

        if selected.empty:
            score_col = self._get_score_col(df)
            if score_col:
                selected = df.nlargest(min_candidates, score_col)
            else:
                selected = df.head(min_candidates)
            used_step = "emergency_top_n"
            logger.warning("모든 단계 완화 후에도 부족 — emergency_top_n 사용: %d개", len(selected))

        # max_candidates 제한
        if max_candidates > 0 and len(selected) > max_candidates:
            score_col = self._get_score_col(selected)
            if score_col:
                selected = selected.nlargest(max_candidates, score_col)
            else:
                selected = selected.head(max_candidates)

        selected = selected.copy()
        selected["selected_by_step"] = used_step
        selected["final_rank"] = range(1, len(selected) + 1)

        self._save_result(selected, ds)
        return selected

    def _load_predictions(self, date_str: str) -> pd.DataFrame:
        """top100 → top50 → top20 → predictions 순서로 파일 로드."""
        file_priority = [
            f"top100_{date_str}.csv",
            f"top50_{date_str}.csv",
            f"top20_{date_str}.csv",
            f"predictions_{date_str}.csv",
        ]

        for fname in file_priority:
            path = os.path.join(self._predictions_dir, fname)
            if os.path.exists(path):
                df = pd.read_csv(path)
                # stock_code 6자리 복원
                code_col = "stock_code" if "stock_code" in df.columns else "ticker"
                if code_col in df.columns:
                    df[code_col] = df[code_col].astype(str).str.zfill(6)
                logger.info("예측 파일 로드: %s (%d행)", path, len(df))
                return df

        # 파일 없으면 예측 스크립트 실행 시도
        logger.warning("예측 파일 없음. 예측 파이프라인 실행 시도...")
        self._run_prediction_scripts()

        for fname in file_priority:
            path = os.path.join(self._predictions_dir, fname)
            if os.path.exists(path):
                return pd.read_csv(path)

        return pd.DataFrame()

    def _run_prediction_scripts(self) -> None:
        scripts = [
            "src/predict_candidates.py",
            "src/select_top_candidates.py",
        ]
        for script in scripts:
            if not os.path.exists(script):
                continue
            try:
                result = subprocess.run(
                    [sys.executable, script],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    logger.warning("%s 실행 실패:\n%s", script, result.stderr[:300])
            except Exception as e:
                logger.warning("%s 실행 오류: %s", script, str(e))

    def _apply_hard_exclusions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Hard exclusion 필터 적용 — 절대 완화 불가."""
        mask = pd.Series([True] * len(df), index=df.index)

        # 거래정지
        if self._hard.get("exclude_halted_stock", True) and "is_halted" in df.columns:
            mask &= ~df["is_halted"].astype(bool)

        # 관리종목
        if self._hard.get("exclude_management_stock", True) and "is_management" in df.columns:
            mask &= ~df["is_management"].astype(bool)

        # 투자주의환기
        if self._hard.get("exclude_warning_stock", True) and "is_warning" in df.columns:
            mask &= ~df["is_warning"].astype(bool)

        # 우선주
        code_col = "stock_code" if "stock_code" in df.columns else "ticker"
        if self._hard.get("exclude_preferred_stock", True) and code_col in df.columns:
            mask &= ~df[code_col].astype(str).str.zfill(6).apply(is_preferred_stock)

        # 스팩
        name_col = "stock_name" if "stock_name" in df.columns else "name"
        if self._hard.get("exclude_spac", True) and name_col in df.columns:
            mask &= ~df[name_col].astype(str).apply(is_spac)

        # ETF/ETN
        if self._hard.get("exclude_etf_etn", True) and name_col in df.columns:
            mask &= ~df[name_col].astype(str).apply(is_etf_etn)

        # 최소 가격
        price_col = next((c for c in ("close", "current_price") if c in df.columns), None)
        if price_col:
            mask &= df[price_col] >= self._min_price

        excluded = (~mask).sum()
        if excluded > 0:
            logger.info("hard exclusion 제외: %d개", excluded)

        result = df[mask].copy()
        result["exclusion_reason"] = ""
        return result

    def _apply_step(self, df: pd.DataFrame, step: str) -> pd.DataFrame:
        if step == "normal_filters":
            return self._filter_normal(df)
        elif step == "relax_trading_value":
            return self._filter_relaxed_trading_value(df)
        elif step == "relax_volatility":
            return self._filter_relaxed_volatility(df)
        elif step == "score_only_with_hard_exclusions":
            return self._filter_score_only(df)
        return df

    def _filter_normal(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        min_tv = float(self.risk.get("min_daily_trading_value", 3_000_000_000))
        tv_col = next((c for c in ("trading_value",) if c in result.columns), None)
        if tv_col:
            result = result[result[tv_col] >= min_tv]
        return result

    def _filter_relaxed_trading_value(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        min_tv = float(self.risk.get("relaxed_min_daily_trading_value", 500_000_000))
        tv_col = next((c for c in ("trading_value",) if c in result.columns), None)
        if tv_col:
            result = result[result[tv_col] >= min_tv]
        return result

    def _filter_relaxed_volatility(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        min_tv = float(self.risk.get("relaxed_min_daily_trading_value", 500_000_000))
        tv_col = next((c for c in ("trading_value",) if c in result.columns), None)
        if tv_col:
            result = result[result[tv_col] >= min_tv]
        return result

    def _filter_score_only(self, df: pd.DataFrame) -> pd.DataFrame:
        score_col = self._get_score_col(df)
        top_n = self.ft.get("force_buy_top_n", 100)
        if score_col:
            return df.nlargest(top_n, score_col)
        return df

    def _get_score_col(self, df: pd.DataFrame) -> Optional[str]:
        for col in ("probability_2pct", "prediction_score", "proba_up", "probability"):
            if col in df.columns:
                return col
        return None

    def _save_result(self, df: pd.DataFrame, date_str: str) -> None:
        path = os.path.join("reports", f"force_trade_candidates_{date_str}.csv")
        code_col = "stock_code" if "stock_code" in df.columns else "ticker"
        name_col = "stock_name" if "stock_name" in df.columns else "name"
        price_col = next((c for c in ("close", "current_price") if c in df.columns), None)
        score_col = self._get_score_col(df)

        out_cols = [c for c in [
            code_col, name_col, price_col, score_col,
            "trading_value", "selected_by_step", "exclusion_reason", "final_rank",
        ] if c and c in df.columns]

        df[out_cols].to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("후보 저장: %s (%d개)", path, len(df))
        print(f"후보 저장: {path} ({len(df)}개 종목)")


def main() -> None:
    parser = argparse.ArgumentParser(description="거래 보장 후보 선정")
    parser.add_argument("--date", default=None, help="날짜 YYYYMMDD (기본값: 오늘)")
    parser.add_argument("--min-candidates", type=int, default=1, help="최소 후보 수 (기본값: 1)")
    parser.add_argument("--max-candidates", type=int, default=100, help="최대 후보 수 (기본값: 100)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    selector = ForceTradeSelector(args.config)
    try:
        result = selector.select(
            date_str=args.date,
            min_candidates=args.min_candidates,
            max_candidates=args.max_candidates,
        )
        print(f"\n선정 결과: {len(result)}개 종목")
        if not result.empty:
            code_col = "stock_code" if "stock_code" in result.columns else "ticker"
            name_col = "stock_name" if "stock_name" in result.columns else "name"
            cols = [c for c in [code_col, name_col, "close", "selected_by_step"] if c in result.columns]
            print(result[cols].to_string(index=False))
    except ForceTradeSelectorError as e:
        print(f"오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
