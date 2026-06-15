"""통합 매매 파이프라인 진단 스크립트.

이 스크립트 하나로 현재가, 계좌, 매수가능금액, 후보파일, 주문 가능 여부를
한 번에 확인합니다.

사용법:
    python src/diagnose_all_trading_pipeline.py --mode mock --budget 10000000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from utils import setup_logger

logger = setup_logger(__name__, "logs/diagnose_all_trading_pipeline.log")

PREDICTIONS_DIR = PROJECT_ROOT / "reports" / "predictions"


def _find_active_buy_candidate_file(date_str: Optional[str] = None) -> Optional[Path]:
    """buy_top20 파일만 탐색 (단일 진실 공급원)."""
    if not PREDICTIONS_DIR.exists():
        return None
    today = date_str or datetime.now().strftime("%Y%m%d")
    today_file = PREDICTIONS_DIR / f"buy_top20_{today}.csv"
    if today_file.exists():
        return today_file
    found = sorted(PREDICTIONS_DIR.glob("buy_top20_????????.csv"), reverse=True)
    if found:
        return found[0]
    return None


def _find_latest_orders_file(subdir: str, pattern: str) -> str:
    orders_dir = PROJECT_ROOT / "reports" / "orders" / subdir
    if not orders_dir.exists():
        orders_dir = PROJECT_ROOT / "reports" / "orders"
    if orders_dir.exists():
        files = sorted(orders_dir.glob(pattern), reverse=True)
        if files:
            return str(files[0])
    return ""


def run_diagnosis(mode: str = "mock", budget: int = 10_000_000) -> Dict:
    """매매 파이프라인 전체 진단.

    Returns:
        {
            "success": bool,
            "candidate_file": str,
            "candidate_count": int,
            "current_price_updated": bool,
            "broker_position_count": int,
            "local_open_position_count": int,
            "orderable_cash_success": bool,
            "orderable_cash": int or None,
            "buy_preflight_ok": bool,
            "buy_block_reason": str,
            "latest_buy_orders_file": str,
            "latest_sell_orders_file": str,
            "render_pipeline_ready": bool,
            "failed_checks": list,
        }
    """
    mode_lower = (mode or "mock").lower()
    failed_checks: List[str] = []
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result: Dict = {
        "success": False,
        "mode": mode_lower.upper(),
        "candidate_file": "",
        "candidate_count": 0,
        "current_price_updated": False,
        "price_updated_age_min": None,
        "broker_position_count": -1,
        "broker_api_success": False,
        "local_open_position_count": -1,
        "orderable_cash_success": False,
        "orderable_cash": None,
        "buy_preflight_ok": False,
        "buy_block_reason": "",
        "latest_buy_orders_file": "",
        "latest_sell_orders_file": "",
        "render_pipeline_ready": False,
        "failed_checks": [],
        "checked_at": checked_at,
    }

    print(f"[DIAG] 진단 시작: mode={mode_lower.upper()}, budget={budget:,}원", flush=True)

    # ── 1. buy_top20 후보 파일 ────────────────────────────────────────────────
    active_file = _find_active_buy_candidate_file()
    if active_file:
        result["candidate_file"] = str(active_file)
        try:
            import pandas as pd
            df = pd.read_csv(active_file)
            result["candidate_count"] = len(df)
            # 현재가 갱신 여부 (30분 이내)
            if "price_updated_at" in df.columns:
                try:
                    latest_ts = pd.to_datetime(df["price_updated_at"].dropna()).max()
                    age_min = (datetime.now() - latest_ts.to_pydatetime().replace(tzinfo=None)).total_seconds() / 60
                    result["price_updated_age_min"] = round(age_min, 1)
                    result["current_price_updated"] = age_min < 30
                except Exception:
                    result["current_price_updated"] = False
            print(f"[DIAG] 후보 파일: {active_file.name} ({result['candidate_count']}개)", flush=True)
        except Exception as e:
            failed_checks.append(f"후보파일 읽기 실패: {e}")
    else:
        failed_checks.append("buy_top20 파일 없음 — 장중 Top20 필터를 먼저 실행하세요")
        print("[DIAG] buy_top20 파일 없음", flush=True)

    # ── 2. KIS 계좌조회 ───────────────────────────────────────────────────────
    if mode_lower != "paper":
        try:
            from kis_api import KISApiClient
            from safety_gate import SafetyGate
            gate = SafetyGate(str(PROJECT_ROOT / "config.yaml"), runtime_mode=mode_lower)
            api = KISApiClient(str(PROJECT_ROOT / "config.yaml"), gate=gate)
            broker_df = api.get_positions()
            if broker_df is not None:
                result["broker_position_count"] = len(broker_df)
                result["broker_api_success"] = True
                print(f"[DIAG] KIS 계좌조회 성공: {result['broker_position_count']}개 보유", flush=True)
            else:
                result["broker_position_count"] = 0
                result["broker_api_success"] = True
                print("[DIAG] KIS 계좌조회 성공: 보유 없음", flush=True)
        except Exception as e:
            result["broker_position_count"] = -1
            result["broker_api_success"] = False
            failed_checks.append(f"KIS 계좌조회 실패: {e}")
            print(f"[DIAG] KIS 계좌조회 실패: {e}", flush=True)
    else:
        result["broker_api_success"] = True
        result["broker_position_count"] = 0

    # ── 3. 로컬 OPEN 포지션 ───────────────────────────────────────────────────
    try:
        from position_manager import PositionManager
        pm = PositionManager(str(PROJECT_ROOT / "config.yaml"), mode=mode_lower)
        open_pos = pm.get_open_positions()
        result["local_open_position_count"] = sum(
            1 for pos in open_pos.values()
            if getattr(pos, "status", "OPEN") == "OPEN"
            and int(getattr(pos, "quantity", 0)) > 0
        )
        print(f"[DIAG] 로컬 OPEN 포지션: {result['local_open_position_count']}개", flush=True)
    except Exception as e:
        result["local_open_position_count"] = -1
        failed_checks.append(f"로컬 포지션 읽기 실패: {e}")
        print(f"[DIAG] 로컬 포지션 읽기 실패: {e}", flush=True)

    # ── 4. 주문가능금액 ───────────────────────────────────────────────────────
    if mode_lower != "paper":
        try:
            from kis_api import KISApiClient
            from safety_gate import SafetyGate
            gate = SafetyGate(str(PROJECT_ROOT / "config.yaml"), runtime_mode=mode_lower)
            api = KISApiClient(str(PROJECT_ROOT / "config.yaml"), gate=gate)
            cash_result = api.get_orderable_cash_result()
            result["orderable_cash_success"] = cash_result.get("success", False)
            result["orderable_cash"] = (
                int(cash_result["orderable_cash"])
                if cash_result.get("success") and cash_result.get("orderable_cash") is not None
                else None
            )
            if result["orderable_cash_success"]:
                print(f"[DIAG] 주문가능금액: {result['orderable_cash']:,}원", flush=True)
            else:
                failed_checks.append(f"주문가능금액 조회 실패: {cash_result.get('error_message', '')}")
                print(f"[DIAG] 주문가능금액 조회 실패: {cash_result.get('error_message', '')}", flush=True)
        except Exception as e:
            result["orderable_cash_success"] = False
            result["orderable_cash"] = None
            failed_checks.append(f"주문가능금액 조회 예외: {e}")
            print(f"[DIAG] 주문가능금액 조회 예외: {e}", flush=True)
    else:
        result["orderable_cash_success"] = True
        result["orderable_cash"] = budget
        print(f"[DIAG] PAPER 모드: budget={budget:,}원 사용", flush=True)

    # ── 5. 매수 preflight ─────────────────────────────────────────────────────
    open_cnt = (
        result["broker_position_count"]
        if result["broker_api_success"] and result["broker_position_count"] >= 0
        else result["local_open_position_count"]
    )
    try:
        from utils import load_config
        cfg = load_config(str(PROJECT_ROOT / "config.yaml"))
        max_pos = cfg.get("risk", {}).get("max_positions", 20)
    except Exception:
        max_pos = 20

    if not result["candidate_file"]:
        result["buy_block_reason"] = "buy_top20 파일 없음"
    elif open_cnt >= max_pos:
        result["buy_block_reason"] = f"최대 보유 종목 수 초과: {open_cnt}/{max_pos}"
    elif not result["orderable_cash_success"]:
        result["buy_block_reason"] = "주문가능금액 조회 실패"
    elif result["orderable_cash"] is not None and result["orderable_cash"] < 10000:
        result["buy_block_reason"] = f"주문가능금액 부족: {result['orderable_cash']:,}원"
    else:
        result["buy_preflight_ok"] = True

    # ── 6. 최신 주문 파일 ─────────────────────────────────────────────────────
    result["latest_buy_orders_file"] = _find_latest_orders_file(mode_lower, "buy_orders_*.csv")
    result["latest_sell_orders_file"] = _find_latest_orders_file(mode_lower, "sell_orders_*.csv")

    # ── 7. Render 파이프라인 준비 ─────────────────────────────────────────────
    models_dir = PROJECT_ROOT / "models"
    data_models_dir = PROJECT_ROOT / "data" / "models"
    features_file = PROJECT_ROOT / "data" / "processed" / "features.csv"
    has_model = (
        any(models_dir.glob("*.joblib")) if models_dir.exists() else False
    ) or (
        any(data_models_dir.glob("*.joblib")) if data_models_dir.exists() else False
    )
    result["render_pipeline_ready"] = has_model and features_file.exists()
    if not has_model:
        failed_checks.append("모델 파일 없음 (models/*.joblib)")
    if not features_file.exists():
        failed_checks.append("features.csv 없음 (data/processed/features.csv)")

    # ── 최종 ─────────────────────────────────────────────────────────────────
    result["failed_checks"] = failed_checks
    result["success"] = (
        len(failed_checks) == 0
        and result["buy_preflight_ok"]
        and result["broker_api_success"]
        and result["orderable_cash_success"]
    )

    print(f"\n[DIAG] === 진단 결과 ===", flush=True)
    print(f"  후보 파일: {result['candidate_file'] or '없음'}", flush=True)
    print(f"  현재가 갱신: {'✅' if result['current_price_updated'] else '❌'}", flush=True)
    print(f"  KIS 보유 종목: {result['broker_position_count']}개", flush=True)
    print(f"  로컬 OPEN: {result['local_open_position_count']}개", flush=True)
    print(f"  주문가능금액: {result['orderable_cash']}원 (성공={result['orderable_cash_success']})", flush=True)
    _preflight_str = "✅" if result["buy_preflight_ok"] else f"❌ {result['buy_block_reason']}"
    print(f"  전부 매수 가능: {_preflight_str}", flush=True)
    if failed_checks:
        print(f"  실패 항목:", flush=True)
        for fc in failed_checks:
            print(f"    - {fc}", flush=True)
    print(f"  종합: {'✅ 정상' if result['success'] else '❌ 문제 있음'}", flush=True)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="통합 매매 파이프라인 진단")
    parser.add_argument("--mode", default="mock", choices=["paper", "mock", "real"],
                        help="거래 모드 (기본: mock)")
    parser.add_argument("--budget", type=int, default=10_000_000,
                        help="진단용 예산 (기본: 1000만원)")
    parser.add_argument("--json-only", action="store_true",
                        help="JSON만 출력 (stdout)")
    args = parser.parse_args()

    result = run_diagnosis(mode=args.mode, budget=args.budget)

    print("\n[DIAG] 최종 JSON 결과:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
