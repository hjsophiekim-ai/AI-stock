"""수동매도 경로 진단 스크립트.

MOCK/REAL 매도 경로가 올바른지 주문 전에 검증합니다.
--dry-run: 주문 직전까지만 검증 (API 주문 없음)
--execute: 실제 MOCK/REAL 매도 주문 실행

사용법:
    python src/manual_sell_diagnosis.py --mode mock --stock-code 055550 --quantity 1 --dry-run
    python src/manual_sell_diagnosis.py --mode mock --stock-code 055550 --quantity 1 --execute
    python src/manual_sell_diagnosis.py --mode mock --stock-code 055550 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import yaml

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent

if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

os.chdir(str(PROJECT_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from kis_api import KISApiClient
from kis_auth import fingerprint_key, get_kis_credentials
from price_tick import adjust_price_to_tick
from real_order_utils import normalize_stock_code
from safety_gate import SafetyGate
from trade_mode import get_expected_key_fingerprint_for_mode
from trading_calendar import TradingCalendar
from utils import ensure_dir, load_config


def _make_mode_config(mode: str, config_path: str) -> str:
    """모드 전용 임시 config 생성."""
    cfg = load_config(config_path)
    mode = mode.lower()
    if mode == "mock":
        cfg["live_trade"] = False
        cfg["paper_trade"] = False
        cfg.setdefault("kis", {})["use_mock"] = True
        cfg["kis"]["mock_app_key_env"] = "KIS_MOCK_APP_KEY"
        cfg["kis"]["mock_app_secret_env"] = "KIS_MOCK_APP_SECRET"
        cfg["kis"]["mock_token_cache_file"] = "data/mock_token_cache.json"
    elif mode == "real":
        cfg["live_trade"] = True
        cfg["paper_trade"] = False
        cfg.setdefault("kis", {})["use_mock"] = False
    fd, path = tempfile.mkstemp(prefix=f"ai_stock_{mode}_", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    return path


def _save_report(result: Dict[str, Any]) -> Dict[str, str]:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = PROJECT_ROOT / "reports" / f"manual_sell_diagnosis_{ts}.json"
    txt_path = PROJECT_ROOT / "reports" / f"manual_sell_diagnosis_{ts}.txt"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    lines = [
        "MANUAL SELL DIAGNOSIS REPORT",
        f"run_at: {result.get('run_at')}",
        f"mode: {result.get('requested_mode')} → {result.get('resolved_mode')}",
        f"stock_code: {result.get('stock_code')}",
        f"quantity: {result.get('quantity')}",
        f"dry_run: {result.get('dry_run')}",
        "",
        f"base_url: {result.get('base_url')}",
        f"token_url: {result.get('token_url')}",
        f"key_type_used: {result.get('key_type_used')}",
        f"token_cache_file: {result.get('token_cache_file')}",
        "",
        f"expected_appkey_fingerprint: {result.get('expected_appkey_fingerprint')}",
        f"header_appkey_fingerprint: {result.get('header_appkey_fingerprint')}",
        f"app_key_mode_valid: {result.get('app_key_mode_valid')}",
        f"mode_url_valid: {result.get('mode_url_valid')}",
        "",
        f"current_price: {result.get('current_price')}",
        f"sell_price: {result.get('sell_price')}",
        f"broker_quantity: {result.get('broker_quantity')}",
        f"local_quantity: {result.get('local_quantity')}",
        "",
        f"tr_id: {result.get('tr_id')}",
        f"api_called: {result.get('api_called')}",
        f"mock_order_called: {result.get('mock_order_called')}",
        f"real_order_called: {result.get('real_order_called')}",
        "",
        f"order_no: {result.get('order_no')}",
        f"rt_cd: {result.get('rt_cd')}",
        f"msg: {result.get('msg')}",
        f"success: {result.get('success')}",
    ]
    errors = result.get("diagnosis_errors", [])
    if errors:
        lines += ["", "DIAGNOSIS ERRORS:"] + [f"  [!] {e}" for e in errors]
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return {"json_path": str(json_path), "txt_path": str(txt_path)}


def run_manual_sell_diagnosis(
    stock_code: str,
    mode: str = "mock",
    quantity: int = None,
    dry_run: bool = True,
    config_path: str = "config.yaml",
) -> Dict[str, Any]:
    """매도 경로 진단.

    Args:
        stock_code: 종목코드
        mode: mock / real / paper
        quantity: 매도 수량 (None이면 positions.json 또는 KIS 잔고에서 자동 결정)
        dry_run: True이면 매도 주문 직전까지만 검증 (실제 주문 없음)
        config_path: config.yaml 경로
    """
    code = normalize_stock_code(stock_code)
    mode = (mode or "mock").lower()
    tmp_path = None

    expected_fp = get_expected_key_fingerprint_for_mode(mode.upper())
    creds_check = get_kis_credentials(mode.upper())
    actual_fp_pre = creds_check.get("appkey_fingerprint", "MISSING")

    result: Dict[str, Any] = {
        "run_at": datetime.now().isoformat(),
        "requested_mode": mode.upper(),
        "resolved_mode": "",
        "stock_code": code,
        "quantity": quantity,
        "dry_run": dry_run,
        "base_url": "",
        "token_url": "",
        "key_type_used": "",
        "token_cache_file": "",
        "app_key_mode_valid": False,
        "mode_url_valid": False,
        "expected_appkey_fingerprint": expected_fp,
        "header_appkey_fingerprint": actual_fp_pre,
        "current_price": 0,
        "sell_price": 0,
        "broker_quantity": 0,
        "local_quantity": 0,
        "tr_id": "",
        "api_called": False,
        "mock_order_called": False,
        "real_order_called": False,
        "order_no": "",
        "rt_cd": "",
        "msg": "",
        "success": False,
        "diagnosis_errors": [],
    }

    if expected_fp != actual_fp_pre and expected_fp not in ("MISSING", "N/A") and actual_fp_pre not in ("MISSING",):
        result["diagnosis_errors"].append(
            f"사전 검증 실패: expected={expected_fp} actual={actual_fp_pre}"
        )

    try:
        tmp_path = _make_mode_config(mode, config_path) if mode in ("mock",) else config_path
        gate = SafetyGate(tmp_path, runtime_mode=mode)
        api = KISApiClient(tmp_path, gate=gate)
        result["resolved_mode"] = gate.mode

        meta = api.diagnostic_metadata()
        result.update(meta)
        result["header_appkey_fingerprint"] = meta.get("appkey_fingerprint", actual_fp_pre)

        # appkey 일치 확인
        actual_header_fp = meta.get("appkey_fingerprint", "MISSING")
        if expected_fp not in ("MISSING", "N/A") and actual_header_fp != expected_fp:
            result["diagnosis_errors"].append(
                f"KISApiClient 헤더 appkey 불일치: expected={expected_fp} actual={actual_header_fp}"
            )
            result["app_key_mode_valid"] = False
        elif expected_fp not in ("MISSING", "N/A"):
            result["app_key_mode_valid"] = True

        # 토큰 발급
        try:
            api.auth.get_access_token()
        except Exception as exc:
            result["msg"] = f"token failed: {exc}"
            result["diagnosis_errors"].append(f"토큰 발급 실패: {exc}")
            paths = _save_report(result)
            result.update(paths)
            return result

        # 현재가 조회
        try:
            quote = api.get_current_price(code)
            if quote is not None:
                cur = int(quote.get("current_price", 0) or 0)
            else:
                cur = 0
            result["current_price"] = cur
            sell_price = adjust_price_to_tick(cur, side="sell", method="floor") if cur > 0 else 0
            result["sell_price"] = sell_price
        except Exception as exc:
            result["diagnosis_errors"].append(f"현재가 조회 실패: {exc}")
            sell_price = 0

        # 로컬 포지션 확인
        try:
            from position_manager import PositionManager
            pm = PositionManager(config_path)
            pos = pm.get_position(code)
            if pos:
                result["local_quantity"] = int(pos.quantity or 0)
                if quantity is None:
                    quantity = result["local_quantity"]
                    result["quantity"] = quantity
        except Exception as exc:
            result["diagnosis_errors"].append(f"로컬 포지션 조회 실패: {exc}")

        # KIS 계좌 잔고 확인
        try:
            positions_df = api.get_positions()
            if positions_df is not None and not positions_df.empty:
                row = positions_df[positions_df["stock_code"].astype(str).str.zfill(6) == code]
                if not row.empty:
                    result["broker_quantity"] = int(row.iloc[0].get("quantity", 0) or 0)
        except Exception as exc:
            result["diagnosis_errors"].append(f"KIS 잔고 조회 실패 (무시): {exc}")

        # dry-run이면 여기서 종료
        if dry_run:
            result["msg"] = "dry-run 완료 — 매도 주문 실행하지 않음"
            result["success"] = result["app_key_mode_valid"] and sell_price > 0
            paths = _save_report(result)
            result.update(paths)
            return result

        # 실제 매도 주문
        if not quantity or quantity <= 0:
            result["msg"] = "매도 수량 없음 — 주문 생략"
            result["diagnosis_errors"].append("매도 수량 결정 불가 (positions.json 또는 --quantity 지정 필요)")
            paths = _save_report(result)
            result.update(paths)
            return result

        if not sell_price or sell_price <= 0:
            result["msg"] = "매도가 결정 불가 — 주문 생략"
            result["diagnosis_errors"].append("현재가 조회 실패로 매도가 결정 불가")
            paths = _save_report(result)
            result.update(paths)
            return result

        from trading_calendar import SESSION_REGULAR
        cal = TradingCalendar(config_path)
        sell_session = cal.get_market_session()

        result["api_called"] = True
        if gate.mode == "MOCK":
            result["mock_order_called"] = True
        if gate.mode == "REAL":
            result["real_order_called"] = True

        resp = api.place_cash_sell_order(code, int(quantity), int(sell_price), "limit", sell_session)
        final_meta = api.diagnostic_metadata()
        result.update(final_meta)
        result["header_appkey_fingerprint"] = final_meta.get("appkey_fingerprint", result["header_appkey_fingerprint"])

        rt_cd = resp.get("rt_cd", "")
        order_no = resp.get("output", {}).get("ODNO", "")
        result["rt_cd"] = rt_cd
        result["msg"] = resp.get("msg1", resp.get("raw_msg", ""))
        result["order_no"] = order_no
        result["tr_id"] = resp.get("tr_id", "")
        result["success"] = bool(rt_cd == "0" and order_no)

        if not result["success"] and "모의투자용 앱키가 아닙니다" in result.get("msg", ""):
            result["diagnosis_errors"].append(
                f"'모의투자용 앱키가 아닙니다' 오류 — REAL 키가 MOCK 서버로 전송됨 "
                f"expected={expected_fp} actual={result['header_appkey_fingerprint']}"
            )

    except Exception as exc:
        result["msg"] = str(exc)
        result["diagnosis_errors"].append(f"예외: {exc}")
    finally:
        if tmp_path and tmp_path != config_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    paths = _save_report(result)
    result.update(paths)
    return result


def _print_result(result: dict) -> None:
    sep = "-" * 60
    print("=" * 60)
    print("MANUAL SELL DIAGNOSIS")
    print("=" * 60)
    fields = [
        "requested_mode", "resolved_mode", "dry_run",
        "base_url", "token_url", "key_type_used", "token_cache_file",
        "expected_appkey_fingerprint", "header_appkey_fingerprint",
        "app_key_mode_valid", "mode_url_valid",
        "stock_code", "quantity", "current_price", "sell_price",
        "broker_quantity", "local_quantity",
        "tr_id", "api_called", "mock_order_called", "real_order_called",
        "order_no", "rt_cd", "msg", "success",
    ]
    for f in fields:
        print(f"  {f:<40} = {result.get(f, '')}")
    errors = result.get("diagnosis_errors", [])
    if errors:
        print(sep)
        print("  DIAGNOSIS ERRORS:")
        for e in errors:
            print(f"    [!] {e}")
    print(sep)
    print(f"  json: {result.get('json_path', '')}")
    print(f"  txt:  {result.get('txt_path', '')}")
    print("=" * 60)


def run_all_manual_sell_diagnosis(
    mode: str = "mock",
    dry_run: bool = True,
    config_path: str = "config.yaml",
) -> Dict[str, Any]:
    """positions.json + KIS 계좌 기준 전체 보유종목 일괄 진단."""
    from position_manager import PositionManager

    positions = {}
    try:
        pm = PositionManager(config_path)
        positions = pm.get_all_positions() or {}
    except Exception as exc:
        print(f"  [WARN] positions.json 읽기 실패: {exc}")

    # KIS 계좌에서 추가 종목 보완
    try:
        tmp_path = _make_mode_config(mode, config_path) if mode == "mock" else config_path
        gate = SafetyGate(tmp_path, runtime_mode=mode)
        api = KISApiClient(tmp_path, gate=gate)
        broker_df = api.get_positions()
        if broker_df is not None and not broker_df.empty:
            for _, row in broker_df.iterrows():
                code = str(row.get("stock_code", "")).zfill(6)
                qty = int(row.get("quantity", 0) or 0)
                if qty > 0 and code not in positions:
                    positions[code] = type("P", (), {"stock_code": code, "stock_name": row.get("stock_name", ""), "quantity": qty})()
        if tmp_path != config_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as exc:
        print(f"  [WARN] KIS 잔고 조회 실패 (무시): {exc}")

    if not positions:
        result = {
            "run_at": datetime.now().isoformat(),
            "mode": mode.upper(),
            "all": True,
            "total": 0,
            "success_count": 0,
            "fail_count": 0,
            "results": [],
            "message": "보유종목 없음",
        }
        print("  [INFO] 보유종목 없음 — 매도 대상 없음")
        return result

    print(f"\n전량 일괄매도 진단 시작: {len(positions)}개 종목  mode={mode.upper()}  dry_run={dry_run}")
    print("-" * 60)

    all_results = []
    success_count = 0
    fail_count = 0

    for code, pos in positions.items():
        qty = int(getattr(pos, "quantity", 0) or 0)
        name = getattr(pos, "stock_name", "")
        if qty <= 0:
            print(f"  [SKIP] {code} {name} — 수량 0")
            all_results.append({"stock_code": code, "stock_name": name, "quantity": 0,
                                 "success": False, "rejected_reason": "NO_POSITION_TO_SELL"})
            continue

        print(f"  [{code}] {name} {qty}주 진단 중...")
        r = run_manual_sell_diagnosis(
            stock_code=code,
            mode=mode,
            quantity=qty,
            dry_run=dry_run,
            config_path=config_path,
        )
        all_results.append(r)
        if r.get("success"):
            success_count += 1
            print(f"    → {'DRY-RUN OK' if dry_run else 'SUCCESS'} | order_no={r.get('order_no','')} key={r.get('key_type_used','')}")
        else:
            fail_count += 1
            print(f"    → FAIL | {r.get('msg','')} | errors={r.get('diagnosis_errors',[])}")

    summary = {
        "run_at": datetime.now().isoformat(),
        "mode": mode.upper(),
        "dry_run": dry_run,
        "all": True,
        "total": len(positions),
        "success_count": success_count,
        "fail_count": fail_count,
        "results": all_results,
    }
    print("-" * 60)
    print(f"  완료: 총 {len(positions)}개 | 성공 {success_count}개 | 실패 {fail_count}개")

    # 요약 리포트 저장
    ensure_dir(str(PROJECT_ROOT / "reports"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = PROJECT_ROOT / "reports" / f"manual_sell_diagnosis_all_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    summary["json_path"] = str(json_path)
    print(f"  리포트: {json_path}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="수동매도 경로 진단")
    parser.add_argument("--stock-code", default=None, help="종목코드 (--all 사용 시 생략 가능)")
    parser.add_argument("--all", action="store_true", help="전체 보유종목 일괄 진단")
    parser.add_argument("--mode", default="mock", choices=["mock", "real", "paper"], help="거래 모드")
    parser.add_argument("--quantity", type=int, default=None, help="매도 수량 (없으면 포지션 전량)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="주문 직전까지만 검증 (기본)")
    parser.add_argument("--execute", action="store_true", help="실제 매도 주문 실행")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args()

    if not args.stock_code and not args.all:
        parser.error("--stock-code 또는 --all 중 하나를 지정해야 합니다.")

    dry = not args.execute

    if args.all:
        result = run_all_manual_sell_diagnosis(
            mode=args.mode,
            dry_run=dry,
            config_path=args.config,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        total_fail = result.get("fail_count", 0)
        if total_fail > 0 and not dry:
            sys.exit(1)
        return

    result = run_manual_sell_diagnosis(
        stock_code=args.stock_code,
        mode=args.mode,
        quantity=args.quantity,
        dry_run=dry,
        config_path=args.config,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_result(result)

    if not result.get("success") and not dry:
        sys.exit(1)


if __name__ == "__main__":
    main()
