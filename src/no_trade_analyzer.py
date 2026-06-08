"""거래 0건 원인 분석 도구.

왜 당일 거래가 발생하지 않았는지 파일과 설정을 분석하여
사람이 이해할 수 있는 보고서를 생성합니다.

사용법:
    python src/no_trade_analyzer.py
    python src/no_trade_analyzer.py --date 20260608
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))

from utils import ensure_dir, get_today_str, is_etf_etn, is_preferred_stock, is_spac, load_config, setup_logger

logger = setup_logger(__name__, "logs/no_trade_analyzer.log")


class NoTradeAnalyzer:
    """거래 0건 원인 분석 클래스."""

    def __init__(self, config_path: str = "config.yaml") -> None:
        self.cfg = load_config(config_path)
        self.risk = self.cfg.get("risk", {})
        self.ft = self.cfg.get("force_trade", {})
        self._predictions_dir = self.cfg.get("paths", {}).get("predictions_dir", "reports/predictions")
        self._findings: List[Dict] = []
        ensure_dir("reports")

    def analyze(self, date_str: Optional[str] = None) -> str:
        """전체 분석 실행 후 보고서 경로 반환."""
        ds = date_str or get_today_str("%Y%m%d")
        self._findings.clear()

        print(f"\n[거래 0건 원인 분석] {ds}\n" + "=" * 60)

        self._check_config_mode()
        self._check_env_vars()
        self._check_emergency_stop()
        self._check_predictions_file(ds)
        self._check_force_trade_enabled()
        self._check_no_trade_reason_file(ds)
        self._check_order_log(ds)
        self._check_api_log()

        report_path = self._save_report(ds)
        return report_path

    # ------------------------------------------------------------------
    # 개별 분석 항목
    # ------------------------------------------------------------------

    def _check_config_mode(self) -> None:
        live_trade = self.cfg.get("live_trade", False)
        use_mock = self.cfg.get("kis", {}).get("use_mock", True)
        confirm = self.cfg.get("safety", {}).get("confirm_live_trade", False)
        ft_enabled = self.ft.get("enabled", False)

        if not live_trade:
            self._add("설정", "live_trade=false — 실제 주문이 비활성화되어 있습니다.", "설정 확인 필요")
        elif use_mock:
            self._add("설정", "MOCK 모드 — 모의투자 주문만 발생합니다.", "정상")
        elif not confirm:
            self._add("설정", "REAL 모드이지만 confirm_live_trade=false — 실전 주문 불가.", "설정 확인 필요")
        else:
            self._add("설정", "REAL 모드 활성화.", "정상")

        if not ft_enabled:
            self._add("force_trade", "force_trade.enabled=false — 거래 보장 모드 비활성화.", "설정 확인 필요")

    def _check_env_vars(self) -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        missing = []
        for key in ("KIS_APP_KEY", "KIS_APP_SECRET"):
            if not os.environ.get(key):
                missing.append(key)
        if missing:
            self._add("환경변수", f"누락: {', '.join(missing)}", "설정 필요")
        else:
            self._add("환경변수", "API 키 환경변수 확인됨.", "정상")

    def _check_emergency_stop(self) -> None:
        if os.path.exists("data/EMERGENCY_STOP"):
            self._add("긴급중단", "data/EMERGENCY_STOP 파일이 존재합니다 — 모든 신규 주문이 차단됩니다.", "조치 필요")
        else:
            self._add("긴급중단", "긴급 중단 비활성화.", "정상")

    def _check_predictions_file(self, date_str: str) -> None:
        top20 = os.path.join(self._predictions_dir, f"top20_{date_str}.csv")
        pred = os.path.join(self._predictions_dir, f"predictions_{date_str}.csv")

        for path in (top20, pred):
            if os.path.exists(path):
                try:
                    import pandas as pd
                    df = pd.read_csv(path)
                    n = len(df)
                    self._add("예측파일", f"{os.path.basename(path)}: {n}개 종목", "정상")
                    self._analyze_filter_stats(df, date_str)
                except Exception as e:
                    self._add("예측파일", f"{os.path.basename(path)}: 읽기 오류 — {e}", "오류")
                return

        self._add("예측파일", f"예측 파일 없음 ({date_str}) — predict_candidates.py와 select_top20.py를 먼저 실행하세요.", "조치 필요")

    def _analyze_filter_stats(self, df, date_str: str) -> None:
        """필터별 탈락 종목 수 계산."""
        import pandas as pd

        total = len(df)
        stats = {}

        # 거래대금 필터
        min_tv = float(self.risk.get("min_daily_trading_value", 3_000_000_000))
        if "trading_value" in df.columns:
            tv_fail = int((df["trading_value"] < min_tv).sum())
            stats["거래대금 미달"] = tv_fail

        # 가격 필터
        min_price = int(self.risk.get("min_price", 1000))
        price_col = next((c for c in ("current_price", "close") if c in df.columns), None)
        if price_col:
            price_fail = int((df[price_col] < min_price).sum())
            stats[f"가격{min_price}원 미달"] = price_fail

        # 위험종목 필터
        danger_count = 0
        if "is_halted" in df.columns:
            danger_count += int(df["is_halted"].astype(bool).sum())
        if "is_management" in df.columns:
            danger_count += int(df["is_management"].astype(bool).sum())
        if "is_warning" in df.columns:
            danger_count += int(df["is_warning"].astype(bool).sum())
        if danger_count > 0:
            stats["위험종목(정지/관리/경고)"] = danger_count

        # 우선주/스팩/ETF
        ticker_col = "ticker" if "ticker" in df.columns else None
        name_col = "name" if "name" in df.columns else None
        excluded_special = 0
        if ticker_col:
            excluded_special += int(df[ticker_col].astype(str).apply(is_preferred_stock).sum())
        if name_col:
            excluded_special += int(df[name_col].astype(str).apply(is_spac).sum())
            excluded_special += int(df[name_col].astype(str).apply(is_etf_etn).sum())
        if excluded_special > 0:
            stats["우선주/스팩/ETF"] = excluded_special

        for category, count in stats.items():
            status = "필터 탈락 있음" if count > 0 else "정상"
            self._add(f"필터[{category}]", f"전체 {total}개 중 {count}개 탈락", status)

    def _check_force_trade_enabled(self) -> None:
        if not self.ft.get("enabled", False):
            self._add(
                "거래보장모드",
                "force_trade.enabled=false. config.yaml에서 true로 변경 후 재실행하세요.",
                "비활성화"
            )

    def _check_no_trade_reason_file(self, date_str: str) -> None:
        path = os.path.join("reports", f"no_trade_reason_{date_str}.txt")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                self._add("거래없음보고서", f"{path} 내용:\n{content[:800]}", "참고")
            except Exception:
                pass

    def _check_order_log(self, date_str: str) -> None:
        path = os.path.join("reports", "orders", f"orders_{date_str}.csv")
        if os.path.exists(path):
            try:
                import pandas as pd
                df = pd.read_csv(path)
                success = int(df["success"].astype(bool).sum()) if "success" in df.columns else "?"
                self._add("주문로그", f"당일 주문 기록: {len(df)}건 (성공: {success}건)", "참고")
            except Exception as e:
                self._add("주문로그", f"주문 로그 읽기 오류: {e}", "오류")
        else:
            self._add("주문로그", "당일 주문 기록 없음.", "정상(주문없음)")

    def _check_api_log(self) -> None:
        log_path = self.cfg.get("logging", {}).get("api_log_file", "logs/api.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                error_lines = [l.strip() for l in lines[-50:] if "ERROR" in l or "오류" in l]
                if error_lines:
                    self._add("API로그", f"최근 오류 {len(error_lines)}건:\n  " + "\n  ".join(error_lines[:5]), "오류 있음")
                else:
                    self._add("API로그", "최근 50줄 내 오류 없음.", "정상")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _add(self, category: str, message: str, status: str) -> None:
        self._findings.append({"category": category, "message": message, "status": status})
        icon = "✅" if status in ("정상",) else ("⚠️" if "확인" in status or "참고" in status else "❌")
        print(f"  {icon} [{category}] {message}")

    def _save_report(self, date_str: str) -> str:
        path = os.path.join("reports", f"no_trade_analysis_{date_str}.txt")
        lines = [
            f"거래 0건 원인 분석 보고서",
            f"생성시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"분석 날짜: {date_str}",
            "=" * 60,
        ]
        for f in self._findings:
            lines.append(f"[{f['category']}] ({f['status']}) {f['message']}")
        lines.append("=" * 60)
        lines.append("\n조치 권장사항:")
        for f in self._findings:
            if f["status"] in ("설정 확인 필요", "조치 필요", "설정 필요", "비활성화"):
                lines.append(f"  → {f['category']}: {f['message'][:100]}")

        with open(path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines))
        print(f"\n보고서 저장: {path}")
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description="거래 0건 원인 분석")
    parser.add_argument("--date", default=None, help="날짜 YYYYMMDD (기본값: 오늘)")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    analyzer = NoTradeAnalyzer(args.config)
    analyzer.analyze(args.date)


if __name__ == "__main__":
    main()
