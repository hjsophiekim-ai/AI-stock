"""Synchronize KIS broker positions into data/positions.json."""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from position_manager import PositionManager
from price_tick import adjust_price_to_tick
from safety_gate import SafetyGate
from strategy_config import get_strategy
from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sync_broker_positions(
    mode: str = "mock",
    strategy: str = "morning_0930",
    config_path: str = "config.yaml",
    apply: bool = False,
    close_missing: bool = False,
    purge_missing: bool = False,
) -> dict:
    """Sync KIS broker positions to local positions.json.

    Args:
        mode: Trade mode (paper/mock/real)
        strategy: Strategy ID for new positions
        config_path: Path to config.yaml
        apply: When True, apply close_missing/purge_missing changes
        close_missing: When True (with apply=True), mark local OPEN positions
                       absent from broker as CLOSED with reason
                       BROKER_POSITION_MISSING_AFTER_SYNC
        purge_missing: When True (with apply=True), delete missing positions
                       entirely (overrides close_missing)
    """
    gate = SafetyGate(config_path, runtime_mode=mode)
    api = KISApiClient(config_path, gate=gate)
    broker_df = api.get_positions()
    pm = PositionManager(config_path)
    local_open_before = pm.get_open_positions()
    strategy_cfg = get_strategy(strategy)
    now = datetime.now()
    now_str = now.isoformat()
    today = now.strftime("%Y%m%d")

    ensure_dir(str(PROJECT_ROOT / "data"))
    ensure_dir(str(PROJECT_ROOT / "reports"))

    if broker_df is None:
        broker_df = pd.DataFrame()
    with open(PROJECT_ROOT / "data" / "broker_positions.json", "w", encoding="utf-8") as f:
        json.dump(broker_df.to_dict("records"), f, ensure_ascii=False, indent=2)

    # Backup positions.json before applying destructive changes
    backup_path = None
    if apply and (close_missing or purge_missing):
        backup_dir = PROJECT_ROOT / "reports" / "position_backups"
        ensure_dir(str(backup_dir))
        positions_file = PROJECT_ROOT / "data" / "positions.json"
        if positions_file.exists():
            backup_path = backup_dir / f"positions_before_sync_{today}.json"
            shutil.copy2(str(positions_file), str(backup_path))

    diff_rows = []
    broker_codes = set()
    closed_count = 0
    purged_count = 0

    if not broker_df.empty:
        for _, row in broker_df.iterrows():
            code = str(row.get("stock_code", "")).zfill(6)
            broker_codes.add(code)
            qty = int(row.get("quantity", 0) or 0)
            avg_price = float(row.get("avg_price", 0) or 0)
            current_price = float(row.get("current_price", avg_price) or avg_price)
            existing = pm.get_position(code)
            pos = pm.update_position_after_buy(
                stock_code=code,
                stock_name=str(row.get("stock_name", code)),
                quantity=qty,
                entry_price=avg_price,
                entry_time=datetime.now(),
                strategy_id=(getattr(existing, "strategy_id", "") if existing else "") or strategy_cfg["id"],
                strategy_name=(getattr(existing, "strategy_name", "") if existing else "") or strategy_cfg["name"],
                take_profit_rate=strategy_cfg["take_profit_rate"],
                stop_loss_rate=strategy_cfg["stop_loss_rate"],
                allowed_sell_sessions=strategy_cfg["allowed_sell_sessions"],
                source="broker",
                replace_existing=True,
                sell_policy_id=(getattr(existing, "sell_policy_id", "") if existing else ""),
            )
            pos.current_price = current_price
            pos.avg_price = avg_price
            pos.profit_loss_rate = float(row.get("pnl_rate", 0) or 0)
            pos.target_price = adjust_price_to_tick(round(avg_price * 1.02), side="sell", method="ceil")
            pos.stop_price = adjust_price_to_tick(round(avg_price * 0.97), side="sell", method="ceil")
            pos.stop_loss_price = pos.stop_price
            pos.broker_synced_at = now_str
            diff_rows.append({
                "stock_code": code,
                "status": "broker_and_local",
                "quantity": qty,
                "avg_price": avg_price,
            })

    for code, pos in local_open_before.items():
        if code not in broker_codes:
            if apply:
                cur_pos = pm.get_position(code)
                if cur_pos and not cur_pos.is_closed:
                    if purge_missing:
                        pm.remove_position(code)
                        purged_count += 1
                        diff_rows.append({
                            "stock_code": code,
                            "status": "local_only_purged",
                            "quantity": pos.quantity,
                            "avg_price": pos.entry_price,
                        })
                    elif close_missing:
                        cur_pos.is_closed = True
                        cur_pos.status = "CLOSED"
                        cur_pos.quantity = 0
                        cur_pos.exit_reason = "BROKER_POSITION_MISSING_AFTER_SYNC"
                        cur_pos.exit_time = now_str
                        closed_count += 1
                        diff_rows.append({
                            "stock_code": code,
                            "status": "local_only_closed",
                            "quantity": 0,
                            "avg_price": pos.entry_price,
                            "closed_reason": "BROKER_POSITION_MISSING_AFTER_SYNC",
                        })
                    else:
                        diff_rows.append({
                            "stock_code": code,
                            "status": "local_only",
                            "quantity": pos.quantity,
                            "avg_price": pos.entry_price,
                        })
                else:
                    diff_rows.append({
                        "stock_code": code,
                        "status": "local_only",
                        "quantity": pos.quantity,
                        "avg_price": pos.entry_price,
                    })
            else:
                diff_rows.append({
                    "stock_code": code,
                    "status": "local_only",
                    "quantity": pos.quantity,
                    "avg_price": pos.entry_price,
                })

    pm.save_local_positions()

    diff_path = PROJECT_ROOT / "reports" / f"account_position_diff_{today}.csv"
    pd.DataFrame(diff_rows).to_csv(diff_path, index=False, encoding="utf-8-sig")

    open_count = pm.open_position_count()
    result = {
        "success": True,
        "mode": mode,
        "resolved_mode": gate.mode,
        "broker_count": int(len(broker_df)),
        "local_count": int(len(pm.get_all_positions())),
        "open_count": open_count,
        "closed_count": closed_count,
        "purged_count": purged_count,
        "diff_path": str(diff_path),
        "backup_path": str(backup_path) if backup_path else "",
        "apply": apply,
        "close_missing": close_missing,
    }

    report_path = PROJECT_ROOT / "reports" / f"sync_report_{today}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS broker positions → local positions.json 동기화")
    parser.add_argument("--mode", default="mock", choices=["paper", "mock", "real"])
    parser.add_argument("--strategy", default="morning_0930", choices=["morning_0930", "afternoon_1500"])
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--apply", action="store_true", help="로컬 포지션 변경 적용 (close-missing / purge-missing 활성화)")
    parser.add_argument("--close-missing", dest="close_missing", action="store_true",
                        help="KIS에 없지만 로컬 OPEN인 종목을 CLOSED 처리 (--apply 필요)")
    parser.add_argument("--purge-missing", dest="purge_missing", action="store_true",
                        help="KIS에 없는 로컬 포지션 완전 삭제 (--apply 필요, --close-missing 대체)")
    args = parser.parse_args()
    result = sync_broker_positions(
        mode=args.mode,
        strategy=args.strategy,
        config_path=args.config,
        apply=args.apply,
        close_missing=args.close_missing,
        purge_missing=args.purge_missing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
