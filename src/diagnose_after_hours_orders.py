"""시간외 주문구분 후보 테스트 스크립트.

MOCK API를 사용하여 PRE_MARKET / AFTER_CLOSE / AFTER_HOURS_SINGLE / REGULAR 세션별
주문구분 코드(ORD_DVSN) 후보가 한국투자증권 모의투자 서버에서 지원되는지 확인합니다.

- REAL 주문은 절대 실행하지 않습니다.
- 현재 시간이 해당 세션이 아니면 dry-run 결과만 출력합니다.
- 현재 시간이 해당 세션이면 MOCK 주문 API를 후보 코드마다 호출하고 결과를 분류합니다.

사용법:
    python src/diagnose_after_hours_orders.py --stock-code 005930 --amount 10000
    python src/diagnose_after_hours_orders.py --stock-code 105560 --amount 10000 --session PRE_MARKET
    python src/diagnose_after_hours_orders.py --stock-code 105560 --amount 10000 --session PRE_MARKET --test-all-codes
    python src/diagnose_after_hours_orders.py --stock-code 105560 --amount 10000 --session PRE_MARKET --ord-dvsn 05
    python src/diagnose_after_hours_orders.py --stock-code 105560 --amount 10000 --dry-run
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# UTF-8 강제
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
os.chdir(str(PROJECT_ROOT))

from trading_calendar import (
    TradingCalendar,
    SESSION_PRE_MARKET, SESSION_AFTER_CLOSE, SESSION_AFTER_HOURS_SINGLE,
    SESSION_REGULAR, SESSION_CLOSED, SESSION_CLOSING_AUCTION,
)
from utils import ensure_dir, setup_logger

logger = setup_logger(__name__, "logs/diagnose_after_hours.log")

# 세션별 주문구분 후보 코드 목록
ORDER_CODE_CANDIDATES: Dict[str, List[str]] = {
    SESSION_REGULAR:            ["00"],
    SESSION_PRE_MARKET:         ["05", "60"],
    SESSION_AFTER_CLOSE:        ["06", "62"],
    SESSION_AFTER_HOURS_SINGLE: ["61"],
}

# 세션별 메타정보
SESSION_META: Dict[str, Dict] = {
    SESSION_REGULAR: {
        "label": "정규장",
        "time_window": "09:00~15:20",
        "note": "ORD_DVSN=00 공식 확인됨",
    },
    SESSION_PRE_MARKET: {
        "label": "장전시간외",
        "time_window": "08:30~09:00",
        "note": "KIS 공식 문서 재확인 필요 (후보: 05, 60)",
    },
    SESSION_AFTER_CLOSE: {
        "label": "장후시간외",
        "time_window": "15:30~16:00",
        "note": "KIS 공식 문서 재확인 필요 (후보: 06, 62)",
    },
    SESSION_AFTER_HOURS_SINGLE: {
        "label": "시간외단일가",
        "time_window": "16:00~18:00",
        "note": "KIS 공식 문서 재확인 필요 (후보: 61)",
    },
}

# 응답 메시지 키워드 → 분류명
_CLASSIFICATION_RULES = [
    ("모의투자에서 제공하지 않는", "MOCK_UNSUPPORTED_ORDER_TYPE"),
    ("장종료",                    "MARKET_CLOSED_OR_WRONG_SESSION"),
    ("장이 종료",                 "MARKET_CLOSED_OR_WRONG_SESSION"),
    ("시간외",                    "MARKET_CLOSED_OR_WRONG_SESSION"),
    ("주문가능금액 부족",          "VALID_ORDER_TYPE_BUT_INSUFFICIENT_FUNDS"),
    ("잔고 부족",                 "VALID_ORDER_TYPE_BUT_INSUFFICIENT_FUNDS"),
    ("호가",                      "VALID_ORDER_TYPE_BUT_INVALID_PRICE"),
    ("가격",                      "VALID_ORDER_TYPE_BUT_INVALID_PRICE"),
]


def classify_response(rt_cd: str, msg: str) -> str:
    """API 응답을 분류한다.

    Returns:
        SUPPORTED                               — 주문 성공
        MOCK_UNSUPPORTED_ORDER_TYPE             — 모의투자 서버 미지원 주문유형
        MARKET_CLOSED_OR_WRONG_SESSION          — 장종료 또는 세션 불일치
        VALID_ORDER_TYPE_BUT_INSUFFICIENT_FUNDS — 잔고 부족 (주문구분 자체는 유효)
        VALID_ORDER_TYPE_BUT_INVALID_PRICE      — 호가단위/가격 오류
        API_REJECTED                            — 기타 거부
    """
    if rt_cd == "0":
        return "SUPPORTED"
    for keyword, classification in _CLASSIFICATION_RULES:
        if keyword in msg:
            return classification
    return "API_REJECTED"


def _print_section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


def test_single_code(
    session_key: str,
    ord_dvsn: str,
    stock_code: str,
    quantity: int,
    current_price: int,
    api,
) -> Dict:
    """단일 ORD_DVSN 코드에 대해 MOCK API를 호출하고 결과를 분류한다."""
    result: Dict = {
        "ord_dvsn": ord_dvsn,
        "api_called": True,
        "rt_cd": "",
        "msg": "",
        "classification": "",
        "mock_supported": None,
        "real_api_called": False,
        "tr_id": "",
        "order_no": "",
        "cancel_rt_cd": "",
    }
    try:
        resp = api.place_cash_buy_order(
            stock_code=stock_code,
            quantity=quantity,
            price=current_price,
            order_type="limit",
            order_session=session_key,
            ord_dvsn_override=ord_dvsn,
        )
        rt_cd = resp.get("rt_cd", "")
        raw_msg = resp.get("raw_msg", resp.get("msg1", ""))
        tr_id = resp.get("tr_id", "")
        classification = classify_response(rt_cd, raw_msg)

        result.update({
            "rt_cd": rt_cd,
            "msg": raw_msg,
            "classification": classification,
            "mock_supported": (classification == "SUPPORTED"),
            "tr_id": tr_id,
        })

        if rt_cd == "0":
            order_no = resp.get("output", {}).get("ODNO", "")
            result["order_no"] = order_no
            if order_no:
                try:
                    cancel_resp = api.cancel_order(order_no, stock_code, quantity)
                    cancel_rt = cancel_resp.get("rt_cd", "")
                    result["cancel_rt_cd"] = cancel_rt
                    print(f"    주문취소: rt_cd={cancel_rt} {'성공' if cancel_rt=='0' else '실패'}")
                except Exception as ce:
                    print(f"    주문취소 실패: {ce}")

    except NotImplementedError as e:
        result.update({
            "api_called": False,
            "classification": "REAL_BLOCKED",
            "msg": str(e),
        })
    except Exception as e:
        result.update({
            "api_called": False,
            "classification": "ERROR",
            "msg": str(e),
        })
        logger.exception("test_single_code 예외 [세션=%s ord_dvsn=%s]", session_key, ord_dvsn)

    return result


def diagnose_session(
    session_key: str,
    stock_code: str,
    amount: float,
    current_session: str,
    mock_keys_ok: bool,
    codes_to_test: List[str],
    dry_run: bool = False,
) -> List[Dict]:
    """단일 세션에 대한 진단 수행. 코드별 결과 리스트를 반환한다."""
    meta = SESSION_META.get(session_key, {})
    label = meta.get("label", session_key)
    time_window = meta.get("time_window", "")
    note = meta.get("note", "")
    in_session = (current_session == session_key)

    _print_section(f"{label} ({session_key})")
    print(f"  예상 시간대    : {time_window}")
    print(f"  테스트 코드    : {codes_to_test}")
    print(f"  현재 세션 해당 : {'YES — API 호출 가능' if in_session else 'NO'}")
    print(f"  참고           : {note}")

    base = {
        "session": session_key,
        "label": label,
        "expected_time_window": time_window,
        "current_time_in_session": in_session,
        "real_api_called": False,
        "note": note,
    }

    skip_reason = None
    skip_cl = "DRY_RUN"
    if dry_run:
        skip_reason = "DRY-RUN (--dry-run 옵션)"
    elif not in_session:
        skip_reason = f"세션 외 시간 (현재: {current_session}) — {time_window}에 재실행"
    elif not mock_keys_ok:
        skip_reason = "MOCK 키 없음 (.env KIS_MOCK_APP_KEY 등 확인)"
        skip_cl = "SKIP"

    if skip_reason:
        print(f"  상태           : {skip_reason}")
        return [
            {**base, "ord_dvsn": code, "api_called": False, "rt_cd": "",
             "msg": skip_reason, "classification": skip_cl,
             "mock_supported": None, "tr_id": "", "order_no": "", "cancel_rt_cd": ""}
            for code in codes_to_test
        ]

    # MOCK API 호출
    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
        from dotenv import load_dotenv
        load_dotenv()

        gate = SafetyGate("config.yaml", runtime_mode="mock")
        api = KISApiClient("config.yaml", gate=gate)

        price_info = api.get_current_price(stock_code)
        current_price = int(price_info.get("current_price", 0))
        if current_price <= 0:
            msg = f"현재가 조회 실패 ({current_price})"
            print(f"  상태           : {msg}")
            return [{**base, "ord_dvsn": code, "api_called": False, "rt_cd": "",
                     "msg": msg, "classification": "PRICE_FETCH_FAILED",
                     "mock_supported": None, "tr_id": "", "order_no": "", "cancel_rt_cd": ""}
                    for code in codes_to_test]

        quantity = max(1, int(amount // current_price))
        print(f"  현재가         : {current_price:,}원")
        print(f"  주문수량       : {quantity}주 ({amount:,.0f}원 기준)")
        print()

        rows = []
        for code in codes_to_test:
            print(f"  → ORD_DVSN={code} 테스트 중...")
            row = test_single_code(session_key, code, stock_code, quantity, current_price, api)
            row.update(base)
            row["ord_dvsn"] = code
            rows.append(row)
            cl = row.get("classification", "")
            rt = row.get("rt_cd") or "-"
            msg_short = (row.get("msg") or "")[:40]
            print(f"    rt_cd={rt}  분류={cl}  메시지={msg_short}")
        return rows

    except Exception as e:
        logger.exception("diagnose_session 예외 [%s]", session_key)
        print(f"  오류: {e}")
        return [{**base, "ord_dvsn": code, "api_called": False, "rt_cd": "",
                 "msg": str(e), "classification": "ERROR",
                 "mock_supported": None, "tr_id": "", "order_no": "", "cancel_rt_cd": ""}
                for code in codes_to_test]


def run_diagnosis(
    stock_code: str,
    amount: float,
    target_session: Optional[str] = None,
    test_all_codes: bool = False,
    ord_dvsn_override: Optional[str] = None,
    dry_run: bool = False,
) -> List[Dict]:
    """전체 또는 특정 세션 진단 실행."""
    from dotenv import load_dotenv, dotenv_values
    load_dotenv()
    env = dotenv_values(".env") or {}

    mock_key_ok = bool(env.get("KIS_MOCK_APP_KEY") or env.get("KIS_APP_KEY") or os.getenv("KIS_APP_KEY"))
    mock_sec_ok = bool(env.get("KIS_MOCK_APP_SECRET") or env.get("KIS_APP_SECRET") or os.getenv("KIS_APP_SECRET"))
    mock_acc_ok = bool(env.get("KIS_MOCK_ACCOUNT_NO") or os.getenv("KIS_MOCK_ACCOUNT_NO"))
    mock_keys_ok = mock_key_ok and mock_sec_ok and mock_acc_ok

    cal = TradingCalendar("config.yaml")
    current_session = cal.get_market_session()
    now = datetime.now()

    testable = list(ORDER_CODE_CANDIDATES.keys())
    sessions = [target_session] if (target_session and target_session in testable) else testable

    _print_section(f"시간외 주문구분 후보 테스트 — {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  종목코드       : {stock_code}")
    print(f"  테스트 금액    : {amount:,.0f}원")
    print(f"  현재 세션      : {current_session}")
    print(f"  MOCK 키 상태   : {'완비' if mock_keys_ok else 'MOCK 키 누락'}")
    print(f"  REAL 주문      : 절대 실행하지 않음")
    if dry_run:
        print(f"  모드           : DRY-RUN")
    elif ord_dvsn_override:
        print(f"  ORD_DVSN 지정  : {ord_dvsn_override} (--ord-dvsn)")
    elif test_all_codes:
        print(f"  테스트 모드    : 전체 후보 코드 (--test-all-codes)")
    else:
        print(f"  테스트 모드    : 기본 코드 1개")

    all_results: List[Dict] = []
    for sess in sessions:
        candidates = ORDER_CODE_CANDIDATES.get(sess, [])
        if ord_dvsn_override:
            codes = [ord_dvsn_override]
        elif test_all_codes:
            codes = candidates
        else:
            codes = [candidates[0]] if candidates else []

        if not codes:
            continue

        rows = diagnose_session(
            session_key=sess,
            stock_code=stock_code,
            amount=amount,
            current_session=current_session,
            mock_keys_ok=mock_keys_ok,
            codes_to_test=codes,
            dry_run=dry_run,
        )
        all_results.extend(rows)

    return all_results


def _print_results_table(results: List[Dict]) -> None:
    """결과를 표 형식으로 출력한다."""
    _print_section("테스트 결과 요약")
    hdr = f"  {'세션':<22} {'ORD_DVSN':<10} {'API호출':<7} {'rt_cd':<6} {'분류':<43} {'메시지 (앞 30자)'}"
    print(hdr)
    print("  " + "-" * 115)

    prev_sess = ""
    for r in results:
        sess = r.get("session", "")
        label = r.get("label", sess)
        display = f"{label}({sess})" if sess != prev_sess else ""
        prev_sess = sess
        api_called = "YES" if r.get("api_called") else "NO"
        rt_cd = r.get("rt_cd") or "-"
        cl = r.get("classification") or "-"
        msg = (r.get("msg") or "")[:30]
        code = r.get("ord_dvsn", "-")
        print(f"  {display:<22} {code:<10} {api_called:<7} {rt_cd:<6} {cl:<43} {msg}")
    print()


def save_reports(results: List[Dict], stock_code: str, amount: float) -> None:
    """진단 결과를 txt / json 파일로 저장."""
    ensure_dir("reports")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = PROJECT_ROOT / "reports" / f"after_hours_diagnosis_{ts}.txt"
    json_path = PROJECT_ROOT / "reports" / f"after_hours_diagnosis_{ts}.json"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "=" * 60,
        "  시간외 주문구분 후보 테스트 보고서",
        f"  생성시각 : {now_str}",
        f"  종목코드 : {stock_code}",
        f"  테스트금액: {amount:,.0f}원",
        "=" * 60,
        "",
        f"  {'세션':<22} {'ORD_DVSN':<10} {'API호출':<7} {'rt_cd':<6} {'분류':<43} {'메시지'}",
        "  " + "-" * 115,
    ]
    for r in results:
        sess_label = f"{r.get('label','')}({r.get('session','')})"
        api_called = "YES" if r.get("api_called") else "NO"
        lines.append(
            f"  {sess_label:<22} {r.get('ord_dvsn',''):<10} {api_called:<7} "
            f"{r.get('rt_cd',''):<6} {r.get('classification',''):<43} {(r.get('msg') or '')[:50]}"
        )
    lines.append("")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": now_str,
            "stock_code": stock_code,
            "amount": amount,
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"진단 보고서 저장:")
    print(f"  {txt_path}")
    print(f"  {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="시간외 주문구분 후보 테스트 — MOCK API 기준, REAL 주문 절대 없음"
    )
    parser.add_argument("--stock-code", default="005930", help="종목코드 (기본: 005930)")
    parser.add_argument("--amount", type=float, default=10000.0, help="테스트 금액 원 (기본: 10000)")
    parser.add_argument(
        "--session",
        choices=list(ORDER_CODE_CANDIDATES.keys()),
        default=None,
        help="특정 세션만 진단 (기본: 전체 세션)",
    )
    parser.add_argument(
        "--test-all-codes",
        action="store_true",
        help="해당 세션의 주문구분 후보 코드를 모두 테스트",
    )
    parser.add_argument(
        "--ord-dvsn",
        type=str,
        default=None,
        metavar="CODE",
        help="특정 주문구분 코드만 테스트 (예: --ord-dvsn 05)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API 호출 없이 주문구분 해석만 출력",
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    results = run_diagnosis(
        stock_code=args.stock_code,
        amount=args.amount,
        target_session=args.session,
        test_all_codes=args.test_all_codes,
        ord_dvsn_override=args.ord_dvsn,
        dry_run=args.dry_run,
    )

    _print_results_table(results)
    save_reports(results, args.stock_code, args.amount)

    print("\n[참고] 시간외 주문구분 코드 확인 방법:")
    print("  KIS Developers: https://apiportal.koreainvestment.com/")
    print("  국내주식 현금주문 → ORD_DVSN 코드표 참조")
    print("  REAL 주문은 공식 문서에서 코드 확인 후에만 사용하세요.")


if __name__ == "__main__":
    main()
