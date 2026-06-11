"""REAL trade daily confirmation management."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION_FILE = PROJECT_ROOT / "data" / "real_trade_confirmation.json"

_REQUIRED_FIELDS = [
    "confirm_live_trade",
    "allow_real_bulk_order",
    "understand_real_money",
    "understand_no_profit_guarantee",
    "understand_order_may_be_unfilled",
    "understand_user_responsibility",
]


def load_confirmation() -> Dict[str, Any]:
    """Load daily confirmation. Returns empty dict if not confirmed today."""
    if not CONFIRMATION_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIRMATION_FILE.read_text(encoding="utf-8"))
        return data
    except Exception:
        return {}


def is_confirmed_today() -> bool:
    """Check if user confirmed REAL trade conditions today."""
    data = load_confirmation()
    if not data.get("real_trade_confirmed"):
        return False
    today = date.today().strftime("%Y%m%d")
    return data.get("confirmation_date") == today and all(data.get(f, False) for f in _REQUIRED_FIELDS)


def save_confirmation(ack_fields: Dict[str, bool]) -> Dict[str, Any]:
    """Save daily confirmation."""
    today = date.today().strftime("%Y%m%d")
    data = {
        "real_trade_confirmed": all(ack_fields.values()),
        "confirmed_at": __import__("datetime").datetime.now().isoformat(),
        "confirmation_date": today,
        **{f: bool(ack_fields.get(f, False)) for f in _REQUIRED_FIELDS},
    }
    CONFIRMATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIRMATION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def clear_confirmation() -> None:
    """Clear the confirmation (force re-confirm next time)."""
    if CONFIRMATION_FILE.exists():
        CONFIRMATION_FILE.unlink()
