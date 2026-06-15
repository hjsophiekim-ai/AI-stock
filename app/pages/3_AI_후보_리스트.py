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
from trading_service import run_buy_candidates, list_sell_policies
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

col1, col2, col3, col4 = st.columns(4)
col1.metric("3년치 데이터", "있음 ✅" if daily_path.exists() else "없음 ❌")
col2.metric("학습 모델", "있음 ✅" if model_path.exists() else "없음 ❌")
col3.metric("Top100 파일", "있음 ✅" if top100_path.exists() else "없음 ❌")
col4.metric("force_trade 후보", "있음 ✅" if force_path.exists() else "없음 ❌")

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
        st.write("🔄 전체 파이프라인 실행 시작...")
        with st.spinner("전체 파이프라인 실행 중 (시간이 걸립니다)..."):
            r = run_full_pipeline(mode="mock", top_n=100, refresh_prices=True, years=3)

        st.session_state["last_pipeline_result"] = r

        if not isinstance(r, dict):
            st.error("파이프라인 반환값 오류 — dict가 아닌 값 반환됨")
            st.code(str(r)[:2000], language="text")

        elif r.get("success"):
            st.success(f"전체 파이프라인 완료 — Top100: {r.get('candidate_count', 0)}개 종목")
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
            _failed = r.get("failed_step") or r.get("stage", "알 수 없음")
            _errmsg = r.get("error_message") or r.get("message", "")
            st.error(f"전체 파이프라인 실패 — 실패 단계: {_failed}")
            if _errmsg:
                st.caption(_errmsg)

            _steps = r.get("steps", [])
            if _steps:
                _sdf = pd.DataFrame([{
                    "단계": s.get("step", ""),
                    "결과": "✅ 성공" if s.get("success") else "❌ 실패",
                    "소요(초)": s.get("duration_sec", 0),
                    "returncode": s.get("returncode", 0),
                } for s in _steps])
                st.dataframe(_sdf, use_container_width=True, hide_index=True)

            _stderr = r.get("stderr_raw") or r.get("stderr", "")
            _stdout = r.get("stdout_raw") or r.get("stdout", "")
            if _stderr:
                with st.expander(f"오류 상세 (stderr) — {_failed}", expanded=True):
                    st.code(_stderr[-4000:], language="text")
            if _stdout:
                with st.expander(f"실행 출력 (stdout) — {_failed}"):
                    st.code(_stdout[-2000:], language="text")

            _errs = r.get("errors", [])
            if _errs and not _stderr:
                st.text_area("오류 상세", "\n".join(str(e) for e in _errs[:5]), height=120)

            _lp = r.get("log_path", "")
            if _lp:
                st.caption(f"로그: {_lp}")

            with st.expander("전체 결과 JSON", expanded=False):
                st.json(r)

# ── 빠른 후보 생성 (Render 권장) ─────────────────────────────────
row3 = st.columns(2)
with row3[0]:
    if st.button("빠른 후보 생성 (Render 권장)", use_container_width=True):
        print("[PIPELINE] BUTTON_CLICKED fast_pipeline", flush=True)
        st.write("🔄 빠른 후보 생성 시작 (predict → select_top)...")
        with st.spinner("빠른 후보 생성 중 (predict_candidates + select_top_candidates)..."):
            r = run_fast_candidate_pipeline(mode="mock", top_n=100)

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
        _log_files = sorted(_log_dir.glob("pipeline_*.log"), reverse=True) if _log_dir.exists() else []
        if _log_files:
            _lf = _log_files[0]
            try:
                _log_content = _lf.read_text(encoding="utf-8", errors="replace")
                with st.expander(f"로그: {_lf.name}", expanded=True):
                    st.code(_log_content[-5000:], language="text")
            except Exception as _ex:
                st.error(f"로그 읽기 실패: {_ex}")
        else:
            st.info("로그 파일이 없습니다 (logs/pipeline_*.log)")

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
        refresh_mode = st.selectbox("갱신 모드", ["MOCK", "PAPER"], key="refresh_mode_select",
                                    help="MOCK: KIS 모의투자 API 사용 / PAPER: API 호출 없음")
        if st.button("현재가 갱신", use_container_width=True):
            with st.spinner(f"현재가 갱신 중 (mode={refresh_mode})..."):
                r = run_script("refresh_candidate_prices.py",
                               args=["--date", today_str, "--top", "100",
                                     "--mode", refresh_mode.lower()], timeout=300)
            if r and r.get("success"):
                st.success("현재가 갱신 완료")
                st.rerun()
            else:
                st.error((r or {}).get("message", "갱신 실패"))

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
