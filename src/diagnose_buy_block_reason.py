"""매수 차단 원인 진단 스크립트.

사용법:
    python src/diagnose_buy_block_reason.py --mode mock

출력:
    position_path, broker_count, local_open_count, pending_sell_count,
    max_positions, buy_allowed, block_reason, pending_sell_symbols,
    closed_symbols, suggested_action
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def diagnose(mode: str = "mock", config_path: str = "config.yaml") -> dict:
    config_full = str(PROJECT_ROOT / config_path)

    # --- 1. 로컬 포지션 분석 ---
    from position_manager import PositionManager
    pm = PositionManager(config_full, mode=mode)
    all_pos = pm.get_all_positions()

    open_pos = [
        p for p in all_pos.values()
        if not p.is_closed and p.status == "OPEN" and int(p.quantity) > 0
    ]
    pending_pos = [
        p for p in all_pos.values()
        if p.status == "OPEN_WITH_PENDING_SELL"
    ]
    closed_pos = [
        p for p in all_pos.values()
        if p.is_closed or p.status == "CLOSED"
    ]

    # --- 2. config max_positions ---
    from utils import load_config
    cfg = load_config(config_full)
    max_positions = cfg.get("risk", {}).get("max_positions", 20)

    # --- 3. KIS 브로커 조회 ---
    broker_count = None
    broker_error = None
    try:
        from kis_api import KISApiClient
        from safety_gate import SafetyGate
        gate = SafetyGate(config_full, runtime_mode=mode)
        api = KISApiClient(config_full, gate=gate)
        broker_df = api.get_positions()
        broker_count = int(len(broker_df)) if broker_df is not None and not broker_df.empty else 0
    except Exception as ex:
        broker_error = str(ex)
        broker_count = None

    # --- 4. 차단 원인 판단 ---
    local_open_count = len(open_pos)
    pending_sell_count = len(pending_pos)
    closed_count = len(closed_pos)
    buy_allowed = local_open_count < max_positions

    block_reason = ""
    suggested_action = ""

    if buy_allowed:
        block_reason = "없음 — 매수 가능"
        suggested_action = "전부 매수를 실행하세요."
    else:
        block_reason = (
            f"최대 보유 종목 수 초과: OPEN {local_open_count}/{max_positions}"
        )
        if pending_sell_count > 0:
            block_reason += f" | 미체결 매도 {pending_sell_count}건"
            suggested_action = (
                "보유종목 페이지 → '계좌 일괄매도' 후 자동 동기화가 실행됩니다.\n"
                "또는: python src/sync_broker_positions.py --mode mock --apply --close-missing"
            )
        else:
            suggested_action = (
                "포지션 파일에 OPEN 종목이 남아 있습니다.\n"
                f"  python src/sync_broker_positions.py --mode {mode} --apply --close-missing"
            )

    result = {
        "mode": mode,
        "position_path": str(pm._positions_file),
        "total_positions": len(all_pos),
        "local_open_count": local_open_count,
        "pending_sell_count": pending_sell_count,
        "closed_count": closed_count,
        "broker_count": broker_count if broker_count is not None else "조회실패",
        "broker_error": broker_error or "",
        "max_positions": max_positions,
        "buy_allowed": buy_allowed,
        "block_reason": block_reason,
        "pending_sell_symbols": [
            {"code": p.stock_code, "name": p.stock_name, "order_no": p.pending_sell_order_no}
            for p in pending_pos
        ],
        "open_symbols": [
            {"code": p.stock_code, "name": p.stock_name, "qty": p.quantity}
            for p in open_pos
        ],
        "closed_symbols": [
            {"code": p.stock_code, "name": p.stock_name}
            for p in closed_pos[:10]
        ],
        "suggested_action": suggested_action,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="매수 차단 원인 진단")
    parser.add_argument("--mode", default="mock", choices=["paper", "mock", "real"])
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    result = diagnose(mode=args.mode, config_path=args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print(f"  매수 {'✅ 가능' if result['buy_allowed'] else '❌ 차단'}")
    print(f"  OPEN: {result['local_open_count']}  |  미체결매도: {result['pending_sell_count']}  |  max: {result['max_positions']}")
    if result["block_reason"] and not result["buy_allowed"]:
        print(f"  차단 원인: {result['block_reason']}")
    print(f"  조치: {result['suggested_action']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
