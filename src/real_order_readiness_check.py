"""Check readiness for REAL single-stock test orders without placing orders."""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))

from kis_api import KISApiClient
from safety_gate import SafetyGate
from utils import ensure_dir, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "*" * max(0, len(value) - 8) + value[-4:]


def _real_api_config_path(config_path: str) -> str:
    cfg = load_config(config_path)
    cfg["live_trade"] = True
    cfg["paper_trade"] = False
    cfg.setdefault("kis", {})["use_mock"] = False
    cfg["kis"]["app_key_env"] = "KIS_REAL_APP_KEY"
    cfg["kis"]["app_secret_env"] = "KIS_REAL_APP_SECRET"
    cfg["kis"]["account_no_env"] = "KIS_ACCOUNT_NO"
    cfg["kis"]["token_cache_file"] = "data/real_token_cache.json"
    cfg.setdefault("safety", {})["confirm_live_trade"] = True
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml", encoding="utf-8")
    yaml.safe_dump(cfg, tmp, allow_unicode=True, sort_keys=False)
    tmp.close()
    return tmp.name


def run_readiness_check(config_path: str = "config.yaml", call_real_api: bool = True) -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    cfg = load_config(config_path)
    gate = SafetyGate(config_path, runtime_mode="real")
    conditions = gate.get_real_order_conditions()

    real_key = os.environ.get("KIS_REAL_APP_KEY") or os.environ.get("KIS_APP_KEY", "")
    real_secret = os.environ.get("KIS_REAL_APP_SECRET", "")
    real_account = os.environ.get("KIS_ACCOUNT_NO", "")
    checks = {
        "real_api_key": bool(real_key and real_secret and real_account),
        "real_api_key_masked": _mask(real_key),
        "real_account_masked": _mask(real_account),
        "real_token": False,
        "real_balance": False,
        "orderable_cash": False,
        "orderable_cash_amount": 0,
        "conditions": conditions,
        "missing_conditions": [k for k, v in conditions.items() if not v],
    }

    if call_real_api and checks["real_api_key"]:
        tmp_path = _real_api_config_path(config_path)
        try:
            api = KISApiClient(tmp_path, gate=SafetyGate(tmp_path, runtime_mode="real"))
            token = api.auth.get_access_token()
            checks["real_token"] = bool(token)
            bal = api.get_account_balance()
            checks["real_balance"] = isinstance(bal, dict) and bool(bal)
            cash = api.get_orderable_cash()
            checks["orderable_cash"] = cash >= 0
            checks["orderable_cash_amount"] = int(cash)
        except Exception as ex:
            checks["real_api_error"] = str(ex)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    ready = (
        checks["real_api_key"]
        and checks["real_token"]
        and checks["real_balance"]
        and checks["orderable_cash"]
        and all(conditions.values())
    )
    checks["verdict"] = "READY_FOR_REAL_SINGLE_TEST" if ready else "NOT_READY"
    return checks


def save_report(result: dict) -> dict:
    ensure_dir(str(PROJECT_ROOT / "reports"))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = PROJECT_ROOT / "reports" / f"real_order_readiness_{ts}.txt"
    json_path = PROJECT_ROOT / "reports" / f"real_order_readiness_{ts}.json"
    lines = [
        "=" * 60,
        "  실전 주문 준비상태 점검",
        "=" * 60,
        f"REAL API 키: {'OK' if result.get('real_api_key') else 'FAIL'}",
        f"REAL 토큰: {'OK' if result.get('real_token') else 'FAIL'}",
        f"REAL 계좌조회: {'OK' if result.get('real_balance') else 'FAIL'}",
        f"주문가능금액: {'OK' if result.get('orderable_cash') else 'FAIL'} {result.get('orderable_cash_amount', 0):,}원",
    ]
    for key, value in result.get("conditions", {}).items():
        lines.append(f"{key}: {'OK' if value else 'FAIL'}")
    lines.append(f"최종판정: {result.get('verdict')}")
    if result.get("missing_conditions"):
        lines.append("부족한 조건: " + ", ".join(result["missing_conditions"]))
    if result.get("real_api_error"):
        lines.append("REAL API 오류: " + str(result["real_api_error"]))
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"txt_path": str(txt_path), "json_path": str(json_path)}


def main() -> None:
    result = run_readiness_check()
    paths = save_report(result)
    print(Path(paths["txt_path"]).read_text(encoding="utf-8"))
    print(f"\n보고서: {paths['txt_path']}")
    print(f"JSON: {paths['json_path']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
