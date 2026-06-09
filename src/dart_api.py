"""Small DART Open API client used for disclosure risk checks."""

import io
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree

import pandas as pd
import requests
from dotenv import load_dotenv

from utils import ensure_dir, setup_logger

load_dotenv()
logger = setup_logger(__name__, "logs/dart_api.log")

BASE_URL = "https://opendart.fss.or.kr/api"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORP_CACHE = PROJECT_ROOT / "data" / "dart_corp_codes.csv"


def get_dart_api_key() -> str:
    return os.environ.get("DART_API_KEY", "").strip()


def has_dart_api_key() -> bool:
    return bool(get_dart_api_key())


class DARTApiClient:
    def __init__(self, api_key: Optional[str] = None, timeout: int = 15) -> None:
        self.api_key = (api_key or get_dart_api_key()).strip()
        self.timeout = timeout

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def load_corp_codes(self, refresh: bool = False) -> pd.DataFrame:
        ensure_dir(str(CORP_CACHE.parent))
        if CORP_CACHE.exists() and not refresh:
            return pd.read_csv(CORP_CACHE, dtype=str).fillna("")
        if not self.api_key:
            return pd.DataFrame(columns=["corp_code", "corp_name", "stock_code", "modify_date"])

        resp = requests.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": self.api_key}, timeout=self.timeout)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            root = ElementTree.fromstring(zf.read(xml_name))
        rows = []
        for item in root.findall("list"):
            rows.append({
                "corp_code": (item.findtext("corp_code") or "").strip(),
                "corp_name": (item.findtext("corp_name") or "").strip(),
                "stock_code": (item.findtext("stock_code") or "").strip(),
                "modify_date": (item.findtext("modify_date") or "").strip(),
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df.to_csv(CORP_CACHE, index=False, encoding="utf-8-sig")
        return df

    def get_corp_code_by_stock_code(self, stock_code: str) -> str:
        code = str(stock_code).replace(".0", "").zfill(6)
        df = self.load_corp_codes()
        if df.empty:
            return ""
        hit = df[df["stock_code"].astype(str).str.zfill(6) == code]
        if hit.empty:
            return ""
        return str(hit.iloc[0]["corp_code"])

    def list_disclosures(
        self,
        stock_code: str,
        lookback_days: int = 3,
        bgn_de: Optional[str] = None,
        end_de: Optional[str] = None,
    ) -> List[Dict]:
        if not self.api_key:
            return []
        corp_code = self.get_corp_code_by_stock_code(stock_code)
        if not corp_code:
            return []
        end = datetime.now()
        start = end - timedelta(days=int(lookback_days))
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bgn_de": bgn_de or start.strftime("%Y%m%d"),
            "end_de": end_de or end.strftime("%Y%m%d"),
            "page_no": "1",
            "page_count": "100",
        }
        resp = requests.get(f"{BASE_URL}/list.json", params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") not in ("000", "013"):
            logger.warning("DART list status=%s message=%s stock_code=%s", data.get("status"), data.get("message"), stock_code)
            return []
        rows = []
        for row in data.get("list", []) or []:
            rows.append({
                "stock_code": str(stock_code).zfill(6),
                "corp_code": corp_code,
                "corp_name": row.get("corp_name", ""),
                "report_nm": row.get("report_nm", ""),
                "rcept_no": row.get("rcept_no", ""),
                "rcept_dt": row.get("rcept_dt", ""),
                "flr_nm": row.get("flr_nm", ""),
                "rm": row.get("rm", ""),
            })
        return rows
