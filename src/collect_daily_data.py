"""최근 N년치 전 종목 일봉 데이터 수집.

pykrx 우선 → FinanceDataReader → 샘플 데이터(SAMPLE_DATA) 순서로 fallback.
실패한 종목은 logs/data_collect_errors.log에 기록하며 전체 프로세스는 중단되지 않습니다.

실행:
    python src/collect_daily_data.py --years 3 --limit 100
    python src/collect_daily_data.py --years 3 --all
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(__file__))
from utils import (
    calc_trading_days_ago,
    ensure_dir,
    get_today_str,
    is_etf_etn,
    is_preferred_stock,
    is_spac,
    load_config,
    save_csv,
    setup_logger,
)

logger = setup_logger(__name__, "logs/collect_daily_data.log")
cfg = load_config("config.yaml")

# 샘플 데이터 fallback용 주요 종목 (pykrx/FDR 모두 실패 시)
SAMPLE_STOCKS = [
    ("005930", "삼성전자", "KOSPI"),
    ("000660", "SK하이닉스", "KOSPI"),
    ("035420", "NAVER", "KOSPI"),
    ("035720", "카카오", "KOSPI"),
    ("005380", "현대차", "KOSPI"),
    ("051910", "LG화학", "KOSPI"),
    ("006400", "삼성SDI", "KOSPI"),
    ("003670", "포스코퓨처엠", "KOSPI"),
    ("068270", "셀트리온", "KOSPI"),
    ("105560", "KB금융", "KOSPI"),
    ("055550", "신한지주", "KOSPI"),
    ("000270", "기아", "KOSPI"),
    ("003550", "LG", "KOSPI"),
    ("096770", "SK이노베이션", "KOSPI"),
    ("010130", "고려아연", "KOSPI"),
    ("028260", "삼성물산", "KOSPI"),
    ("034730", "SK", "KOSPI"),
    ("032830", "삼성생명", "KOSPI"),
    ("018260", "삼성에스디에스", "KOSPI"),
    ("030200", "KT", "KOSPI"),
    ("086790", "하나금융지주", "KOSPI"),
    ("011200", "HMM", "KOSPI"),
    ("023530", "롯데쇼핑", "KOSPI"),
    ("047050", "포스코인터내셔널", "KOSPI"),
    ("015760", "한국전력", "KOSPI"),
    ("042660", "한화오션", "KOSPI"),
    ("009150", "삼성전기", "KOSPI"),
    ("012330", "현대모비스", "KOSPI"),
    ("011790", "SKC", "KOSPI"),
    ("010950", "S-Oil", "KOSPI"),
]


def _pykrx_with_timeout(func, *args, timeout_sec: int = 20):
    """pykrx 함수를 별도 스레드에서 실행하고 timeout_sec 초 내 완료 안 되면 None 반환."""
    import threading
    result = [None]
    exc = [None]

    def _run():
        try:
            result[0] = func(*args)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    if t.is_alive():
        raise TimeoutError(f"pykrx 호출 {timeout_sec}초 초과 — Render/네트워크 지연 의심")
    if exc[0]:
        raise exc[0]
    return result[0]


def get_ticker_list_pykrx(market: str = "ALL") -> Optional[pd.DataFrame]:
    """pykrx로 종목 리스트 수집 (스레드 타임아웃 적용)."""
    try:
        from pykrx import stock
        today = get_today_str("%Y%m%d")
        rows = []
        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
        for mkt in markets:
            # 전체 종목 코드 리스트 — 타임아웃 30초
            tickers = _pykrx_with_timeout(
                stock.get_market_ticker_list, today, market=mkt, timeout_sec=30
            )
            if not tickers:
                continue

            # 종목명 일괄 조회: stock.get_market_ticker_name 은 건당 HTTP 요청 발생
            # → 배치로 묶어 전체에 20초 타임아웃 적용
            def _fetch_names(tickers_inner):
                return {t: stock.get_market_ticker_name(t) for t in tickers_inner}

            try:
                name_map = _pykrx_with_timeout(_fetch_names, tickers, timeout_sec=120)
            except TimeoutError:
                logger.warning(f"[pykrx] {mkt} 종목명 일괄 조회 타임아웃 — 코드만 사용")
                name_map = {t: t for t in tickers}

            for t in tickers:
                rows.append({
                    "stock_code": str(t).zfill(6),
                    "stock_name": name_map.get(t, t),
                    "market": mkt,
                })

        if rows:
            df = pd.DataFrame(rows)
            logger.info(f"[pykrx] 종목 리스트 수집 완료: {len(df)}개")
            return df
    except TimeoutError as te:
        logger.warning(f"pykrx 타임아웃: {te}")
    except Exception as e:
        logger.warning(f"pykrx 종목 리스트 수집 실패: {e}")
    return None


def get_ticker_list_fdr(market: str = "ALL") -> Optional[pd.DataFrame]:
    """FinanceDataReader로 종목 리스트 수집."""
    try:
        import FinanceDataReader as fdr
        markets = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]
        rows = []
        for mkt in markets:
            listing = fdr.StockListing(mkt)
            for _, r in listing.iterrows():
                code = str(r.get("Code", r.get("Symbol", ""))).zfill(6)
                name = str(r.get("Name", r.get("ISU_ABBRV", "")))
                if code and name:
                    rows.append({"stock_code": code, "stock_name": name, "market": mkt})
        if rows:
            df = pd.DataFrame(rows)
            logger.info(f"[FDR] 종목 리스트 수집 완료: {len(df)}개")
            return df
    except Exception as e:
        logger.warning(f"FDR 종목 리스트 수집 실패: {e}")
    return None


def get_ticker_list(market: str = "ALL") -> pd.DataFrame:
    """종목 리스트 수집 (pykrx → FDR → 샘플 순서)."""
    df = get_ticker_list_pykrx(market)
    if df is not None and not df.empty:
        return df

    df = get_ticker_list_fdr(market)
    if df is not None and not df.empty:
        return df

    logger.warning("모든 종목 리스트 수집 실패. 샘플 데이터 사용 [SAMPLE_DATA]")
    print("\n[SAMPLE_DATA] pykrx/FDR 종목 리스트 수집 실패 — 주요 종목 30개로 대체합니다.")
    print("  pip install pykrx FinanceDataReader 후 재실행 권장\n")
    rows = [{"stock_code": c, "stock_name": n, "market": m} for c, n, m in SAMPLE_STOCKS]
    return pd.DataFrame(rows)


def filter_tickers(df: pd.DataFrame) -> pd.DataFrame:
    """위험·비정상 종목 필터링."""
    risk = cfg.get("risk", {})
    original_count = len(df)

    if risk.get("exclude_preferred_stock", True):
        df = df[~df["stock_code"].apply(is_preferred_stock)]

    if risk.get("exclude_spac", True):
        df = df[~df["stock_name"].apply(is_spac)]

    if risk.get("exclude_etf_etn", True):
        df = df[~df["stock_name"].apply(is_etf_etn)]

    filtered_count = len(df)
    logger.info(
        f"필터링: {original_count}개 → {filtered_count}개 "
        f"({original_count - filtered_count}개 제외)"
    )
    return df.reset_index(drop=True)


def fetch_daily_pykrx(stock_code: str, start: str, end: str, timeout_sec: int = 15) -> Optional[pd.DataFrame]:
    """pykrx로 일봉 데이터 조회 (종목별 타임아웃 적용)."""
    def _fetch():
        from pykrx import stock as _stock
        return _stock.get_market_ohlcv_by_date(start, end, stock_code)

    try:
        df = _pykrx_with_timeout(_fetch, timeout_sec=timeout_sec)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        KR_COL_MAP = {
            "날짜": "date",
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "volume",
            "거래대금": "trading_value",
        }
        df = df.rename(columns=KR_COL_MAP)
        if "date" not in df.columns:
            df = df.rename(columns={df.columns[0]: "date"})
        if "trading_value" not in df.columns:
            df["trading_value"] = df["close"] * df["volume"]
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["volume"] > 0]
        return df[["date", "open", "high", "low", "close", "volume", "trading_value"]]
    except TimeoutError as te:
        raise RuntimeError(f"pykrx 타임아웃 [{stock_code}]: {te}")
    except Exception as e:
        raise RuntimeError(f"pykrx 조회 실패: {e}")


def fetch_daily_fdr(stock_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """FinanceDataReader로 일봉 데이터 조회."""
    try:
        import FinanceDataReader as fdr
        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:]}"
        df = fdr.DataReader(stock_code, start_fmt, end_fmt)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        rename_map = {
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if "trading_value" not in df.columns:
            df["trading_value"] = df.get("close", 0) * df.get("volume", 0)
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["volume"] > 0]
        return df[["date", "open", "high", "low", "close", "volume", "trading_value"]]
    except Exception as e:
        raise RuntimeError(f"FDR 조회 실패: {e}")


def make_stock_master(tickers_df: pd.DataFrame) -> pd.DataFrame:
    """stock_master.csv 생성."""
    master = tickers_df.copy()
    master["is_preferred"] = master["stock_code"].apply(is_preferred_stock)
    master["is_spac"] = master["stock_name"].apply(is_spac)
    master["is_etf_etn"] = master["stock_name"].apply(is_etf_etn)
    master["is_halted"] = False   # collect_all_daily_data 완료 후 _update_stock_master_halted로 갱신
    master["is_management"] = False
    return master[["stock_code", "stock_name", "market",
                   "is_preferred", "is_spac", "is_etf_etn",
                   "is_halted", "is_management"]]


def _update_stock_master_halted(
    master_path: str, output_path: str, tickers_df: pd.DataFrame
) -> None:
    """수집된 OHLCV에서 거래정지 종목을 탐지해 stock_master.csv 업데이트.

    탐지 방법: OHLCV에 과거 데이터가 있었으나 최근 3거래일(≈7일) 내 데이터가 없는 종목.
    - 거래대금 미달로 수집 자체가 생략된 종목과 구별하기 위해 과거 수집 이력 기준 사용.
    - tickers_df 전체 기준이 아닌, 실제 OHLCV에 있던 종목만 대상으로 비교.
    """
    if not os.path.exists(output_path) or not os.path.exists(master_path):
        return
    try:
        ohlcv = pd.read_csv(output_path, parse_dates=["date"], dtype={"stock_code": str})
        master = pd.read_csv(master_path, dtype={"stock_code": str})
        if ohlcv.empty:
            return

        latest = ohlcv["date"].max()
        recent_cutoff = latest - pd.Timedelta(days=7)

        # 과거 데이터가 있던 종목(수집 이력 있음) 중 최근 7일 내 미등장 → 거래정지 추정
        ever_collected = set(ohlcv["stock_code"].astype(str).str.zfill(6).unique())
        recent_codes = set(
            ohlcv[ohlcv["date"] >= recent_cutoff]["stock_code"].astype(str).str.zfill(6).unique()
        )
        halted_codes = ever_collected - recent_codes  # 과거엔 있었지만 최근 없음

        master["stock_code"] = master["stock_code"].astype(str).str.zfill(6)
        master["is_halted"] = master["stock_code"].isin(halted_codes)
        save_csv(master, master_path)
        logger.info(
            f"stock_master 거래정지 업데이트: {len(halted_codes)}개 탐지 "
            f"(기준일 {latest.strftime('%Y-%m-%d')}, 과거 수집 이력 있으나 최근 7일 내 거래 없음)"
        )
        if halted_codes:
            halted_names = master.loc[master["is_halted"], "stock_name"].tolist()[:10]
            logger.info(f"거래정지 추정 종목(최대 10개): {halted_names}")
    except Exception as exc:
        logger.warning(f"stock_master 거래정지 업데이트 실패 (무시): {exc}")


def collect_all_daily_data(
    tickers_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    output_path: str,
    error_log_path: str,
    limit: Optional[int] = None,
) -> None:
    """전 종목 일봉 데이터를 수집하여 CSV로 저장."""
    ensure_dir(os.path.dirname(output_path))
    ensure_dir(os.path.dirname(error_log_path) if os.path.dirname(error_log_path) else "logs")

    # limit 적용
    if limit and limit > 0:
        tickers_df = tickers_df.head(limit)
        logger.info(f"--limit {limit} 적용: {len(tickers_df)}개 종목으로 제한")

    all_data: List[pd.DataFrame] = []
    error_tickers: List[str] = []

    # 증분 업데이트: 기존 데이터가 있으면 로드
    if os.path.exists(output_path):
        existing = pd.read_csv(
            output_path,
            parse_dates=["date"],
            dtype={"stock_code": str},
        )
        last_date = existing["date"].max().strftime("%Y%m%d")
        if last_date >= end_date:
            logger.info("이미 최신 데이터가 수집되어 있습니다.")
            return

        # ── 장 마감 전 스킵 로직 ─────────────────────────────────────
        # 장중(09:00~16:30 KST)에는 오늘의 EOD 데이터가 아직 없으므로
        # 2607개 종목을 모두 조회해봤자 시간만 낭비된다.
        # 기존 데이터가 3일 이내(주말 포함)이면 장 마감 후까지 기다린다.
        _now = datetime.now()
        _market_closed = (_now.hour > 16) or (_now.hour == 16 and _now.minute >= 30)
        _last_dt = datetime.strptime(last_date, "%Y%m%d")
        _end_dt = datetime.strptime(end_date, "%Y%m%d")
        _days_old = (_end_dt - _last_dt).days
        if _days_old <= 3 and not _market_closed:
            logger.info(
                f"장 마감 전 — 기존 데이터({last_date}, {_days_old}일 전) 사용. "
                f"장 마감(16:30) 후 재실행하면 오늘({end_date}) 데이터를 수집합니다."
            )
            return
        # ─────────────────────────────────────────────────────────────

        start_date = last_date
        all_data.append(existing)
        logger.info(f"기존 데이터 로드 ({len(existing):,}행), {last_date}부터 업데이트")

    logger.info(
        f"일봉 수집 시작: {len(tickers_df)}개 종목, {start_date} ~ {end_date}"
    )

    for _, row in tqdm(tickers_df.iterrows(), total=len(tickers_df), desc="일봉 수집"):
        stock_code = str(row["stock_code"]).zfill(6)
        stock_name = row["stock_name"]
        market = row.get("market", "KOSPI")

        try:
            df = None
            try:
                df = fetch_daily_pykrx(stock_code, start_date, end_date)
            except Exception as pykrx_err:
                logger.debug(f"pykrx 실패 [{stock_code}]: {pykrx_err}")

            if df is None or df.empty:
                try:
                    df = fetch_daily_fdr(stock_code, start_date, end_date)
                except Exception as fdr_err:
                    logger.debug(f"FDR 실패 [{stock_code}]: {fdr_err}")

            if df is None or df.empty:
                logger.debug(f"데이터 없음: {stock_code} ({stock_name})")
                continue

            # 최소 거래대금 필터
            min_tv = cfg.get("risk", {}).get("min_avg_20d_trading_value", 5_000_000_000)
            avg_tv = df["trading_value"].tail(20).mean()
            if avg_tv < min_tv * 0.1:
                logger.debug(f"거래대금 부족 제외: {stock_code} ({stock_name}), 평균 {avg_tv:,.0f}원")
                continue

            df["stock_code"] = stock_code
            df["stock_name"] = stock_name
            df["market"] = market
            all_data.append(df)

        except Exception as e:
            error_msg = f"{stock_code}|{stock_name}|{e}"
            logger.warning(f"수집 실패: {error_msg}")
            error_tickers.append(error_msg)

    # 오류 로그 기록
    if error_tickers:
        with open(error_log_path, "a", encoding="utf-8") as f:
            for msg in error_tickers:
                f.write(f"{datetime.now().isoformat()} | {msg}\n")
        logger.warning(f"수집 실패 종목 {len(error_tickers)}개 → {error_log_path}")

    if not all_data:
        logger.error("수집된 데이터가 없습니다.")
        return

    result = pd.concat(all_data, ignore_index=True)
    result = result.drop_duplicates(subset=["date", "stock_code"])
    result = result.sort_values(["stock_code", "date"]).reset_index(drop=True)

    # 컬럼 순서 정리
    out_cols = ["date", "stock_code", "stock_name", "open", "high", "low",
                "close", "volume", "trading_value", "market"]
    available = [c for c in out_cols if c in result.columns]
    result = result[available]

    # stock_code 6자리 문자열 보장
    result["stock_code"] = result["stock_code"].astype(str).str.zfill(6)

    save_csv(result, output_path)
    logger.info(
        f"일봉 데이터 저장 완료: {output_path} "
        f"({len(result):,}행, {result['stock_code'].nunique()}개 종목)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="일봉 데이터 수집 (3년치)")
    parser.add_argument("--years", type=int, default=None,
                        help="수집 기간(년, 기본값: config.yaml lookback_years)")
    parser.add_argument("--limit", type=int, default=None,
                        help="종목 수 제한 (테스트용, 예: --limit 100)")
    parser.add_argument("--all", action="store_true",
                        help="전체 종목 수집 (--limit 무시)")
    parser.add_argument("--market", default="ALL", choices=["ALL", "KOSPI", "KOSDAQ"],
                        help="수집 시장 (기본값: ALL)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="기존 파일 무시하고 전체 재수집")
    args = parser.parse_args()

    lookback_years = args.years or cfg["strategy"].get("lookback_years", 3)
    start_date = calc_trading_days_ago(lookback_years)
    end_date = get_today_str("%Y%m%d")
    output_path = cfg["data"]["raw_daily_path"]
    error_log_path = cfg["data"].get("error_log_path", "logs/data_collect_errors.log")

    # ── 조기 스킵: pykrx/FDR API 호출 전에 빠르게 판단 ─────────────
    # 파일 수정 시간만 확인 (CSV 읽기 없음) — 빠른 종료
    if not args.force_refresh and os.path.exists(output_path):
        import time as _t
        _age_hours = (_t.time() - os.path.getmtime(output_path)) / 3600
        _now = datetime.now()
        _market_closed = (_now.hour > 16) or (_now.hour == 16 and _now.minute >= 30)
        if _age_hours < 12 and not _market_closed:
            logger.info(
                f"[조기 종료] 데이터 파일이 {_age_hours:.1f}시간 전 수집됨 & 장 마감 전 "
                f"— 종목 리스트 조회 없이 수집 생략."
            )
            print(f"[SKIP] collect_daily_data: {_age_hours:.1f}h ago, 장 마감 전 건너뜀", flush=True)
            return
    # ────────────────────────────────────────────────────────────────

    # 강제 새로고침
    if args.force_refresh and os.path.exists(output_path):
        os.remove(output_path)
        logger.info("기존 데이터 파일 삭제 (강제 재수집)")

    # Render 환경 자동 감지 — 종목 수 제한 (RENDER=true 또는 RENDER_EXTERNAL_URL 존재 시)
    _is_render = bool(
        os.environ.get("RENDER")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("RENDER_SERVICE_ID")
    )
    _render_limit = int(os.environ.get("RENDER_COLLECT_LIMIT", "300"))

    limit = None if args.all else args.limit
    if _is_render and limit is None and not args.all:
        limit = _render_limit
        logger.info(f"[Render] 환경 감지 — 종목 수 자동 제한: {limit}개 (RENDER_COLLECT_LIMIT={_render_limit})")
        print(f"[RENDER] 환경 감지: 종목 수 {limit}개로 자동 제한합니다.", flush=True)

    logger.info("=== 일봉 데이터 수집 시작 ===")
    logger.info(f"수집 기간: {lookback_years}년 ({start_date} ~ {end_date})")

    tickers_df = get_ticker_list(args.market)
    tickers_df = filter_tickers(tickers_df)

    # stock_master.csv 저장
    master_path = os.path.join(os.path.dirname(output_path), "stock_master.csv")
    master_df = make_stock_master(tickers_df)
    save_csv(master_df, master_path)
    logger.info(f"stock_master 저장: {master_path} ({len(master_df)}개 종목)")

    collect_all_daily_data(
        tickers_df, start_date, end_date, output_path, error_log_path, limit
    )
    # 수집 완료 후 OHLCV 기반으로 거래정지 종목 탐지 및 stock_master 갱신
    _update_stock_master_halted(master_path, output_path, tickers_df)
    logger.info("=== 일봉 데이터 수집 완료 ===")


if __name__ == "__main__":
    main()
