"""AI 후보 리스트 페이지 — top100 + 전략 선택 + 매수 실행."""

import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
sys.path.insert(0, str(PROJECT_ROOT / "app" / "components"))

import streamlit as st
import pandas as pd
from config_service import load_config, get_trade_mode
from prediction_service import (
    load_top20, load_force_candidates, run_script,
    run_force_trade_selector, run_no_trade_analysis,
    get_today_str, get_top20_path, get_force_candidates_path,
)
from pipeline_service import run_full_pipeline, run_fast_candidate_pipeline, find_latest_candidate_file
from trading_service import (
    run_buy_candidates, list_sell_policies,
    refresh_candidate_prices_service, get_active_buy_candidate_file,
)
from tables import render_candidates_table
from warning_box import force_trade_disclaimer, no_profit_guarantee_notice
from mode_badge import render_mode_badge, render_mode_warning


def render_budget_result_metrics(result: dict) -> None:
    if not isinstance(result, dict):
        return
    labels = [
        ("입력 예산", "input_budget"),
        ("실제 주문가능금액", "orderable_cash"),
        ("실제 사용 가능 예산", "effective_budget"),
        ("예상 사용금액", "expected_order_amount"),
        ("남은 금액", "remaining_budget"),
    ]
    if not any(key in result for _, key in labels):
        return
    cols = st.columns(len(labels))
    for col, (label, key) in zip(cols, labels):
        value = int(float(result.get(key, 0) or 0))
        col.metric(label, f"{value:,}원")

st.set_page_config(page_title="AI 후보 리스트", page_icon="📋", layout="wide")
st.title("📋 AI 후보 리스트")
st.caption("+2% 익절 목표 전략 기반 AI 예측 후보 종목 (top100)")

cfg = load_config()
mode = get_trade_mode(cfg)
render_mode_badge(mode)

today_str = get_today_str()

# ── Render 환경 감지 배너 ──────────────────────────────────────────
import os as _os
_IS_RENDER = bool(
    _os.environ.get("RENDER")
    or _os.environ.get("RENDER_EXTERNAL_URL")
    or _os.environ.get("RENDER_SERVICE_ID")
)
if _IS_RENDER:
    _render_limit = int(_os.environ.get("RENDER_COLLECT_LIMIT", "300"))
    st.info(
        f"**Render 배포 환경 감지됨**  \n"
        f"- 데이터 수집: 자동으로 상위 {_render_limit}개 종목으로 제한됩니다.  \n"
        f"- **'전체 파이프라인 실행'은 30~60분 소요**됩니다. 최초 1회만 필요합니다.  \n"
        f"- 이후 재실행은 **'빠른 후보 생성 (Render 권장)'** 버튼을 사용하세요 (2~5분).  \n"
        f"- 수집 종목 수를 늘리려면 Render Dashboard → Environment Variables에서 "
        f"`RENDER_COLLECT_LIMIT=500` 등으로 설정하세요."
    )
_hdr_col1, _hdr_col2 = st.columns([3, 1])
with _hdr_col1:
    st.caption(f"오늘 날짜: {today_str}")
with _hdr_col2:
    if st.button("캐시 초기화", use_container_width=True):
        st.cache_data.clear()
        st.success("캐시 초기화 완료")
        st.rerun()

predictions_dir = PROJECT_ROOT / "reports" / "predictions"


def load_top_n(n: int, date_str: str):
    enriched_path = PROJECT_ROOT / "reports" / f"enriched_candidates_{date_str}.csv"
    if n == 100 and enriched_path.exists():
        try:
            return pd.read_csv(enriched_path)
        except Exception:
            pass
    path = predictions_dir / f"top{n}_{date_str}.csv"
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return None
    return None


def load_top_n_latest(n: int, date_str: str):
    """오늘 파일이 없으면 가장 최근 top{n} 파일을 반환. (date_str, df) 튜플."""
    df = load_top_n(n, date_str)
    if df is not None:
        return date_str, df
    # 1순위: top{n}_YYYYMMDD.csv
    candidates = sorted(predictions_dir.glob(f"top{n}_????????.csv"), reverse=True)
    for p in candidates:
        try:
            df = pd.read_csv(p)
            found_date = p.stem.split("_")[1]
            return found_date, df
        except Exception:
            continue
    # 2순위: candidates_YYYYMMDD.csv
    for p in sorted(predictions_dir.glob("candidates_????????.csv"), reverse=True):
        try:
            df = pd.read_csv(p)
            stem_parts = p.stem.split("_")
            found_date = stem_parts[-1] if stem_parts else date_str
            return found_date, df
        except Exception:
            continue
    return date_str, None


# ── 파일 상태 ──────────────────────────────────────────────────
top100_path = predictions_dir / f"top100_{today_str}.csv"
top20_path = predictions_dir / f"top20_{today_str}.csv"
force_path = PROJECT_ROOT / "reports" / f"force_trade_candidates_{today_str}.csv"
model_path = PROJECT_ROOT / "models" / "model.joblib"
daily_path = PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"

buy_top20_path = predictions_dir / f"buy_top20_{today_str}.csv"

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("3년치 데이터", "있음 ✅" if daily_path.exists() else "없음 ❌")
col2.metric("학습 모델", "있음 ✅" if model_path.exists() else "없음 ❌")
col3.metric("Top100 파일", "있음 ✅" if top100_path.exists() else "없음 ❌")
col4.metric("장중 Top20 파일", "있음 ✅" if buy_top20_path.exists() else "없음 ❌")
col5.metric("force_trade 후보", "있음 ✅" if force_path.exists() else "없음 ❌")

# buy_top20 vs top100 동기화 경고
if top100_path.exists() and buy_top20_path.exists():
    import time as _time_sync
    _top100_mtime = top100_path.stat().st_mtime
    _buy20_mtime = buy_top20_path.stat().st_mtime
    if _top100_mtime > _buy20_mtime + 60:  # top100이 buy_top20보다 1분 이상 새 것
        st.warning(
            f"⚠ top100 파일이 buy_top20 파일보다 최신입니다 "
            f"(top100: {_time_sync.strftime('%H:%M:%S', _time_sync.localtime(_top100_mtime))}, "
            f"buy_top20: {_time_sync.strftime('%H:%M:%S', _time_sync.localtime(_buy20_mtime))}). "
            f"'장중 Top20 필터 실행' 버튼으로 buy_top20을 재생성하면 예산배분/주문 페이지 종목과 일치합니다."
        )
elif top100_path.exists() and not buy_top20_path.exists():
    st.info("장중 Top20 파일이 없습니다. '장중 Top20 필터 실행' 버튼으로 생성하면 예산배분/주문 페이지에서 최적 20개 종목이 표시됩니다.")

# 후보 파일 폴더 상태
_preds_dir_exists = predictions_dir.exists()
_pred_files = sorted(predictions_dir.glob("top100_????????.csv"), reverse=True) if _preds_dir_exists else []
_latest_pred = _pred_files[0] if _pred_files else None
import time as _time
_latest_info = (
    f"{_latest_pred.name}  ({_time.strftime('%Y-%m-%d %H:%M', _time.localtime(_latest_pred.stat().st_mtime))})"
    if _latest_pred else "없음"
)
with st.expander(f"후보 파일 폴더 상태 (reports/predictions)", expanded=False):
    st.write(f"- 폴더 존재: {'✅' if _preds_dir_exists else '❌'}")
    st.write(f"- top100 파일 수: {len(_pred_files)}개")
    st.write(f"- 최신 파일: {_latest_info}")

st.divider()

# ── 환경변수 상태 ──────────────────────────────────────────────
st.subheader("환경변수 상태 (파이프라인 실행 전 확인)")
_env_keys = ["DART_API_KEY", "KIS_MOCK_APP_KEY", "KIS_REAL_APP_KEY"]
_env_cols = st.columns(len(_env_keys))
for _ec, _ek in zip(_env_cols, _env_keys):
    _ec.metric(_ek, "OK ✅" if os.environ.get(_ek) else "MISSING ❌")

st.divider()

# ── 데이터 파이프라인 실행 버튼 ──────────────────────────────────
st.subheader("데이터 파이프라인 실행")
st.caption("순서대로 실행하세요: 데이터 수집 → 피처 생성 → 라벨 생성 → 모델학습 → 예측 → Top100")

def _show_script_result(r: dict, label: str) -> None:
    """스크립트 실행 결과를 화면에 표시 (성공/실패 + stdout/stderr)."""
    if not isinstance(r, dict):
        st.error(f"{label}: 반환값 오류 (None)")
        return
    if r.get("success"):
        st.success(f"{label} 완료")
        if r.get("stdout"):
            with st.expander("실행 출력 (stdout)"):
                st.code(r["stdout"][-2000:], language="text")
    else:
        st.error(f"❌ {label} 실패: {r.get('message', '')}")
        if r.get("stderr"):
            with st.expander("오류 상세 (stderr)", expanded=True):
                st.code(r["stderr"][-3000:], language="text")
        if r.get("stdout"):
            with st.expander("실행 출력 (stdout)"):
                st.code(r["stdout"][-2000:], language="text")


row1 = st.columns(4)
pipeline_steps = [
    ("1. 데이터 수집", "collect_daily_data.py"),
    ("2. 피처 생성", "make_features.py"),
    ("3. 라벨 생성", "make_labels.py"),
    ("4. 모델 학습", "train_model.py"),
]
for i, (label, script) in enumerate(pipeline_steps):
    with row1[i]:
        if st.button(label, use_container_width=True):
            with st.spinner(f"{label} 실행 중..."):
                r = run_script(script, timeout=1800)
            _show_script_result(r, label)

row2 = st.columns(4)
with row2[0]:
    if st.button("5. 예측 생성", use_container_width=True):
        with st.spinner("예측 생성 중..."):
            r = run_script("predict_candidates.py", timeout=300)
        _show_script_result(r, "5. 예측 생성")

with row2[1]:
    if st.button("6. Top100 생성", use_container_width=True):
        with st.spinner("Top100 생성 중..."):
            r = run_script("select_top_candidates.py", timeout=120)
        _show_script_result(r, "6. Top100 생성")
        if r and r.get("success"):
            st.rerun()

with row2[2]:
    if st.button("7. force_trade 후보", use_container_width=True):
        with st.spinner("force_trade 후보 생성 중..."):
            r = run_force_trade_selector(today_str, min_candidates=1)
        if r and r.get("success"):
            st.success(f"후보 {r.get('count',0)}개 생성")
            st.rerun()
        else:
            st.error((r or {}).get("message", "오류"))

with row2[3]:
    if st.button("전체 파이프라인 실행", use_container_width=True, type="primary"):
        print("[PIPELINE] BUTTON_CLICKED full_pipeline", flush=True)
        # ── 핵심 수정: run_full_pipeline()을 직접 호출 ──────────────────
        # 이전 방식: st.write() → run_pipeline_step() 루프
        #   → WebSocket 끊기면 st.write()에서 StopException → 루프 종료
        #   → make_features 이후 단계가 실행되지 않음
        # 새 방식: run_full_pipeline() 내부에서 print()만 사용
        #   → WebSocket과 무관하게 모든 단계가 Render에서 실행됨
        #   → Render Logs에 ABOUT_TO_START / START / END 로그가 반드시 남음
        try:
            _info_box = st.empty()
            _info_box.info(
                "파이프라인 실행 중입니다. 데이터 수집에 최대 30분 소요될 수 있습니다.\n\n"
                "연결이 끊겨도 서버에서 계속 실행됩니다 — Render Dashboard > Logs에서 진행 상황을 확인하세요."
            )
        except Exception:
            pass

        _r = run_full_pipeline(mode="paper", top_n=100, refresh_prices=True, years=3)
        st.session_state["last_pipeline_result"] = _r

        try:
            _info_box.empty()
        except Exception:
            pass

        try:
            if _r.get("success"):
                st.session_state["latest_candidate_file"] = _r.get("candidate_file", "")
                st.success(
                    f"전체 파이프라인 완료 — Top100: {_r.get('candidate_count', 0)}개 종목\n\n"
                    f"로그: {_r.get('log_path', '')}"
                )
                # ── 단계별 결과 표 ──────────────────────────────────────
                _steps_data = []
                for _s in _r.get("steps", []):
                    _art = _s.get("artifacts_after", {})
                    _steps_data.append({
                        "단계": _s.get("step", ""),
                        "결과": "✅" if _s.get("success") else "❌",
                        "소요(초)": _s.get("duration_sec", 0),
                        "종료코드": _s.get("returncode", -1),
                        "raw파일": _art.get("raw_count", "-"),
                        "processed": _art.get("processed_count", "-"),
                        "top100": _art.get("top100_count", "-"),
                    })
                with st.expander("단계별 결과", expanded=True):
                    if _steps_data:
                        st.dataframe(pd.DataFrame(_steps_data), use_container_width=True, hide_index=True)
                    # 최종 산출물 현황
                    _af = _r.get("artifacts_final", {})
                    if _af:
                        _af_cols = st.columns(6)
                        _af_cols[0].metric("raw 파일", f"{_af.get('raw_count', 0)}개")
                        _af_cols[1].metric("processed", f"{_af.get('processed_count', 0)}개")
                        _af_cols[2].metric("models", f"{_af.get('models_count', 0)}개")
                        _af_cols[3].metric("top100", f"{_af.get('top100_count', 0)}개")
                        _af_cols[4].metric("buy_top20", f"{_af.get('buy_top20_count', 0)}개")
                        _af_cols[5].metric("buy_top20 파일", "✅" if _r.get("buy_top20_file") else "없음")
                st.rerun()
            else:
                _failed = _r.get("failed_step", "?")
                _msg = _r.get("error_message", "알 수 없는 오류")
                st.error(f"파이프라인 실패 — 단계: {_failed}\n\n{_msg[:500]}")
                # 실패 단계 stderr
                _fail_stderr = _r.get("stderr_raw", "")
                if _fail_stderr:
                    with st.expander("실패 단계 stderr", expanded=True):
                        st.code(_fail_stderr[-4000:], language="text")
                # 산출물 현황 (실패 지점까지 생성된 파일 확인)
                _af_fail = _r.get("artifacts_final", {})
                if _af_fail:
                    with st.expander("실패 시점 산출물 현황", expanded=True):
                        _afc = st.columns(5)
                        _afc[0].metric("raw", f"{_af_fail.get('raw_count',0)}개 / daily:{_af_fail.get('raw_daily_exists',False)}")
                        _afc[1].metric("features", "✅" if _af_fail.get("features_exists") else "❌")
                        _afc[2].metric("labels", "✅" if _af_fail.get("labels_exists") else "❌")
                        _afc[3].metric("models", f"{_af_fail.get('models_count',0)}개")
                        _afc[4].metric("top100", f"{_af_fail.get('top100_count',0)}개")
                # 모든 단계 결과 JSON
                with st.expander("전체 결과 JSON (Render 디버그용)", expanded=False):
                    st.json(_r)
        except Exception as _disp_ex:
            # WebSocket이 끊긴 경우에도 파이프라인은 완료됨 — 재접속 후 확인 가능
            print(f"[PIPELINE] UI_DISPLAY_ERROR: {_disp_ex}", flush=True)

# ── 빠른 후보 생성 (Render 권장) ─────────────────────────────────
row3 = st.columns(2)
with row3[0]:
    if st.button("빠른 후보 생성 (Render 권장)", use_container_width=True):
        print("[PIPELINE] BUTTON_CLICKED fast_pipeline", flush=True)
        st.write("🔄 빠른 후보 생성 시작 (predict → select_top)...")
        with st.spinner("빠른 후보 생성 중 (predict_candidates + select_top_candidates)..."):
            r = run_fast_candidate_pipeline(mode="paper", top_n=100)

        st.session_state["last_pipeline_result"] = r

        if not isinstance(r, dict):
            st.error("파이프라인 반환값 오류 — dict가 아닌 값 반환됨")
            st.code(str(r)[:2000], language="text")
        elif r.get("success"):
            st.success(f"빠른 후보 생성 완료 — Top100: {r.get('candidate_count', 0)}개 종목")
            _cf = r.get("candidate_file", "")
            if _cf:
                st.session_state["latest_candidate_file"] = _cf
                st.caption(f"후보 파일: {_cf}")
            _steps = r.get("steps", [])
            if _steps:
                _sdf = pd.DataFrame([{
                    "단계": s.get("step", ""),
                    "결과": "✅ 성공" if s.get("success") else "❌ 실패",
                    "소요(초)": s.get("duration_sec", 0),
                    "returncode": s.get("returncode", 0),
                } for s in _steps])
                st.dataframe(_sdf, use_container_width=True, hide_index=True)
            with st.expander("전체 결과 JSON", expanded=False):
                st.json(r)
            st.rerun()
        else:
            _failed = r.get("failed_step", "알 수 없음")
            st.error(f"빠른 후보 생성 실패 — {_failed}: {r.get('error_message','')}")
            _stderr = r.get("stderr_raw", "")
            if _stderr:
                with st.expander("오류 상세 (stderr)", expanded=True):
                    st.code(_stderr[-4000:], language="text")
            with st.expander("전체 결과 JSON", expanded=False):
                st.json(r)

with row3[1]:
    if st.button("최신 파이프라인 로그 보기", use_container_width=True):
        _log_dir = PROJECT_ROOT / "logs"
        # JSON 로그 우선, 없으면 .log fallback
        _log_files = sorted(_log_dir.glob("pipeline_????????_*.json"), reverse=True) if _log_dir.exists() else []
        if not _log_files:
            _log_files = sorted(_log_dir.glob("pipeline_*.log"), reverse=True) if _log_dir.exists() else []
        if _log_files:
            _lf = _log_files[0]
            try:
                _log_content = _lf.read_text(encoding="utf-8", errors="replace")
                with st.expander(f"로그: {_lf.name}", expanded=True):
                    if _lf.suffix == ".json":
                        try:
                            import json as _json_log
                            st.json(_json_log.loads(_log_content))
                        except Exception:
                            st.code(_log_content[-5000:], language="json")
                    else:
                        st.code(_log_content[-5000:], language="text")
            except Exception as _ex:
                st.error(f"로그 읽기 실패: {_ex}")
        else:
            st.info("로그 파일이 없습니다 (logs/pipeline_*.json)")

# ── Render 파이프라인 Probe ──────────────────────────────────────────
_probe_cols = st.columns(2)
with _probe_cols[0]:
    if st.button("Render 파이프라인 Probe 실행", use_container_width=True):
        import subprocess as _spp, sys as _spsy
        from pipeline_service import ensure_runtime_directories as _erd
        _erd()
        try:
            _probe_result = _spp.run(
                [_spsy.executable, "-u", str(PROJECT_ROOT / "src" / "render_pipeline_probe.py")],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
                env=__import__("os").environ.copy(),
            )
            if _probe_result.returncode == 0:
                try:
                    _probe_json = __import__("json").loads(_probe_result.stdout)
                    with st.expander("Probe 결과 (JSON)", expanded=True):
                        st.json(_probe_json)
                except Exception:
                    st.code(_probe_result.stdout[:3000], language="json")
            else:
                st.error(f"Probe 실패 (exit {_probe_result.returncode})")
                st.code(_probe_result.stderr[:2000], language="text")
        except Exception as _probe_ex:
            st.error(f"Probe 실행 오류: {_probe_ex}")

# ── 장중 매수 Top20 필터 실행 ──────────────────────────────────────
st.divider()
row4 = st.columns(3)
with row4[0]:
    _intraday_mode = st.selectbox(
        "장중 필터 모드",
        ["paper", "mock", "real"],
        index=0,
        key="intraday_filter_mode",
        help="paper: API 미사용 / mock: KIS 모의 OHLCV 조회 / real: KIS 실전 OHLCV 조회",
    )

with row4[1]:
    if st.button("장중 매수 Top20 필터 실행", use_container_width=True, type="primary"):
        print("[PIPELINE] BUTTON_CLICKED intraday_top20_filter", flush=True)
        from pipeline_service import run_pipeline_step
        _intra_today = datetime.now().strftime("%Y%m%d")
        with st.spinner(f"장중 Top20 필터 실행 중 (mode={_intraday_mode})..."):
            _intra_r = run_pipeline_step(
                step_name="select_intraday_buy_candidates",
                script_name="select_intraday_buy_candidates.py",
                args=["--mode", _intraday_mode, "--date", _intra_today, "--top-n", "20"],
                timeout=300,
            )
        st.session_state["last_intraday_result"] = _intra_r
        if _intra_r.get("success"):
            st.success("장중 Top20 필터 완료")
            _intra_out = predictions_dir / f"buy_top20_{_intra_today}.csv"
            if _intra_out.exists():
                _intra_df = pd.read_csv(_intra_out)
                st.metric("선정 종목 수", f"{len(_intra_df)}개")
                st.dataframe(_intra_df.head(20), use_container_width=True)
            with st.expander("상세 결과 JSON", expanded=False):
                st.json(_intra_r)
            st.rerun()
        else:
            st.error(f"장중 Top20 필터 실패: {_intra_r.get('stderr', '')[:1000]}")
            with st.expander("오류 상세", expanded=True):
                st.code(_intra_r.get("stderr", "")[-3000:], language="text")

with row4[2]:
    _buy_top20_today = predictions_dir / f"buy_top20_{today_str}.csv"
    if _buy_top20_today.exists():
        try:
            _bt20 = pd.read_csv(_buy_top20_today)
            st.metric("오늘 buy_top20", f"{len(_bt20)}개")
            if st.button("buy_top20 CSV 미리보기", use_container_width=True):
                st.dataframe(_bt20, use_container_width=True)
        except Exception:
            st.metric("오늘 buy_top20", "읽기 오류")
    else:
        st.metric("오늘 buy_top20", "없음")

# ── 최근 파이프라인 결과 (session_state 보존) ───────────────────
if "last_pipeline_result" in st.session_state:
    _lpr = st.session_state["last_pipeline_result"]
    _lpr_success = _lpr.get("success") if isinstance(_lpr, dict) else None
    _lpr_icon = "✅" if _lpr_success else "❌"
    with st.expander(f"{_lpr_icon} 최근 파이프라인 실행 결과 (session 보존)", expanded=False):
        if isinstance(_lpr, dict):
            _lpr_steps = _lpr.get("steps", [])
            if _lpr_steps:
                st.dataframe(pd.DataFrame([{
                    "단계": s.get("step", ""),
                    "결과": "✅" if s.get("success") else "❌",
                    "소요(초)": s.get("duration_sec", 0),
                } for s in _lpr_steps]), use_container_width=True, hide_index=True)
            st.json(_lpr)
        else:
            st.write(_lpr)

st.divider()

# ── Top100 리스트 ────────────────────────────────────────────────
_top100_date, df_top100 = load_top_n_latest(100, today_str)
if _top100_date != today_str:
    st.warning(
        f"오늘({today_str}) 파이프라인 결과가 아직 없습니다. "
        f"가장 최근 데이터({_top100_date})를 표시합니다. "
        f"장 마감(16:30) 후 '전체 파이프라인 실행'을 누르면 오늘 결과가 생성됩니다."
    )
st.subheader(f"AI Top100 후보 ({_top100_date})")

if df_top100 is not None and not df_top100.empty:
    # 종목코드 6자리 정규화
    code_col = "stock_code" if "stock_code" in df_top100.columns else "ticker"
    if code_col in df_top100.columns:
        df_top100[code_col] = df_top100[code_col].apply(
            lambda x: str(int(str(x).replace(".0", "").strip())).zfill(6)
        )

    col_a, col_b, col_c = st.columns([2, 1, 1])
    with col_a:
        sort_options = [c for c in ["probability_2pct", "trading_value", "volume_ratio_20"] if c in df_top100.columns]
        if sort_options:
            sort_col = st.selectbox("정렬 기준", sort_options)
            df_top100 = df_top100.sort_values(sort_col, ascending=False)
    with col_b:
        show_n = st.selectbox("표시 개수", [20, 50, 100], index=2)
        df_display = df_top100.head(show_n)
    with col_c:
        refresh_mode = st.selectbox(
            "갱신 모드", ["PAPER", "MOCK"], key="refresh_mode_select",
            help="PAPER: API 호출 없음 (빠름, 권장) / MOCK: KIS 모의투자 API",
        )
        if st.button("현재가 갱신하기", use_container_width=True):
            _rm = refresh_mode.lower()

            # 단일 진실 공급원: buy_top20 파일만 갱신
            _active_file = get_active_buy_candidate_file(date=today_str)
            if not _active_file:
                st.error(
                    f"buy_top20_{today_str}.csv 파일이 없습니다.\n"
                    "'장중 매수 Top20 필터 실행' 버튼을 먼저 실행하세요."
                )
            else:
                # 1. buy_top20 갱신 (필수)
                with st.spinner(f"buy_top20 현재가 갱신 중 (mode={refresh_mode})..."):
                    r_buy20 = refresh_candidate_prices_service(
                        date_str=today_str,
                        mode=_rm,
                        candidate_file=str(_active_file),
                    )

                # MOCK 실패 시 PAPER 자동 재시도
                if not r_buy20.get("success") and _rm == "mock":
                    st.warning("MOCK 갱신 실패 — PAPER 모드로 자동 재시도합니다.")
                    r_buy20 = refresh_candidate_prices_service(
                        date_str=today_str,
                        mode="paper",
                        candidate_file=str(_active_file),
                    )

                if r_buy20.get("success"):
                    _updated = r_buy20.get("updated", 0)
                    _failed = r_buy20.get("errors", 0)
                    _used_mode = r_buy20.get("price_mode", refresh_mode.upper())
                    st.success(
                        f"✅ buy_top20 현재가 갱신 완료 (mode={_used_mode})\n"
                        f"갱신: {_updated}개 / 실패: {_failed}개\n"
                        f"파일: {Path(r_buy20.get('file', '')).name}"
                    )
                    if _failed > 0:
                        st.warning(
                            f"⚠ {_failed}개 종목 조회 실패: {r_buy20.get('price_error', '')[:200]}"
                        )
                    # 2. top100도 함께 갱신 (참고용 — 실패해도 무시)
                    _top100_path = predictions_dir / f"top100_{today_str}.csv"
                    if _top100_path.exists():
                        run_script("refresh_candidate_prices.py",
                                   args=["--input", str(_top100_path), "--mode", _rm],
                                   timeout=120)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    _msg = r_buy20.get("message", "갱신 실패")
                    st.error(f"❌ 현재가 갱신 실패: {_msg}")
                    if r_buy20.get("price_error"):
                        st.caption(f"실패 종목: {r_buy20['price_error'][:300]}")

    st.caption("⚠ 이 목록은 모델 기반 후보군입니다. 투자 추천이 아니며 수익을 보장하지 않습니다.")

    # 선택 체크박스 포함 테이블
    df_with_sel = df_display.copy()
    df_with_sel.insert(0, "선택", False)
    edited = st.data_editor(
        df_with_sel,
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", default=False),
            code_col: st.column_config.TextColumn("종목코드"),
        },
        use_container_width=True,
        hide_index=True,
        key="candidate_table",
    )
    selected_rows = edited[edited["선택"] == True] if "선택" in edited.columns else pd.DataFrame()

    csv = df_display.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(f"Top{show_n} CSV 다운로드", csv, f"top{show_n}_{_top100_date}.csv", "text/csv")

    st.divider()

    # ── 거래전략 선택 + 매수 설정 ─────────────────────────────────
    st.subheader("거래전략 선택 및 매수 실행")
    st.caption("+2% 익절 목표 전략 — 수익을 보장하지 않습니다.")

    try:
        from strategy_config import get_strategy_display_labels, label_to_strategy_id
        strategy_labels = get_strategy_display_labels()
    except Exception:
        strategy_labels = [
            "장초반 매매 — 9시30분경 매수, +2% 익절 목표",
            "종가 매매 — 오후 3시경 매수, 시간외·프리마켓·다음날 장중 +2% 익절 목표",
        ]

    strategy_label = st.radio(
        "거래전략 선택",
        strategy_labels,
        index=0,
        help="장초반=9:25~9:40 매수 / 종가=14:50~15:20 매수",
    )

    try:
        strategy_id = label_to_strategy_id(strategy_label)
    except Exception:
        strategy_id = "morning_0930" if "장초반" in strategy_label else "afternoon_1500"

    sell_policies = list_sell_policies()
    policy_labels = {p["name"]: p["id"] for p in sell_policies}
    policy_descriptions = {p["id"]: p.get("description", "") for p in sell_policies}
    selected_policy_label = st.radio("매도방식 선택", list(policy_labels.keys()), horizontal=True, index=0)
    sell_policy_id = policy_labels[selected_policy_label]
    st.caption(policy_descriptions.get(sell_policy_id, ""))
    if sell_policy_id == "manual_hold":
        st.warning("수동매도 전까지 보유를 선택하면 +2%에 도달해도 자동매도되지 않습니다.")

    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    with s_col1:
        order_mode = st.selectbox("주문 모드", ["MOCK", "PAPER", "REAL"], index=0)
    with s_col2:
        buy_budget = st.number_input("예산 (원)", min_value=10_000, max_value=100_000_000,
                                      value=300_000, step=10_000)
    with s_col3:
        max_ord = st.number_input("최대 주문 수", min_value=1, max_value=show_n, value=min(show_n, 20))
    with s_col4:
        do_refresh = st.checkbox("현재가 갱신 후 매수", value=False)

    if order_mode == "REAL":
        st.error("REAL 모드: 안전장치 조건 5개가 모두 충족될 때만 실행됩니다.")

    btn_col1, btn_col2, btn_col3 = st.columns(3)

    enriched_file = PROJECT_ROOT / "reports" / f"enriched_candidates_{today_str}.csv"
    candidate_file = str(enriched_file if enriched_file.exists() else predictions_dir / f"top100_{today_str}.csv")

    with btn_col1:
        if st.button("현재 리스트 전부 매수", type="primary", use_container_width=True):
            with st.spinner(f"{order_mode} 모드로 {len(df_display)}개 후보 매수 실행 중..."):
                r = run_buy_candidates(
                    candidate_file=candidate_file, budget=int(buy_budget),
                    mode=order_mode.lower(), strategy_id=strategy_id,
                    max_orders=int(max_ord), refresh_prices=do_refresh,
                    sell_policy_id=sell_policy_id,
                )
            if not isinstance(r, dict):
                st.error("매수 반환값 오류")
            elif r.get("success"):
                st.success(r.get("message", "매수 완료"))
            else:
                st.error(f"매수 실패: {r.get('message','')}")
                for e in r.get("errors", [])[:2]:
                    st.caption(str(e))
            if isinstance(r, dict) and r.get("allocation_preview"):
                render_budget_result_metrics(r)
                st.dataframe(pd.DataFrame(r["allocation_preview"]), use_container_width=True)

    with btn_col2:
        sel_codes = list(selected_rows[code_col]) if not selected_rows.empty and code_col in selected_rows.columns else []
        btn_label = f"선택 종목만 매수 ({len(sel_codes)}개)" if sel_codes else "선택 종목만 매수 (체크 필요)"
        if st.button(btn_label, use_container_width=True, disabled=(not sel_codes)):
            with st.spinner(f"선택 {len(sel_codes)}개 종목 매수 중..."):
                r = run_buy_candidates(
                    candidate_file=candidate_file, budget=int(buy_budget),
                    mode=order_mode.lower(), strategy_id=strategy_id,
                    max_orders=len(sel_codes), selected_codes=[str(c) for c in sel_codes],
                    refresh_prices=do_refresh,
                    sell_policy_id=sell_policy_id,
                )
            if not isinstance(r, dict):
                st.error("매수 반환값 오류")
            elif r.get("success"):
                st.success(r.get("message", "매수 완료"))
            else:
                st.error(f"매수 실패: {r.get('message','')}")
            if isinstance(r, dict) and r.get("allocation_preview"):
                render_budget_result_metrics(r)
                st.dataframe(pd.DataFrame(r["allocation_preview"]), use_container_width=True)

    with btn_col3:
        if st.button("주문 미리보기 생성", use_container_width=True):
            with st.spinner("주문 미리보기 생성 중..."):
                r = run_buy_candidates(
                    candidate_file=candidate_file, budget=int(buy_budget),
                    mode=order_mode.lower(), strategy_id=strategy_id,
                    max_orders=int(max_ord), preview_only=True,
                    sell_policy_id=sell_policy_id,
                )
            if not isinstance(r, dict):
                st.error("미리보기 반환값 오류")
            elif r.get("allocation_preview"):
                st.success(f"미리보기 {len(r['allocation_preview'])}건 생성 (실제 주문 없음)")
                render_budget_result_metrics(r)
                st.dataframe(pd.DataFrame(r["allocation_preview"]), use_container_width=True)
            else:
                st.warning(r.get("message", "미리보기 생성 실패"))

else:
    st.info("오늘의 Top100 파일이 없습니다. 위의 '6. Top100 생성' 또는 '전체 파이프라인 실행'을 클릭하세요.")

    df_top20 = load_top20(today_str)
    if df_top20 is not None and not df_top20.empty:
        st.subheader("Top20 후보 (Top100 미생성 시 대체)")
        render_candidates_table(df_top20)

    if st.button("거래 0건 원인 분석"):
        with st.spinner("분석 중..."):
            r = run_no_trade_analysis()
        if r and r.get("success"):
            st.success("분석 완료")
            st.json(r.get("data", {}))
        else:
            st.error((r or {}).get("message", "분석 오류"))

st.divider()

# ── 장중 AI 모델 파이프라인 (당일 +2% 목표) ──────────────────────
st.subheader("장중 AI 모델 파이프라인 (당일 오전 10:30~11:30 매수 → +2% 목표)")
st.caption("intraday_2pct 모델: AUC=0.69 / Precision@top5%=79% / backtest hit_rate=80.9%")

_intra_candidates_path = predictions_dir / f"intraday_candidates_{today_str}.csv"
_intra_buy20_path = predictions_dir / f"buy_top20_{today_str}.csv"
_intra_model_path = PROJECT_ROOT / "models" / "intraday_2pct_model.joblib"

_ic1, _ic2, _ic3, _ic4 = st.columns(4)
_ic1.metric("장중 AI 모델", "있음 ✅" if _intra_model_path.exists() else "없음 ❌")
_ic2.metric("intraday_candidates", "있음 ✅" if _intra_candidates_path.exists() else "없음 ❌")
_ic3.metric("buy_top20", "있음 ✅" if _intra_buy20_path.exists() else "없음 ❌")
_intra_latest_feat = PROJECT_ROOT / "data" / "processed" / "intraday_latest_features.csv"
_ic4.metric("최신 피처", "있음 ✅" if _intra_latest_feat.exists() else "없음 ❌")

_irow1 = st.columns(4)
with _irow1[0]:
    if st.button("장중 피처 생성", use_container_width=True, key="btn_intra_features"):
        with st.spinner("intraday features 생성 중 (~2분)..."):
            _r_if = run_script("make_intraday_features.py", timeout=300)
        _show_script_result(_r_if, "장중 피처 생성")

with _irow1[1]:
    if st.button("장중 AI 예측", use_container_width=True, key="btn_intra_predict"):
        with st.spinner("intraday 후보 예측 중..."):
            _r_ip = run_script("predict_intraday_candidates.py",
                               args=["--date", today_str], timeout=120)
        _show_script_result(_r_ip, "장중 AI 예측")
        if _r_ip and _r_ip.get("success"):
            st.rerun()

with _irow1[2]:
    if st.button("장중 Top20 선정 (AI)", use_container_width=True, key="btn_intra_top20"):
        with st.spinner("intraday Top20 선정 중..."):
            _r_i20 = run_script("select_today_buy_top20.py",
                                args=["--date", today_str, "--safe-mode", "--mode", "mock"],
                                timeout=180)
        _show_script_result(_r_i20, "장중 Top20 선정")
        if _r_i20 and _r_i20.get("success"):
            st.rerun()

with _irow1[3]:
    if st.button("장중 백테스트", use_container_width=True, key="btn_intra_backtest"):
        with st.spinner("장중 전략 백테스트 실행 중..."):
            _r_ibt = run_script("backtest_intraday_strategy.py",
                                args=["--top-n", "20", "--min-prob", "0.58"],
                                timeout=180)
        _show_script_result(_r_ibt, "장중 백테스트")

# buy_top20 프리뷰 with prob_intraday_2pct
_active_buy20 = get_active_buy_candidate_file(date=today_str)
if _active_buy20 and Path(_active_buy20).exists():
    try:
        _df_buy20 = pd.read_csv(_active_buy20)
        _buy20_has_intra = "prob_intraday_2pct" in _df_buy20.columns
        _intra_label = "장중 AI prob" if _buy20_has_intra else "legacy score"
        st.caption(f"현재 buy_top20: {Path(_active_buy20).name} | {len(_df_buy20)}개 종목 | {_intra_label}")

        _show_cols = ["final_buy_rank"]
        for _c in ["stock_code", "ticker", "stock_name", "name"]:
            if _c in _df_buy20.columns:
                _show_cols.append(_c)
                break
        for _c in ["prob_intraday_2pct", "prob_intraday_3pct", "prob_intraday_5pct",
                   "final_intraday_score", "expected_max_return_pct",
                   "probability_2pct", "nextday_prob_2pct",
                   "gap_rate", "prev_return_1d", "current_price"]:
            if _c in _df_buy20.columns:
                _show_cols.append(_c)
        _show_cols = [c for c in _show_cols if c in _df_buy20.columns]

        st.dataframe(
            _df_buy20[_show_cols].head(20),
            use_container_width=True,
            hide_index=True,
            column_config={
                "prob_intraday_2pct": st.column_config.ProgressColumn(
                    "prob +2% (당일)", format="%.3f", min_value=0, max_value=1),
                "prob_intraday_3pct": st.column_config.ProgressColumn(
                    "prob +3% (당일)", format="%.3f", min_value=0, max_value=1),
                "prob_intraday_5pct": st.column_config.ProgressColumn(
                    "prob +5% (당일)", format="%.3f", min_value=0, max_value=1),
                "final_intraday_score": st.column_config.ProgressColumn(
                    "종합점수", format="%.3f", min_value=0, max_value=1),
                "gap_rate": st.column_config.NumberColumn("갭률", format="%.2f%%"),
                "probability_2pct": st.column_config.ProgressColumn(
                    "prob (익일)", format="%.3f", min_value=0, max_value=1),
            },
        )
        if not _buy20_has_intra:
            st.warning(
                "⚠ 이 buy_top20은 legacy 모델 기반입니다. "
                "'장중 AI 예측' → '장중 Top20 선정 (AI)' 순으로 실행하면 "
                "prob_intraday_2pct 기반 파일로 갱신됩니다."
            )
    except Exception as _e_b20:
        st.caption(f"buy_top20 미리보기 오류: {_e_b20}")
else:
    st.info(
        "buy_top20 파일 없음. '장중 AI 예측' 버튼 → '장중 Top20 선정 (AI)' 버튼 순으로 실행하세요."
    )

st.divider()

# ── force_trade 후보 ────────────────────────────────────────────
st.subheader("force_trade 후보 (필터 완화 적용)")
force_trade_disclaimer()

df_force = load_force_candidates(today_str)
if df_force is not None and not df_force.empty:
    render_candidates_table(df_force)
    csv2 = df_force.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        "force_trade 후보 CSV", csv2,
        f"force_trade_candidates_{today_str}.csv", "text/csv",
    )
else:
    st.info("force_trade 후보 파일이 없습니다. '7. force_trade 후보' 버튼을 클릭하세요.")

no_profit_guarantee_notice()
