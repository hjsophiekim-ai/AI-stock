"""계좌 동기화 진단 스크립트.

KIS 계좌(MOCK/REAL) 보유 종목과 로컬 positions.json의 불일치를 진단합니다.

사용법:
    python diagnose_account_sync.py
    python diagnose_account_sync.py --sync      # 불일치 자동 동기화
    python diagnose_account_sync.py --reset     # positions.json 초기화 (KIS 계좌 기준 재생성)
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from utils import load_config, setup_logger
from kis_api import KISApiClient
from position_manager import PositionManager

logger = setup_logger(__name__, "logs/diagnose_account_sync.log")

CONFIG_PATH = str(PROJECT_ROOT / "config.yaml")


def _get_broker_positions(api: KISApiClient):
    """KIS 계좌에서 보유 종목 조회."""
    import pandas as pd
    df = api.get_positions()
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def _get_local_positions(pm: PositionManager):
    """로컬 positions.json 보유 종목 조회."""
    return pm.get_all_positions()


def diagnose(sync: bool = False, reset: bool = False) -> dict:
    """계좌 동기화 진단 실행.

    Args:
        sync: True이면 KIS→로컬 동기화 실행
        reset: True이면 positions.json을 KIS 계좌 기준으로 완전 재생성

    Returns:
        진단 결과 dict
    """
    cfg = load_config(CONFIG_PATH)
    api = KISApiClient(CONFIG_PATH)
    pm = PositionManager(CONFIG_PATH)

    broker_df = _get_broker_positions(api)
    local_positions = _get_local_positions(pm)

    broker_codes = set(broker_df["stock_code"].tolist()) if not broker_df.empty else set()
    local_codes = set(local_positions.keys())

    only_broker = broker_codes - local_codes
    only_local = local_codes - broker_codes
    both = broker_codes & local_codes

    print("\n" + "=" * 60)
    print("계좌 동기화 진단 보고서")
    print("=" * 60)
    print(f"  KIS 계좌 보유 종목 수 : {len(broker_codes)}개  {sorted(broker_codes)}")
    print(f"  로컬 JSON 보유 종목 수: {len(local_codes)}개  {sorted(local_codes)}")
    print(f"  공통 종목             : {len(both)}개  {sorted(both)}")
    print(f"  KIS만 있는 종목       : {len(only_broker)}개  {sorted(only_broker)}")
    print(f"  로컬만 있는 종목      : {len(only_local)}개  {sorted(only_local)}")

    if only_broker:
        print("\n[경고] KIS 계좌에만 있는 종목 (로컬 미등록):")
        for code in sorted(only_broker):
            row = broker_df[broker_df["stock_code"] == code].iloc[0]
            print(f"  {code}  {row.get('stock_name','?')}  {int(row.get('quantity',0))}주  평균단가 {float(row.get('avg_price',0)):,.0f}원")

    if only_local:
        print("\n[경고] 로컬에만 있는 종목 (KIS 계좌에 없음):")
        for code in sorted(only_local):
            pos = local_positions[code]
            print(f"  {code}  {pos.stock_name}  {pos.quantity}주  매수가 {pos.entry_price:,.0f}원")

    result = {
        "broker_codes": sorted(broker_codes),
        "local_codes": sorted(local_codes),
        "only_broker": sorted(only_broker),
        "only_local": sorted(only_local),
        "match": not only_broker and not only_local,
    }

    if sync and not reset:
        if broker_df.empty:
            print("\n[동기화] KIS 계좌에 보유 종목이 없습니다. 동기화를 건너뜁니다.")
        else:
            print(f"\n[동기화] KIS→로컬 동기화 실행 중...")
            pm.sync_positions_from_broker(broker_df)
            print(f"[동기화] 완료: {len(only_broker)}개 추가됨")
        result["synced"] = True

    if reset:
        print("\n[리셋] positions.json을 KIS 계좌 기준으로 재생성합니다.")
        pos_file = PROJECT_ROOT / "data" / "positions.json"
        # 기존 백업
        if pos_file.exists():
            backup = pos_file.with_suffix(".json.bak_reset")
            pos_file.rename(backup)
            print(f"  기존 파일 백업: {backup}")
        # 새 positions.json 생성
        new_data = {}
        for _, row in broker_df.iterrows() if not broker_df.empty else iter([]):
            from datetime import datetime
            code = row["stock_code"]
            pm2 = PositionManager(CONFIG_PATH)
            pm2.update_position_after_buy(
                stock_code=code,
                stock_name=str(row.get("stock_name", "")),
                quantity=int(row["quantity"]),
                entry_price=float(row["avg_price"]),
                entry_time=datetime.now(),
            )
        pm2.save_local_positions() if broker_df.empty is False else None
        print(f"  {len(broker_df)}개 종목으로 재생성 완료")
        result["reset"] = True

    print("\n" + ("일치" if result["match"] else "불일치") + " — 진단 완료")
    return result


def main():
    parser = argparse.ArgumentParser(description="KIS 계좌 ↔ 로컬 positions.json 동기화 진단")
    parser.add_argument("--sync", action="store_true", help="KIS→로컬 자동 동기화")
    parser.add_argument("--reset", action="store_true", help="positions.json을 KIS 계좌 기준으로 완전 재생성")
    args = parser.parse_args()

    result = diagnose(sync=args.sync, reset=args.reset)
    print("\nJSON 요약:")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
