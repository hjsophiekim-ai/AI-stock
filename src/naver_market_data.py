"""Collect and normalize Naver Finance market tables.

Naver is a secondary public-market source. It is used for market/sector context,
not as the final orderable realtime quote source.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data" / "external"
REPORT_DIR = PROJECT_ROOT / "reports" / "market"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

WORLD_SYMBOL_HINTS = [
    ("다우 산업", "^DJI", "Dow Jones Industrial Average", "index", "industrial"),
    ("다우 운송", "^DJT", "Dow Jones Transportation Average", "index", "industrial"),
    ("나스닥", "^IXIC", "NASDAQ Composite", "index", "technology"),
    ("NASDAQ", "^IXIC", "NASDAQ Composite", "index", "technology"),
    ("S&P", "^GSPC", "S&P 500", "index", "risk_on"),
    ("다우", "^DJI", "Dow Jones", "index", "industrial"),
    ("필라델피아", "^SOX", "Philadelphia Semiconductor Index", "index", "semiconductor"),
    ("반도체", "^SOX", "Philadelphia Semiconductor Index", "index", "semiconductor"),
    ("러셀", "^RUT", "Russell 2000", "index", "smallcap"),
    ("VIX", "^VIX", "CBOE VIX", "macro", "risk_off"),
    ("엔비디아", "NVDA", "NVIDIA", "stock", "semiconductor"),
    ("NVIDIA", "NVDA", "NVIDIA", "stock", "semiconductor"),
    ("AMD", "AMD", "AMD", "stock", "semiconductor"),
    ("마이크론", "MU", "Micron", "stock", "semiconductor"),
    ("MICRON", "MU", "Micron", "stock", "semiconductor"),
    ("브로드컴", "AVGO", "Broadcom", "stock", "semiconductor"),
    ("BROADCOM", "AVGO", "Broadcom", "stock", "semiconductor"),
    ("테슬라", "TSLA", "Tesla", "stock", "battery_ev"),
    ("TESLA", "TSLA", "Tesla", "stock", "battery_ev"),
    ("팔란티어", "PLTR", "Palantir", "stock", "ai_robotics"),
    ("PALANTIR", "PLTR", "Palantir", "stock", "ai_robotics"),
    ("애플", "AAPL", "Apple", "stock", "technology"),
    ("APPLE", "AAPL", "Apple", "stock", "technology"),
    ("마이크로소프트", "MSFT", "Microsoft", "stock", "technology"),
    ("MICROSOFT", "MSFT", "Microsoft", "stock", "technology"),
]

NAVER_WORLD_SYMBOL_MAP = {
    "NAS@IXIC": ("^IXIC", "NASDAQ Composite", "index", "technology"),
    "NAS@NDX": ("QQQ", "NASDAQ 100", "index", "technology"),
    "SPI@SPX": ("^GSPC", "S&P 500", "index", "risk_on"),
    "DJI@DJI": ("^DJI", "Dow Jones Industrial Average", "index", "industrial"),
    "DJI@DJT": ("^DJT", "Dow Jones Transportation Average", "index", "industrial"),
    "NAS@SOX": ("^SOX", "Philadelphia Semiconductor Index", "index", "semiconductor"),
    "RUI@RUT": ("^RUT", "Russell 2000", "index", "smallcap"),
}


def _load_cfg() -> dict[str, Any]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return ((yaml.safe_load(f) or {}).get("us_market_filter", {}) or {}).get("naver_finance", {}) or {}
    except Exception:
        return {}


def _fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "cp949"
    return resp.text


def _read_tables(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(StringIO(html), flavor="lxml")
    except Exception:
        return []


def _parse_pct(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _parse_number(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _classify_theme(name: str) -> tuple[str, str, str, str]:
    upper = name.upper()
    for hint, symbol, canonical, category, theme in WORLD_SYMBOL_HINTS:
        if hint.upper() in upper:
            return symbol, canonical, category, theme
    return name[:20], name, "naver_world", "unknown"


def _rows_from_world_script(html: str, source_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for var_name in ("americaData", "europeData", "asiaData"):
        match = re.search(rf"var\s+{var_name}\s*=\s*jindo\.\$H\((\{{.*?\}})\);", html, re.S)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except Exception:
            continue
        for raw_symbol, item in payload.items():
            if not isinstance(item, dict):
                continue
            symbol, canonical, category, theme = NAVER_WORLD_SYMBOL_MAP.get(
                str(raw_symbol),
                _classify_theme(str(item.get("knam") or item.get("enam") or raw_symbol)),
            )
            if theme == "unknown":
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": canonical,
                    "raw_name": str(item.get("knam") or item.get("enam") or raw_symbol),
                    "category": category,
                    "theme": theme,
                    "close": _parse_number(item.get("last")),
                    "prev_close": None,
                    "change_rate": _parse_pct(item.get("rate")) or 0.0,
                    "volume": _parse_number(item.get("gvol")),
                    "trading_value_proxy": None,
                    "after_hours_change_rate": None,
                    "premarket_change_rate": None,
                    "data_source": f"naver_finance_world_{var_name}",
                    "source_url": source_url,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "error": "",
                }
            )
    return rows


def collect_world_market(date_str: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    today = date_str or datetime.now().strftime("%Y%m%d")
    cfg = _load_cfg()
    url = str(cfg.get("world_url") or "https://finance.naver.com/world/")
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        html = _fetch_html(url)
        rows.extend(_rows_from_world_script(html, url))
        for table in _read_tables(html):
            columns = [str(c).strip() for c in table.columns]
            if not any(c in columns for c in ("지수명", "종목명", "구분", "업체명")):
                continue
            name_col = next((c for c in ("지수명", "종목명", "구분", "업체명") if c in columns), None)
            pct_col = next((c for c in ("등락률", "전일비", "전일대비") if c in columns), None)
            price_col = next((c for c in ("현재가", "종가") if c in columns), None)
            if not name_col:
                continue
            for _, row in table.iterrows():
                raw_name = row.get(name_col)
                if pd.isna(raw_name):
                    continue
                name = re.sub(r"^\d+\.", "", str(raw_name)).strip()
                if not name:
                    continue
                change_rate = _parse_pct(row.get(pct_col)) if pct_col else None
                close = _parse_number(row.get(price_col)) if price_col else None
                symbol, canonical, category, theme = _classify_theme(name)
                if theme == "unknown":
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "name": canonical,
                        "raw_name": name,
                        "category": category,
                        "theme": theme,
                        "close": close,
                        "prev_close": None,
                        "change_rate": change_rate if change_rate is not None else 0.0,
                        "volume": None,
                        "trading_value_proxy": None,
                        "after_hours_change_rate": None,
                        "premarket_change_rate": None,
                        "data_source": "naver_finance_world",
                        "source_url": url,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "error": "",
                    }
                )
    except Exception as exc:
        errors.append(str(exc))

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["symbol", "raw_name"], keep="first")
    summary = {
        "success": True,
        "date": today,
        "source_url": url,
        "row_count": int(len(df)),
        "valid_count": int(len(df[df["data_source"].ne("fetch_failed")])) if not df.empty else 0,
        "errors": errors,
    }
    return df, summary


def collect_domestic_leaders(date_str: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    today = date_str or datetime.now().strftime("%Y%m%d")
    cfg = _load_cfg()
    urls = cfg.get("domestic_leader_urls") or [
        "https://finance.naver.com/sise/sise_quant.naver?sosok=0",
        "https://finance.naver.com/sise/sise_quant.naver?sosok=1",
    ]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for url in urls:
        market = "KOSDAQ" if "sosok=1" in str(url) else "KOSPI"
        try:
            html = _fetch_html(str(url))
            for table in _read_tables(html):
                columns = [str(c).strip() for c in table.columns]
                if "종목명" not in columns or "현재가" not in columns:
                    continue
                for _, row in table.iterrows():
                    name = str(row.get("종목명", "")).strip()
                    rank = _parse_number(row.get("N"))
                    if not name or name.lower() == "nan" or rank is None:
                        continue
                    change_rate = _parse_pct(row.get("등락률"))
                    trading_value_mil = _parse_number(row.get("거래대금"))
                    rows.append(
                        {
                            "stock_name": name,
                            "naver_domestic_rank": int(rank),
                            "naver_domestic_market": market,
                            "naver_current_price": _parse_number(row.get("현재가")),
                            "naver_change_rate": change_rate,
                            "naver_volume": _parse_number(row.get("거래량")),
                            "naver_trading_value_krw": (
                                trading_value_mil * 1_000_000 if trading_value_mil is not None else None
                            ),
                            "naver_leader_source": "naver_finance_sise_quant",
                            "naver_source_url": str(url),
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
        except Exception as exc:
            errors.append({"url": str(url), "error": str(exc)})

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["naver_domestic_rank"], kind="stable").drop_duplicates(
            subset=["stock_name"], keep="first"
        )
        rank_score = (1.0 - ((pd.to_numeric(df["naver_domestic_rank"], errors="coerce").fillna(999) - 1) / 200)).clip(0, 1)
        change_score = ((pd.to_numeric(df["naver_change_rate"], errors="coerce").fillna(0).clip(-3, 7) + 3) / 10).clip(0, 1)
        value_score = (
            pd.to_numeric(df["naver_trading_value_krw"], errors="coerce").fillna(0)
            .rank(pct=True, ascending=True)
            .fillna(0.5)
        )
        df["naver_domestic_leader_score"] = (rank_score * 0.45 + change_score * 0.35 + value_score * 0.20).clip(0, 1)
    summary = {
        "success": True,
        "date": today,
        "urls": [str(u) for u in urls],
        "row_count": int(len(df)),
        "errors": errors,
    }
    return df, summary


def collect_naver_market_data(date_str: str | None = None) -> dict[str, Any]:
    today = date_str or datetime.now().strftime("%Y%m%d")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    world_df, world_summary = collect_world_market(today)
    domestic_df, domestic_summary = collect_domestic_leaders(today)

    world_path = DATA_DIR / f"naver_world_{today}.csv"
    domestic_path = DATA_DIR / f"naver_domestic_leaders_{today}.csv"
    summary_path = REPORT_DIR / f"naver_market_summary_{today}.json"
    world_df.to_csv(world_path, index=False, encoding="utf-8-sig")
    domestic_df.to_csv(domestic_path, index=False, encoding="utf-8-sig")
    summary = {
        "success": True,
        "date": today,
        "world_output_file": str(world_path),
        "domestic_output_file": str(domestic_path),
        "world": world_summary,
        "domestic": domestic_summary,
        "created_at": datetime.now().isoformat(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def get_naver_item_safety(code: str) -> dict[str, Any]:
    """Return safety flags from a Naver Finance item page.

    This is a secondary safety check. Network or parsing failures are explicit
    so callers can decide whether to block or keep checking other sources.
    """
    norm_code = str(code).strip().zfill(6)
    url = f"https://finance.naver.com/item/main.naver?code={norm_code}"
    flags = {
        "naver_item_checked": False,
        "naver_item_url": url,
        "naver_item_name": "",
        "naver_item_current_price": None,
        "naver_item_is_trading_halted": False,
        "naver_item_is_management_issue": False,
        "naver_item_is_investment_warning": False,
        "naver_item_fail_reason": "",
    }
    try:
        html = _fetch_html(url)
    except Exception as exc:
        flags["naver_item_fail_reason"] = f"fetch_failed: {exc}"
        return flags

    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if title:
        flags["naver_item_name"] = re.sub(r"\s+", " ", title.group(1)).strip()
    price = re.search(r'<p class="no_today">.*?<span class="blind">([^<]+)</span>', html, re.S | re.I)
    if price:
        flags["naver_item_current_price"] = _parse_number(price.group(1))
    flags["naver_item_is_trading_halted"] = any(
        word in html for word in ("거래정지", "매매정지", "정리매매", "상장폐지")
    )
    flags["naver_item_is_management_issue"] = any(
        word in html for word in ("관리종목", "불성실공시")
    )
    flags["naver_item_is_investment_warning"] = any(
        word in html for word in ("투자주의", "투자경고", "투자위험", "단기과열")
    )
    fail_reasons = []
    if flags["naver_item_is_trading_halted"]:
        fail_reasons.append("naver_item_trading_halted")
    if flags["naver_item_is_management_issue"]:
        fail_reasons.append("naver_item_management_issue")
    if flags["naver_item_is_investment_warning"]:
        fail_reasons.append("naver_item_investment_warning")
    flags["naver_item_checked"] = True
    flags["naver_item_fail_reason"] = ";".join(fail_reasons)
    return flags
