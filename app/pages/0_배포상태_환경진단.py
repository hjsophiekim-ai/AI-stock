"""배포 상태 / 환경 진단 페이지.

Render 배포 환경과 로컬 환경의 차이를 앱 화면에서 바로 확인합니다.
환경변수 값 원문은 절대 표시하지 않으며 OK/MISSING만 표시합니다.
"""

import os
import platform
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
for _p in (
    str(PROJECT_ROOT),
    str(PROJECT_ROOT / "src"),
    str(PROJECT_ROOT / "app" / "services"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st

st.set_page_config(page_title="배포상태 환경진단", page_icon="🔍", layout="wide")
st.title("🔍 배포상태 / 환경 진단")
st.caption(
    "Render 배포 환경을 진단합니다. "
    "**환경변수 값 원문은 절대 표시되지 않습니다.** OK/MISSING만 표시됩니다."
)

# ── 서비스 임포트 ───────────────────────────────────────────────────
try:
    from startup_service import get_commit_hash, get_render_info, ensure_dirs, REQUIRED_DIRS
except Exception as _e:
    st.error(f"startup_service 로드 실패: {_e}")
    st.stop()

# ── 환경변수 로딩 + 필수 디렉토리 자동 생성 ─────────────────────────
try:
    from startup_service import initialize_app_environment
    _init_result = initialize_app_environment()
    _created_dirs = _init_result.get("dirs_created", [])
    _env_file_exists = _init_result.get("env_file_exists", False)
    _env_loaded = _init_result.get("env_loaded", False)
    _is_render = _init_result.get("running_on_render", False)
except Exception:
    try:
        from env_service import inject_to_os_env
        inject_to_os_env()
    except Exception:
        pass
    _created_dirs = ensure_dirs()
    _env_file_exists = (PROJECT_ROOT / ".env").exists()
    _env_loaded = False
    _is_render = bool(os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL"))

# ── 1. 기본 환경 정보 ──────────────────────────────────────────────
st.subheader("1. 기본 환경 정보")

commit_hash = get_commit_hash()
render_info = get_render_info()

c1, c2, c3, c4 = st.columns(4)
c1.metric("현재 배포 커밋", commit_hash)
c2.metric("Python 버전", platform.python_version())
c3.metric("플랫폼", platform.system())
c4.metric("Render 환경", "✅ YES" if render_info["is_render"] else "NO (로컬)")

c5, c6, c7, c8 = st.columns(4)
c5.metric("APP_ENV", os.environ.get("APP_ENV", "(미설정)"))
_root_short = str(PROJECT_ROOT)
_root_display = ("..." + _root_short[-37:]) if len(_root_short) > 40 else _root_short
c6.metric("PROJECT_ROOT", _root_display)
_cwd_short = str(Path.cwd())
_cwd_display = ("..." + _cwd_short[-37:]) if len(_cwd_short) > 40 else _cwd_short
c7.metric("CWD", _cwd_display)
c8.metric("진단 시각", datetime.now().strftime("%H:%M:%S"))

if render_info["is_render"]:
    st.info(
        f"**Render 서비스**: {render_info['render_service_name']}  |  "
        f"**URL**: {render_info['render_external_url']}  |  "
        f"**브랜치**: {render_info['render_git_branch']}  |  "
        f"**커밋**: {render_info['render_git_commit'][:8] if render_info['render_git_commit'] else 'UNKNOWN'}"
    )

st.divider()

# ── 1b. .env 파일 및 환경변수 로딩 상태 ──────────────────────────
st.subheader("1b. .env / 환경변수 로딩 상태")
_ev_c1, _ev_c2, _ev_c3, _ev_c4 = st.columns(4)
_ev_c1.metric(".env 파일", "✅ 있음" if _env_file_exists else "❌ 없음")
_ev_c2.metric(".env 로딩", "✅ 완료" if _env_loaded else ("미설치(fallback)" if _env_file_exists else "파일 없음"))
_ev_c3.metric("Render 환경", "✅ YES" if _is_render else "NO (로컬)")
_dart_ok = bool(os.environ.get("DART_API_KEY"))
_mock_ok = bool(os.environ.get("KIS_MOCK_APP_KEY"))
_real_ok = bool(os.environ.get("KIS_REAL_APP_KEY"))
_ev_c4.metric("핵심 키 상태",
    "✅ 모두 OK" if (_dart_ok and _mock_ok) else
    "⚠️ 일부 MISSING")

_kc1, _kc2, _kc3 = st.columns(3)
_kc1.metric("DART_API_KEY", "✅ OK" if _dart_ok else "❌ MISSING")
_kc2.metric("KIS_MOCK_APP_KEY", "✅ OK" if _mock_ok else "❌ MISSING")
_kc3.metric("KIS_REAL_APP_KEY", "✅ OK" if _real_ok else "❌ MISSING")

if not _env_file_exists and not _is_render:
    st.warning(
        ".env 파일이 없습니다. 로컬 실행 시 프로젝트 루트에 .env 파일을 생성하거나 "
        "Render Dashboard > Environment Variables에서 키를 설정하세요."
    )
elif _is_render and not (_dart_ok and _mock_ok):
    st.warning(
        "Render 환경입니다. Render Dashboard > Environment Variables에서 "
        "DART_API_KEY, KIS_MOCK_APP_KEY 등을 설정하세요. "
        "값 원문은 절대 표시되지 않습니다."
    )

st.divider()

# ── 2. 필수 환경변수 OK/MISSING ───────────────────────────────────
st.subheader("2. 필수 환경변수 (전체)")
st.caption("값은 표시되지 않습니다. OK = 설정됨, MISSING = 미설정")

REQUIRED_ENV_VARS = [
    ("KIS_MOCK_APP_KEY",    "MOCK API Key"),
    ("KIS_MOCK_APP_SECRET", "MOCK API Secret"),
    ("KIS_MOCK_ACCOUNT_NO", "MOCK 계좌번호"),
    ("KIS_REAL_APP_KEY",    "REAL API Key"),
    ("KIS_REAL_APP_SECRET", "REAL API Secret"),
    ("KIS_ACCOUNT_NO",      "REAL 계좌번호"),
    ("DART_API_KEY",        "DART API Key"),
]

env_statuses = {key: bool(os.environ.get(key)) for key, _ in REQUIRED_ENV_VARS}
all_env_ok = all(env_statuses.values())

env_cols = st.columns(len(REQUIRED_ENV_VARS))
for col, (key, label) in zip(env_cols, REQUIRED_ENV_VARS):
    ok = env_statuses[key]
    col.metric(label, "✅ OK" if ok else "❌ MISSING")
    col.caption(key)

if not all_env_ok:
    missing_keys = [key for key, _ in REQUIRED_ENV_VARS if not env_statuses[key]]
    st.error(
        f"누락된 환경변수: {', '.join(missing_keys)}\n\n"
        "**Render Dashboard > Environment Variables에 KIS/DART 키를 입력하세요.**"
    )

st.divider()

# ── 3. 필수 폴더 존재 여부 ────────────────────────────────────────
st.subheader("3. 필수 폴더")

if _created_dirs:
    st.success(f"자동 생성된 폴더 ({len(_created_dirs)}개): {', '.join(_created_dirs)}")

DIRS_TO_SHOW = [
    "data", "data/raw", "data/processed", "models",
    "reports", "reports/predictions", "reports/orders", "logs",
]

dir_cols = st.columns(len(DIRS_TO_SHOW))
for col, rel in zip(dir_cols, DIRS_TO_SHOW):
    exists = (PROJECT_ROOT / rel).exists()
    col.metric(rel, "✅ 있음" if exists else "❌ 없음")

st.divider()

# ── 4. 필수 패키지 import 가능 여부 ──────────────────────────────
st.subheader("4. 필수 패키지")

PACKAGES = [
    ("pandas",   "pandas"),
    ("numpy",    "numpy"),
    ("lightgbm", "lightgbm"),
    ("sklearn",  "scikit-learn"),
    ("pykrx",    "pykrx"),
    ("streamlit","streamlit"),
    ("requests", "requests"),
    ("yaml",     "pyyaml"),
    ("dotenv",   "python-dotenv"),
    ("joblib",   "joblib"),
]

pkg_results = []
for mod, label in PACKAGES:
    try:
        __import__(mod)
        pkg_results.append((label, True, ""))
    except ImportError as _ie:
        pkg_results.append((label, False, str(_ie)[:40]))

pkg_cols = st.columns(len(PACKAGES))
for col, (label, ok, err) in zip(pkg_cols, pkg_results):
    col.metric(label, "✅ OK" if ok else "❌ FAIL")
    if not ok and err:
        col.caption(err)

fail_pkgs = [label for label, ok, _ in pkg_results if not ok]
if fail_pkgs:
    st.warning(f"import 실패 패키지: {', '.join(fail_pkgs)}  —  pip install -r requirements.txt 를 실행하세요.")

st.divider()

# ── 5. reports/predictions 최신 후보 파일 ──────────────────────
st.subheader("5. 최신 후보 파일 (reports/predictions)")

preds_dir = PROJECT_ROOT / "reports" / "predictions"
if not preds_dir.exists():
    st.warning("reports/predictions 폴더가 없습니다. 위 3번 섹션에서 자동 생성합니다.")
else:
    csv_files = sorted(preds_dir.glob("top*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
    if csv_files:
        latest = csv_files[0]
        _mtime = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        st.success(f"최신 후보 파일: **{latest.name}** (수정: {_mtime}, 총 {len(csv_files)}개)")
        _file_rows = [
            {
                "파일명": f.name,
                "크기(bytes)": f"{f.stat().st_size:,}",
                "수정시각": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
            for f in csv_files[:10]
        ]
        st.dataframe(_file_rows, use_container_width=True, hide_index=True)
    else:
        st.warning(
            "후보 파일이 없습니다.  \n"
            "**AI 후보 리스트** 페이지에서 '전체 파이프라인 실행'을 클릭하세요."
        )

st.divider()

# ── 6. KIS MOCK/REAL 토큰 및 연결 상태 ────────────────────────
st.subheader("6. KIS 토큰 / 연결 상태")
st.caption("환경변수가 설정된 경우에만 토큰 상태를 표시합니다. 계좌조회 버튼은 실제 API를 호출합니다.")

try:
    from trading_service import (
        get_kis_token_status,
        check_kis_connection,
        check_kis_account,
    )

    _tab_m, _tab_r = st.tabs(["MOCK 모의투자", "REAL 실전투자"])

    with _tab_m:
        _mts = get_kis_token_status("mock")
        _mk1, _mk2, _mk3, _mk4 = st.columns(4)
        _mock_key_ok = bool(_mts.get("app_key_masked") and _mts.get("app_key_masked") != "MISSING")
        _mk1.metric("MOCK App Key", "✅ OK" if _mock_key_ok else "❌ MISSING")
        _mk2.metric("MOCK 계좌번호", "✅ OK" if _mts.get("account_masked") else "❌ MISSING")

        _mc = _mts.get("cache_exists", False)
        _me = _mts.get("is_expired", True)
        if _mc and not _me:
            _rem = _mts.get("remaining_seconds", 0)
            _mk3.metric("MOCK 토큰", "✅ 유효")
            _mk3.caption(f"잔여: {_rem // 3600}h {(_rem % 3600) // 60}m")
        elif _mc and _me:
            _mk3.metric("MOCK 토큰", "⚠️ 만료됨")
        else:
            _mk3.metric("MOCK 토큰", "❌ 없음")
        _mk4.metric("캐시 파일", "✅ 있음" if _mc else "❌ 없음")

        _mb1, _mb2 = st.columns(2)
        with _mb1:
            if st.button("MOCK 연결 확인 (삼성전자 현재가)", key="diag_mock_conn"):
                with st.spinner("MOCK 연결 확인 중..."):
                    _cr = check_kis_connection("mock")
                if _cr.get("connection_ok"):
                    st.success("MOCK 연결 OK")
                else:
                    st.error(f"MOCK 연결 실패: {_cr.get('error', '')[:150]}")
        with _mb2:
            if st.button("MOCK 계좌조회 확인", key="diag_mock_acc"):
                with st.spinner("MOCK 계좌조회 중..."):
                    _ar = check_kis_account("mock")
                if _ar.get("account_ok"):
                    st.success(f"MOCK 계좌조회 OK — {_ar.get('broker_count', 0)}개 종목")
                else:
                    _err_msg = (_ar.get("response_text") or _ar.get("error") or "")[:300]
                    st.error(f"MOCK 계좌조회 실패: {_err_msg}")

    with _tab_r:
        st.error("REAL 탭: 조회만 안전하며 주문은 실제 자금에 반영됩니다.")
        _rts = get_kis_token_status("real")
        _rk1, _rk2, _rk3, _rk4 = st.columns(4)
        _real_key_ok = bool(_rts.get("app_key_masked") and _rts.get("app_key_masked") != "MISSING")
        _rk1.metric("REAL App Key", "✅ OK" if _real_key_ok else "❌ MISSING")
        _rk2.metric("REAL 계좌번호", "✅ OK" if _rts.get("account_masked") else "❌ MISSING")

        _rc = _rts.get("cache_exists", False)
        _re = _rts.get("is_expired", True)
        if _rc and not _re:
            _rrem = _rts.get("remaining_seconds", 0)
            _rk3.metric("REAL 토큰", "✅ 유효")
            _rk3.caption(f"잔여: {_rrem // 3600}h {(_rrem % 3600) // 60}m")
        elif _rc and _re:
            _rk3.metric("REAL 토큰", "⚠️ 만료됨")
        else:
            _rk3.metric("REAL 토큰", "❌ 없음")
        _rk4.metric("캐시 파일", "✅ 있음" if _rc else "❌ 없음")

        _rb1, _rb2 = st.columns(2)
        with _rb1:
            if st.button("REAL 연결 확인", key="diag_real_conn"):
                with st.spinner("REAL 연결 확인 중..."):
                    _cr = check_kis_connection("real")
                if _cr.get("connection_ok"):
                    st.success("REAL 연결 OK")
                else:
                    st.error(f"REAL 연결 실패: {_cr.get('error', '')[:150]}")
        with _rb2:
            if st.button("REAL 계좌조회 확인", key="diag_real_acc"):
                with st.spinner("REAL 계좌조회 중..."):
                    _ar = check_kis_account("real")
                if _ar.get("account_ok"):
                    st.success(f"REAL 계좌조회 OK — {_ar.get('broker_count', 0)}개 종목")
                else:
                    _err_msg = (_ar.get("response_text") or _ar.get("error") or "")[:300]
                    st.error(f"REAL 계좌조회 실패: {_err_msg}")

except Exception as _kis_e:
    st.error(f"KIS 연결 상태 조회 실패: {_kis_e}")

st.divider()

# ── 7. 전체 진단 JSON ──────────────────────────────────────────
with st.expander("전체 진단 정보 (JSON)", expanded=False):
    _diag_json = {
        "commit_hash": commit_hash,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "project_root": str(PROJECT_ROOT),
        "cwd": str(Path.cwd()),
        "app_env": os.environ.get("APP_ENV", ""),
        "is_render": render_info["is_render"],
        "render_service_name": render_info["render_service_name"],
        "render_external_url": render_info["render_external_url"],
        "render_git_branch": render_info["render_git_branch"],
        "packages": {label: ok for label, ok, _ in pkg_results},
        "env_vars_present": {key: env_statuses[key] for key, _ in REQUIRED_ENV_VARS},
        "dirs_exist": {rel: (PROJECT_ROOT / rel).exists() for rel in DIRS_TO_SHOW},
        "dirs_auto_created": _created_dirs,
    }
    st.json(_diag_json)
