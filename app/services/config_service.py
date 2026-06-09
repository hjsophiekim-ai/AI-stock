"""config.yaml 읽기/쓰기 서비스."""

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config() -> Dict:
    """config.yaml 로드."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(cfg: Dict) -> None:
    """config.yaml 저장."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def nested_update(cfg: Dict, path: str, value: Any) -> Dict:
    """점(.)으로 구분된 경로에 값 설정. 예: 'kis.use_mock' = False."""
    keys = path.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value
    return cfg


def get_trade_mode(cfg: Optional[Dict] = None) -> str:
    """현재 설정에서 거래 모드 계산 (PAPER/MOCK/REAL)."""
    if cfg is None:
        cfg = load_config()
    live = bool(cfg.get("live_trade", False))
    use_mock = bool(cfg.get("kis", {}).get("use_mock", True))
    confirm = bool(cfg.get("safety", {}).get("confirm_live_trade", False))
    if not live:
        return "PAPER"
    if use_mock:
        return "MOCK"
    if confirm:
        return "REAL"
    return "MOCK"


def get_safety_status(cfg: Optional[Dict] = None) -> Dict[str, Any]:
    """안전장치 상태 딕셔너리 반환."""
    if cfg is None:
        cfg = load_config()
    mode = get_trade_mode(cfg)
    return {
        "mode": mode,
        "live_trade": bool(cfg.get("live_trade", False)),
        "paper_trade": bool(cfg.get("paper_trade", True)),
        "use_mock": bool(cfg.get("kis", {}).get("use_mock", True)),
        "confirm_live_trade": bool(cfg.get("safety", {}).get("confirm_live_trade", False)),
        "allow_real_test_order": bool(cfg.get("safety", {}).get("allow_real_test_order", False)),
        "force_trade_enabled": bool(cfg.get("force_trade", {}).get("enabled", False)),
        "force_trade_allow_real": bool(cfg.get("force_trade", {}).get("allow_real_test_order", False)),
    }


def can_enable_real_mode(cfg: Optional[Dict] = None) -> Dict[str, bool]:
    """REAL 모드 전환 가능 조건 체크."""
    if cfg is None:
        cfg = load_config()
    return {
        "live_trade=true": bool(cfg.get("live_trade", False)),
        "use_mock=false": not bool(cfg.get("kis", {}).get("use_mock", True)),
        "confirm_live_trade=true": bool(cfg.get("safety", {}).get("confirm_live_trade", False)),
    }


def enable_paper_mode() -> None:
    """PAPER 모드로 전환."""
    cfg = load_config()
    cfg["live_trade"] = False
    cfg["paper_trade"] = True
    cfg.setdefault("kis", {})["use_mock"] = True
    cfg.setdefault("safety", {})["confirm_live_trade"] = False
    save_config(cfg)


def enable_mock_mode() -> None:
    """MOCK 모드로 전환 (live_trade=true, use_mock=true)."""
    cfg = load_config()
    cfg["live_trade"] = True
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = True
    cfg.setdefault("safety", {})["confirm_live_trade"] = False
    save_config(cfg)


def enable_real_mode() -> None:
    """REAL 모드 전환. 3중 안전장치 모두 활성화."""
    cfg = load_config()
    cfg["live_trade"] = True
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = False
    cfg.setdefault("safety", {})["confirm_live_trade"] = True
    save_config(cfg)


def enable_real_single_test_mode() -> None:
    cfg = load_config()
    cfg["live_trade"] = True
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = False
    safety = cfg.setdefault("safety", {})
    safety["confirm_live_trade"] = True
    safety["allow_real_test_order"] = True
    safety["require_manual_live_trade_confirmation"] = True
    safety["max_real_test_order_amount"] = int(safety.get("max_real_test_order_amount", 10_000))
    safety["max_real_test_quantity"] = int(safety.get("max_real_test_quantity", 1))
    safety["block_real_bulk_order"] = True
    safety["block_real_auto_order_by_default"] = True
    ft = cfg.setdefault("force_trade", {})
    ft["allow_real_test_order"] = True
    ft["allow_real_bulk_order"] = False
    real_trade = cfg.setdefault("real_trade", {})
    real_trade["enabled"] = True
    real_trade["allow_single_stock_test"] = True
    real_trade["allow_bulk_buy"] = False
    real_trade["require_final_checkbox"] = True
    real_trade["require_order_preview"] = True
    real_trade["max_single_test_amount"] = int(real_trade.get("max_single_test_amount", 10_000))
    real_trade["max_single_test_quantity"] = int(real_trade.get("max_single_test_quantity", 1))
    real_trade["allowed_order_type"] = "limit"
    real_trade["allow_market_order"] = False
    real_trade["allow_after_hours_real_order"] = False
    real_trade["real_order_confirmed_by_user"] = True
    save_config(cfg)


def disable_real_ordering() -> None:
    cfg = load_config()
    cfg["live_trade"] = False
    cfg["paper_trade"] = True
    cfg.setdefault("kis", {})["use_mock"] = True
    cfg.setdefault("safety", {})["confirm_live_trade"] = False
    cfg.setdefault("safety", {})["allow_real_test_order"] = False
    cfg.setdefault("force_trade", {})["allow_real_test_order"] = False
    cfg.setdefault("force_trade", {})["allow_real_bulk_order"] = False
    real_trade = cfg.setdefault("real_trade", {})
    real_trade["enabled"] = False
    real_trade["allow_single_stock_test"] = False
    real_trade["allow_bulk_buy"] = False
    real_trade["real_order_confirmed_by_user"] = False
    save_config(cfg)


def get_real_order_conditions() -> Dict[str, bool]:
    cfg = load_config()
    return {
        "live_trade=true": bool(cfg.get("live_trade", False)),
        "paper_trade=false": not bool(cfg.get("paper_trade", True)),
        "kis.use_mock=false": not bool(cfg.get("kis", {}).get("use_mock", True)),
        "safety.confirm_live_trade=true": bool(cfg.get("safety", {}).get("confirm_live_trade", False)),
        "safety.allow_real_test_order=true": bool(cfg.get("safety", {}).get("allow_real_test_order", False)),
        "force_trade.allow_real_test_order=true": bool(cfg.get("force_trade", {}).get("allow_real_test_order", False)),
        "real_trade.enabled=true": bool(cfg.get("real_trade", {}).get("enabled", False)),
        "real_trade.allow_single_stock_test=true": bool(cfg.get("real_trade", {}).get("allow_single_stock_test", False)),
        "real_trade.real_order_confirmed_by_user=true": bool(cfg.get("real_trade", {}).get("real_order_confirmed_by_user", False)),
    }
