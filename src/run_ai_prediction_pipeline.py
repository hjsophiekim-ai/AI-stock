"""AI 예측 파이프라인 통합 실행 스크립트.

의존성 확인 → 데이터 수집 → 피처 생성 → 라벨 생성 → 모델 학습 → 예측 → top100 생성
각 단계 실패 시 원인을 표시하고, 성공하면 최종 파일 목록을 출력합니다.

실행:
    python src/run_ai_prediction_pipeline.py --years 3 --limit 100
    python src/run_ai_prediction_pipeline.py --years 3 --all
    python src/run_ai_prediction_pipeline.py --years 3 --all --budget 300000
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from utils import get_today_str, load_config, setup_logger

logger = setup_logger(__name__, "logs/run_pipeline.log")
cfg = load_config("config.yaml")

GREEN = ""
RED = ""
RESET = ""


def run_step(label: str, cmd: list, timeout: int = 600) -> bool:
    """단계 실행 및 결과 반환."""
    print(f"\n{'='*60}")
    print(f"  [{label}] 실행 중...")
    print(f"  명령: {' '.join(cmd)}")
    print("=" * 60)

    try:
        result = subprocess.run(
            cmd,
            capture_output=False,  # 실시간 출력
            timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        if result.returncode == 0:
            print(f"  [{label}] 완료")
            return True
        else:
            print(f"  [{label}] 실패 (종료코드: {result.returncode})")
            return False
    except subprocess.TimeoutExpired:
        print(f"  [{label}] 타임아웃 ({timeout}초 초과)")
        return False
    except Exception as e:
        print(f"  [{label}] 오류: {e}")
        return False


def check_file(path: str, label: str) -> bool:
    """파일 존재 확인."""
    exists = os.path.exists(path)
    status = "OK" if exists else "없음"
    print(f"  [{status}] {label}: {path}")
    return exists


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 예측 파이프라인 통합 실행")
    parser.add_argument("--years", type=int, default=3, help="수집 기간(년, 기본값: 3)")
    parser.add_argument("--limit", type=int, default=None,
                        help="종목 수 제한 (테스트용, 예: --limit 100)")
    parser.add_argument("--all", action="store_true", help="전체 종목 수집")
    parser.add_argument("--skip-collect", action="store_true",
                        help="데이터 수집 건너뜀 (기존 데이터 사용)")
    parser.add_argument("--skip-train", action="store_true",
                        help="모델 학습 건너뜀 (기존 모델 사용)")
    parser.add_argument("--budget", type=int, default=None,
                        help="예산 배분도 실행 (원)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="기존 데이터 파일 무시하고 재수집")
    args = parser.parse_args()

    today = get_today_str("%Y%m%d")
    python = sys.executable
    results = {}

    print("\n" + "=" * 60)
    print("  AI 예측 파이프라인 시작")
    print(f"  날짜: {today} | 수집 기간: {args.years}년")
    if args.limit and not args.all:
        print(f"  종목 제한: {args.limit}개 (테스트 모드)")
    print("=" * 60)

    # 0. 의존성 확인
    print("\n[0/7] 의존성 확인")
    dep_ok = run_step("의존성확인", [python, "src/check_dependencies.py"], timeout=60)
    if not dep_ok:
        print("  주의: 일부 패키지 누락. 계속 시도합니다.")
        print("  권장: pip install pykrx FinanceDataReader lightgbm")

    # 1. 데이터 수집
    if not args.skip_collect:
        print("\n[1/7] 일봉 데이터 수집")
        collect_cmd = [python, "src/collect_daily_data.py", "--years", str(args.years)]
        if args.force_refresh:
            collect_cmd.append("--force-refresh")
        if args.all:
            collect_cmd.append("--all")
        elif args.limit:
            collect_cmd += ["--limit", str(args.limit)]
        results["collect"] = run_step("데이터수집", collect_cmd, timeout=3600)
    else:
        print("\n[1/7] 데이터 수집 건너뜀")
        results["collect"] = os.path.exists(cfg["data"]["raw_daily_path"])

    if not results["collect"]:
        daily_path = cfg["data"]["raw_daily_path"]
        if not os.path.exists(daily_path):
            print(f"\n오류: 데이터 파일이 없습니다: {daily_path}")
            print("  pip install pykrx 후 다시 실행하거나 --limit 100으로 시도하세요")
            sys.exit(1)
        print("  (기존 데이터 파일로 계속 진행)")

    # 2. 피처 생성
    print("\n[2/7] 피처 생성")
    results["features"] = run_step("피처생성", [python, "src/make_features.py"], timeout=600)
    if not results["features"] and not os.path.exists(cfg["data"]["processed_features_path"]):
        print("\n오류: 피처 생성 실패. 파이프라인을 중단합니다.")
        sys.exit(1)

    # 3. 라벨 생성
    print("\n[3/7] 라벨 생성")
    results["labels"] = run_step("라벨생성", [python, "src/make_labels.py"], timeout=300)
    if not results["labels"] and not os.path.exists(cfg["data"]["processed_labels_path"]):
        print("\n오류: 라벨 생성 실패.")
        sys.exit(1)

    # 4. 모델 학습
    if not args.skip_train:
        print("\n[4/7] 모델 학습")
        results["train"] = run_step("모델학습", [python, "src/train_model.py"], timeout=1800)
    else:
        print("\n[4/7] 모델 학습 건너뜀")
        results["train"] = os.path.exists(cfg["paths"]["model_path"])

    if not results["train"] and not os.path.exists(cfg["paths"]["model_path"]):
        print("\n오류: 모델 파일이 없습니다. train_model.py를 실행하세요.")
        sys.exit(1)

    # 5. 예측 생성
    print("\n[5/7] 예측 생성")
    results["predict"] = run_step("예측생성", [python, "src/predict_candidates.py"], timeout=300)

    # 6. Top100 후보 생성
    print("\n[6/7] Top100 후보 생성")
    results["top100"] = run_step(
        "Top100생성",
        [python, "src/select_top_candidates.py", "--top-n", "100", "--all"],
        timeout=120,
    )

    # 7. (선택) 예산 배분
    if args.budget:
        print(f"\n[7/7] 예산 배분 ({args.budget:,}원)")
        results["budget"] = run_step(
            "예산배분",
            [python, "src/budget_allocator.py", "--budget", str(args.budget),
             "--max-orders", "100"],
            timeout=120,
        )
    else:
        print("\n[7/7] 예산 배분 건너뜀 (--budget 지정 시 실행)")
        results["budget"] = None

    # 결과 요약
    print("\n" + "=" * 60)
    print("  파이프라인 완료 — 결과 파일 확인")
    print("=" * 60)

    predictions_dir = cfg["paths"]["predictions_dir"]
    files_to_check = [
        (cfg["data"]["raw_daily_path"], "일봉 데이터"),
        (cfg["data"]["processed_features_path"], "피처 데이터"),
        (cfg["data"]["processed_labels_path"], "라벨 데이터"),
        ("data/processed/latest_features.csv", "최신 피처 (예측용)"),
        (cfg["paths"]["model_path"], "학습 모델"),
        ("models/feature_columns.json", "피처 컬럼 목록"),
        (os.path.join(predictions_dir, f"predictions_{today}.csv"), "전체 예측"),
        (os.path.join(predictions_dir, f"top20_{today}.csv"), "Top20 후보"),
        (os.path.join(predictions_dir, f"top50_{today}.csv"), "Top50 후보"),
        (os.path.join(predictions_dir, f"top100_{today}.csv"), "Top100 후보"),
    ]

    if args.budget:
        files_to_check.append(
            (os.path.join("reports", f"budget_allocation_{today}.csv"), "예산 배분")
        )

    all_ok = True
    for path, label in files_to_check:
        ok = check_file(path, label)
        if not ok:
            all_ok = False

    top100_path = os.path.join(predictions_dir, f"top100_{today}.csv")
    if os.path.exists(top100_path):
        import pandas as pd
        df = pd.read_csv(top100_path)
        print(f"\nTop100 후보 파일: {top100_path} ({len(df)}개 종목)")
        code_col = "stock_code" if "stock_code" in df.columns else "ticker"
        name_col = "stock_name" if "stock_name" in df.columns else "name"
        prob_col = next((c for c in ("probability_2pct", "proba_up") if c in df.columns), None)
        show_cols = [c for c in [code_col, name_col, "close", prob_col] if c]
        print(df[show_cols].head(10).to_string(index=False))

    print("\n" + "=" * 60)
    print("  다음 명령어:")
    print(f"  MOCK 자동매매:")
    print(f"    python src/force_auto_trade.py --budget {args.budget or 300000} --mode mock --max-orders 100")
    print("  시스템 검증:")
    print("    python src/full_system_verification.py")
    print("=" * 60)


def run_pipeline(
    years: int = 3,
    limit=None,
    all_stocks: bool = False,
    budget=None,
    top_n: int = 100,
    refresh_prices: bool = False,
    skip_collect: bool = False,
    skip_train: bool = False,
) -> dict:
    """파이프라인을 함수로 실행. 항상 dict를 반환하며 절대 None을 반환하지 않는다."""
    today = ""
    created_files = []
    errors = []
    try:
        today = get_today_str("%Y%m%d")
    except Exception:
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y%m%d")

    python = sys.executable
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _run(label, cmd, timeout=600):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=project_root,
            )
            return r.returncode == 0, (r.stderr or "")[-500:]
        except subprocess.TimeoutExpired:
            return False, f"{label} 타임아웃 ({timeout}초)"
        except Exception as ex:
            return False, str(ex)

    safe_cfg = {}
    try:
        safe_cfg = load_config("config.yaml") or {}
    except Exception as ex:
        errors.append(f"config 로드 실패: {ex}")

    data_cfg = safe_cfg.get("data") or {}
    paths_cfg = safe_cfg.get("paths") or {}
    predictions_dir = paths_cfg.get("predictions_dir", "reports/predictions")
    top100_file = os.path.join(project_root, predictions_dir, f"top100_{today}.csv")
    force_file = os.path.join(project_root, "reports", f"force_trade_candidates_{today}.csv")

    try:
        # Step 1: 데이터 수집
        if not skip_collect:
            collect_cmd = [python, "src/collect_daily_data.py", "--years", str(years)]
            if all_stocks:
                collect_cmd.append("--all")
            elif limit:
                collect_cmd += ["--limit", str(limit)]
            ok, err = _run("데이터수집", collect_cmd, timeout=3600)
            if not ok:
                daily_path = os.path.join(project_root, data_cfg.get("raw_daily_path", "data/raw/daily_prices.csv"))
                if not os.path.exists(daily_path):
                    errors.append(f"데이터 수집 실패: {err}")
                    return {
                        "success": False, "stage": "collect_data",
                        "message": f"일봉 데이터 수집 실패: {err}",
                        "created_files": [], "errors": errors,
                        "predictions_file": None, "top100_file": None, "force_trade_file": None,
                    }

        # Step 2: 피처 생성
        ok, err = _run("피처생성", [python, "src/make_features.py"], timeout=600)
        if not ok:
            feat_path = os.path.join(project_root, data_cfg.get("processed_features_path", "data/processed/features.csv"))
            if not os.path.exists(feat_path):
                errors.append(f"피처 생성 실패: {err}")
                return {
                    "success": False, "stage": "make_features",
                    "message": f"피처 생성 실패: {err}",
                    "created_files": created_files, "errors": errors,
                    "predictions_file": None, "top100_file": None, "force_trade_file": None,
                }

        # Step 3: 라벨 생성
        ok, err = _run("라벨생성", [python, "src/make_labels.py"], timeout=300)
        if not ok:
            lbl_path = os.path.join(project_root, data_cfg.get("processed_labels_path", "data/processed/labeled_dataset.csv"))
            if not os.path.exists(lbl_path):
                errors.append(f"라벨 생성 실패: {err}")

        # Step 4: 모델 학습
        if not skip_train:
            ok, err = _run("모델학습", [python, "src/train_model.py"], timeout=1800)
            if not ok:
                model_path = os.path.join(project_root, paths_cfg.get("model_path", "models/model.joblib"))
                if not os.path.exists(model_path):
                    errors.append(f"모델 학습 실패: {err}")
                    return {
                        "success": False, "stage": "train_model",
                        "message": f"모델 학습 실패 (모델 파일 없음): {err}",
                        "created_files": created_files, "errors": errors,
                        "predictions_file": None, "top100_file": None, "force_trade_file": None,
                    }

        # Step 5: 예측 생성
        ok, err = _run("예측생성", [python, "src/predict_candidates.py"], timeout=300)
        pred_file = os.path.join(project_root, predictions_dir, f"predictions_{today}.csv")
        if os.path.exists(pred_file):
            created_files.append(pred_file)

        # Step 6: Top100 생성
        ok, err = _run("Top100생성", [python, "src/select_top_candidates.py", "--top-n", "100", "--all"], timeout=120)
        if os.path.exists(top100_file):
            created_files.append(top100_file)

        # (선택) 예산 배분
        if budget:
            _run("예산배분", [python, "src/budget_allocator.py", "--budget", str(budget), "--max-orders", "100"], timeout=120)

        # force_trade 후보
        _run("force_trade후보", [python, "src/force_trade_selector.py"], timeout=60)
        if os.path.exists(force_file):
            created_files.append(force_file)

        success = os.path.exists(top100_file)
        return {
            "success": success,
            "stage": "completed" if success else "top100_missing",
            "message": "전체 파이프라인 완료" if success else f"top100 파일 미생성: {top100_file}",
            "created_files": created_files,
            "errors": errors,
            "predictions_file": pred_file if os.path.exists(pred_file) else None,
            "top100_file": top100_file if os.path.exists(top100_file) else None,
            "force_trade_file": force_file if os.path.exists(force_file) else None,
            "today": today,
        }

    except Exception as ex:
        import traceback
        errors.append(traceback.format_exc())
        return {
            "success": False, "stage": "exception",
            "message": f"파이프라인 예외 발생: {ex}",
            "created_files": created_files, "errors": errors,
            "predictions_file": None, "top100_file": None, "force_trade_file": None,
        }


if __name__ == "__main__":
    main()
