"""AI 후보 리스트 현재가 갱신 스크립트.

mode 처리:
  paper : KIS API 호출 금지. close → current_price 복사 (price_source=PAPER_CLOSE)
  mock  : KIS 모의투자 서버 (openapivts), KIS_MOCK_APP_KEY 전용
  real  : KIS 실전 서버 (openapi), KIS_REAL_APP_KEY 전용. 주문 API 호출 금지.

None 처리:
  get_current_price(code) 가 None 을 반환해도 죽지 않는다.
  None 이면 price_error="QUOTE_NONE", current_price 는 기존 값 또는 close 유지.
  전체 CSV 저장은 계속 진행한다.

백업:
  shutil.copy2 로 timestamp backup. rename 방식 금지 (FileExistsError 방지).

사용법:
    python refresh_candidate_prices.py --mode mock
    python refresh_candidate_prices.py --mode mock --date 20260610 --top 100
    python refresh_candidate_prices.py --mode paper
    python refresh_candidate_prices.py --mode mock --input reports/predictions/top100_20260610.csv --limit 30
    python refresh_candidate_prices.py --mode mock --top 3   # 빠른 테스트
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from shutil import copy2
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# dotenv 로드 (src 모듈 import 전)
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

import pandas as pd
from utils import ensure_dir, setup_logger

logger = setup_logger(__name__, "logs/refresh_candidate_prices.log")

CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")
PREDICTIONS_DIR = PROJECT_ROOT / "reports" / "predictions"
REPORTS_DIR = PROJECT_ROOT / "reports"


# ── 컬럼명 자동 인식 ────────────────────────────────────────────────────────

_CODE_COLS = ["stock_code", "종목코드", "code", "ticker"]
_NAME_COLS = ["stock_name", "종목명", "name"]
_PRICE_COLS = ["current_price", "close", "현재가"]


def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _norm_code(val) -> str:
    s = str(val).replace(".0", "").strip()
    try:
        return str(int(s)).zfill(6)
    except Exception:
        return s.zfill(6)


# ── 백업 경로 ────────────────────────────────────────────────────────────────

def _make_backup(path: Path) -> Optional[Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.bak_{ts}{path.suffix}")
    try:
        copy2(path, backup)
        return backup
    except Exception as exc:
        logger.warning("백업 실패 (무시하고 계속): %s → %s: %s", path.name, backup.name, exc)
        return None


# ── 파일 탐색 ─────────────────────────────────────────────────────────────────

def find_candidate_file(date_str: str, top_n: int) -> Optional[Path]:
    """후보 파일 탐색.

    탐색 순서:
      1. buy_top20_{date_str}.csv (단일 진실 공급원)
      2. top{top_n}_{date_str}.csv
      3. 더 큰 top-N fallback (--limit으로 행 수 제한)
    """
    # 1순위: buy_top20 (공식 매수 후보)
    buy20 = PREDICTIONS_DIR / f"buy_top20_{date_str}.csv"
    if buy20.exists():
        logger.info("buy_top20_%s.csv 발견 → 현재가 갱신 대상으로 사용", date_str)
        return buy20

    # 2순위: top{top_n}
    path = PREDICTIONS_DIR / f"top{top_n}_{date_str}.csv"
    if path.exists():
        return path

    # 3순위: top-N fallback
    for n in (100, 50, 20, 10):
        if n != top_n:
            fb = PREDICTIONS_DIR / f"top{n}_{date_str}.csv"
            if fb.exists():
                logger.info(
                    "top%d_%s.csv 없음 → %s fallback (--limit %d 적용됨)",
                    top_n, date_str, fb.name, top_n,
                )
                return fb
    return None


def find_active_buy_candidate_file(date_str: Optional[str] = None) -> Optional[Path]:
    """buy_top20 파일만 탐색 (단일 진실 공급원). top100 fallback 금지."""
    today = date_str or datetime.now().strftime("%Y%m%d")
    today_file = PREDICTIONS_DIR / f"buy_top20_{today}.csv"
    if today_file.exists():
        return today_file
    found = sorted(PREDICTIONS_DIR.glob("buy_top20_????????.csv"), reverse=True)
    if found:
        return found[0]
    return None


# ── 리포트 저장 ───────────────────────────────────────────────────────────────

def _save_report(result: Dict[str, Any]) -> Dict[str, str]:
    ensure_dir(str(REPORTS_DIR))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"refresh_candidate_prices_{ts}.json"
    txt_path = REPORTS_DIR / f"refresh_candidate_prices_{ts}.txt"
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        lines = [
            "refresh_candidate_prices 결과 리포트",
            f"run_at: {result.get('run_at')}",
            f"mode: {result.get('price_mode')}",
            f"file: {result.get('file')}",
            f"backup: {result.get('backup')}",
            f"updated: {result.get('updated')}",
            f"errors: {result.get('errors')}",
            f"total: {result.get('total')}",
            f"price_base_url: {result.get('price_base_url')}",
            f"price_token_url: {result.get('price_token_url')}",
            f"price_key_type_used: {result.get('price_key_type_used')}",
            f"price_source: {result.get('price_source')}",
            f"success: {result.get('success')}",
            f"message: {result.get('message')}",
        ]
        if result.get("price_error"):
            lines.append(f"price_errors: {result.get('price_error')}")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as exc:
        logger.warning("리포트 저장 실패: %s", exc)
    return {"report_json": str(json_path), "report_txt": str(txt_path)}


# ── 메인 함수 ─────────────────────────────────────────────────────────────────

def refresh_prices(
    date_str: str,
    top_n: int = 100,
    mode: str = "mock",
    dry_run: bool = False,
    input_path: Optional[str] = None,
    output_path: Optional[str] = None,
    limit: Optional[int] = None,
    sleep_sec: float = 0.2,
    strict: bool = False,
) -> dict:
    """후보 CSV의 현재가를 KIS API로 갱신.

    Returns:
        결과 dict — success, updated, errors, price_mode, price_key_type_used 등 포함
    """
    run_at = datetime.now().isoformat()
    mode = (mode or "mock").lower().strip()
    if dry_run:
        mode = "paper"

    # 파일 결정
    if input_path:
        path = Path(input_path)
    else:
        path = find_candidate_file(date_str, top_n)

    if path is None or not path.exists():
        msg = f"파일 없음: {input_path or f'top{top_n}_{date_str}.csv'}"
        return {"success": False, "message": msg, "updated": 0, "price_mode": mode.upper(), "run_at": run_at}

    try:
        df = pd.read_csv(path)
    except Exception as exc:
        return {"success": False, "message": f"CSV 읽기 실패: {exc}", "updated": 0, "price_mode": mode.upper(), "run_at": run_at}

    if df.empty:
        return {"success": False, "message": "빈 파일", "updated": 0, "price_mode": mode.upper(), "run_at": run_at}

    # 컬럼 자동 인식
    code_col = _find_col(df, _CODE_COLS)
    if code_col is None:
        return {"success": False, "message": f"종목코드 컬럼 없음 (찾는 컬럼: {_CODE_COLS})", "updated": 0, "price_mode": mode.upper(), "run_at": run_at}

    # 종목코드 6자리 정규화
    df[code_col] = df[code_col].apply(_norm_code)

    # limit 적용
    if limit and limit > 0:
        df = df.head(limit).copy()

    # close/current_price 컬럼 확인 (없으면 생성)
    if "close" not in df.columns:
        df["close"] = 0
    if "current_price" not in df.columns:
        df["current_price"] = df["close"]

    print(f"\n현재가 갱신 시작: {path.name}  ({len(df)}개 종목)  mode={mode.upper()}")

    # ── paper 모드: API 호출 없음 ─────────────────────────────────────────────
    if mode == "paper":
        print("[paper] API 호출 없음 — close 값을 current_price로 복사합니다.")
        now_iso = datetime.now().isoformat()
        for idx in df.index:
            close_val = float(df.at[idx, "close"] or 0)
            if close_val > 0:
                df.at[idx, "current_price"] = close_val
            df.at[idx, "price_updated_at"] = now_iso
            df.at[idx, "price_mode"] = "PAPER"
            df.at[idx, "price_source"] = "PAPER_CLOSE"
            df.at[idx, "price_base_url"] = ""
            df.at[idx, "price_token_url"] = ""
            df.at[idx, "price_key_type_used"] = ""
            df.at[idx, "price_error"] = ""

        backup = _make_backup(path)
        out = Path(output_path) if output_path else path
        df.to_csv(out, index=False, encoding="utf-8-sig")
        result = {
            "success": True,
            "message": f"paper 모드 — {len(df)}개 종목 close→current_price 복사 완료",
            "updated": len(df),
            "errors": 0,
            "total": len(df),
            "file": str(out),
            "backup": str(backup) if backup else "",
            "price_mode": "PAPER",
            "price_base_url": "",
            "price_token_url": "",
            "price_key_type_used": "",
            "price_source": "PAPER_CLOSE",
            "price_error": "",
            "run_at": run_at,
        }
        result.update(_save_report(result))
        print(f"  완료: {result['message']}")
        return result

    # ── mock/real 모드: SafetyGate + KISApiClient ─────────────────────────────
    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
    except ImportError as exc:
        return {"success": False, "message": f"모듈 import 실패: {exc}", "updated": 0, "price_mode": mode.upper(), "run_at": run_at}

    try:
        gate = SafetyGate(CONFIG_PATH, runtime_mode=mode)
        api = KISApiClient(CONFIG_PATH, gate=gate)
    except Exception as exc:
        return {"success": False, "message": f"API 클라이언트 초기화 실패: {exc}", "updated": 0, "price_mode": mode.upper(), "run_at": run_at}

    meta = api.diagnostic_metadata()
    price_base_url = meta.get("base_url", "")
    price_token_url = meta.get("token_url", "")
    price_key_type_used = meta.get("key_type_used", "")
    resolved_mode = gate.mode

    # mode-url 검증
    if mode == "mock" and "openapivts" not in price_base_url:
        return {
            "success": False,
            "message": f"MOCK 모드인데 base_url이 openapivts가 아님: {price_base_url}",
            "updated": 0, "price_mode": resolved_mode, "price_base_url": price_base_url,
            "price_key_type_used": price_key_type_used, "run_at": run_at,
        }
    if mode == "real" and "openapi.koreainvestment.com:9443" not in price_base_url:
        return {
            "success": False,
            "message": f"REAL 모드인데 base_url이 openapi.koreainvestment.com:9443가 아님: {price_base_url}",
            "updated": 0, "price_mode": resolved_mode, "price_base_url": price_base_url,
            "price_key_type_used": price_key_type_used, "run_at": run_at,
        }

    # 토큰 발급 확인
    try:
        api.auth.get_access_token()
        price_source = getattr(api.auth, "token_source", None) or "unknown"
    except Exception as exc:
        return {
            "success": False,
            "message": f"토큰 발급 실패: {exc}",
            "updated": 0,
            "price_mode": resolved_mode,
            "price_base_url": price_base_url,
            "price_token_url": price_token_url,
            "price_key_type_used": price_key_type_used,
            "price_source": "token_failed",
            "price_error": str(exc),
            "run_at": run_at,
        }

    print(f"  mode={resolved_mode}  base_url={price_base_url}")
    print(f"  key_type_used={price_key_type_used}  token_source={price_source}")

    updated = 0
    errors = 0
    price_error_list: list[str] = []
    now_iso = datetime.now().isoformat()

    for idx, row in df.iterrows():
        raw_code = row[code_col]
        code = _norm_code(raw_code)
        existing_price = float(row.get("current_price") or row.get("close") or 0)

        try:
            info = api.get_current_price(code)

            # ─── None 처리: info가 None이면 기존 가격 유지 ───────────────────
            if info is None:
                df.at[idx, "price_updated_at"] = now_iso
                df.at[idx, "price_mode"] = resolved_mode
                df.at[idx, "price_source"] = price_source
                df.at[idx, "price_base_url"] = price_base_url
                df.at[idx, "price_token_url"] = price_token_url
                df.at[idx, "price_key_type_used"] = price_key_type_used
                df.at[idx, "price_error"] = "QUOTE_NONE"
                if existing_price > 0:
                    df.at[idx, "current_price"] = existing_price  # 기존 가격 유지
                errors += 1
                price_error_list.append(f"{code}:QUOTE_NONE")
                logger.warning("현재가 조회 None 반환: %s", code)
                time.sleep(sleep_sec)
                continue

            price = 0
            if isinstance(info, dict):
                price = int(info.get("current_price") or info.get("close") or info.get("stck_prpr") or 0)
            elif isinstance(info, (int, float)):
                price = int(info)

            if price and price > 0:
                df.at[idx, "current_price"] = price
                df.at[idx, "close"] = price
                df.at[idx, "price_updated_at"] = now_iso
                df.at[idx, "price_mode"] = resolved_mode
                df.at[idx, "price_source"] = price_source
                df.at[idx, "price_base_url"] = price_base_url
                df.at[idx, "price_token_url"] = price_token_url
                df.at[idx, "price_key_type_used"] = price_key_type_used
                df.at[idx, "price_error"] = ""
                updated += 1
                print(f"  [{idx}] {code}: {price:,}원")
            else:
                df.at[idx, "price_updated_at"] = now_iso
                df.at[idx, "price_mode"] = resolved_mode
                df.at[idx, "price_source"] = price_source
                df.at[idx, "price_error"] = f"PRICE_ZERO (info={str(info)[:60]})"
                if existing_price > 0:
                    df.at[idx, "current_price"] = existing_price
                errors += 1
                price_error_list.append(f"{code}:PRICE_ZERO")

        except Exception as exc:
            err_msg = str(exc)[:120]
            logger.warning("현재가 조회 실패 %s: %s", code, err_msg)
            df.at[idx, "price_updated_at"] = now_iso
            df.at[idx, "price_mode"] = resolved_mode
            df.at[idx, "price_error"] = err_msg
            if existing_price > 0:
                df.at[idx, "current_price"] = existing_price
            errors += 1
            price_error_list.append(f"{code}:{err_msg[:60]}")
            if strict:
                break

        time.sleep(sleep_sec)

    # ── 저장 ─────────────────────────────────────────────────────────────────
    backup = _make_backup(path)
    out = Path(output_path) if output_path else path
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"  갱신 완료: {updated}개 성공, {errors}개 실패 → {out.name}")
    if backup:
        print(f"  백업: {backup.name}")

    result = {
        "success": updated > 0 or (updated == 0 and errors == 0),
        "message": f"{updated}개 현재가 갱신 완료 (mode={resolved_mode}, 실패={errors}개)",
        "updated": updated,
        "errors": errors,
        "total": len(df),
        "file": str(out),
        "backup": str(backup) if backup else "",
        "price_mode": resolved_mode,
        "price_base_url": price_base_url,
        "price_token_url": price_token_url,
        "price_key_type_used": price_key_type_used,
        "price_source": price_source,
        "price_error": "; ".join(price_error_list[:10]) if price_error_list else "",
        "run_at": run_at,
    }
    result.update(_save_report(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 후보 리스트 현재가 갱신")
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                        help="날짜 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--top", type=int, default=100, choices=[20, 50, 100],
                        help="top N (기본: 100)")
    parser.add_argument("--mode", default="mock", choices=["paper", "mock", "real"],
                        help="거래 모드 (기본: mock)")
    parser.add_argument("--input", default=None,
                        help="직접 지정 입력 CSV 경로")
    parser.add_argument("--candidate-file", default=None,
                        help="buy_top20 파일 직접 지정 (--input의 별칭, 단일 진실 공급원)")
    parser.add_argument("--output", default=None,
                        help="출력 CSV 경로 (기본: 입력 파일 덮어쓰기)")
    parser.add_argument("--limit", type=int, default=None, help="갱신할 최대 종목 수")
    parser.add_argument("--sleep", type=float, default=0.2, help="종목 간 대기 시간(초, 기본: 0.2)")
    parser.add_argument("--strict", action="store_true", help="한 종목 실패 시 전체 중단")
    parser.add_argument("--dry-run", action="store_true",
                        help="API 호출 없음 (--mode paper 와 동일)")
    parser.add_argument("--save", action="store_true",
                        help="갱신 후 파일 저장 (기본 동작과 동일, 명시적 플래그)")
    args = parser.parse_args()

    # --candidate-file 이 --input 보다 우선
    input_path = args.candidate_file or args.input
    # --candidate-file만 있고 --input이 없으면 buy_top20 파일 자동 탐색
    if not input_path:
        active = find_active_buy_candidate_file(args.date)
        if active:
            print(f"[INFO] buy_top20 파일 자동 탐색: {active}")
            input_path = str(active)

    result = refresh_prices(
        date_str=args.date,
        top_n=args.top,
        mode=args.mode,
        dry_run=args.dry_run,
        input_path=input_path,
        output_path=args.output,
        limit=args.limit,
        sleep_sec=args.sleep,
        strict=args.strict,
    )

    # JSON 구조화 결과 출력
    import json as _json
    print("\n결과 (JSON):")
    print(_json.dumps({
        "success": result.get("success"),
        "candidate_file": result.get("file", ""),
        "updated_count": result.get("updated", 0),
        "failed_count": result.get("errors", 0),
        "failed_symbols": [e.split(":")[0] for e in (result.get("price_error") or "").split(";") if e.strip()],
        "price_mode": result.get("price_mode", ""),
        "message": result.get("message", ""),
    }, ensure_ascii=False, indent=2))

    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
