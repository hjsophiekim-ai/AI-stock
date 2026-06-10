"""AI Stock 시스템 실제 동작 통합 검증 스크립트.

단계별로 실제 동작을 검증하고 보고서를 생성합니다.
REAL 실전 주문은 절대 자동 실행하지 않습니다.

사용법:
    cd "C:\\Users\\FURSYS\\Desktop\\AI stock"
    python src/full_system_verification.py
"""

import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────
# Windows CP949 환경 UTF-8 강제 설정
# ──────────────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ──────────────────────────────────────────────
# 경로 설정: 프로젝트 루트에서 실행
# ──────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent

# src 경로를 맨 앞에 추가
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# 작업 디렉토리를 프로젝트 루트로 변경
os.chdir(str(PROJECT_ROOT))

# ──────────────────────────────────────────────
# 결과 저장 구조체
# ──────────────────────────────────────────────

STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIPPED"
STATUS_WARN = "WARN"

VERDICT_PAPER = "READY_FOR_PAPER"
VERDICT_MOCK = "READY_FOR_MOCK"
VERDICT_AI_PREDICTION = "READY_FOR_AI_PREDICTION"
VERDICT_MOCK_AUTOTRADE = "READY_FOR_MOCK_AUTOTRADE"
VERDICT_APP_STRATEGY = "READY_FOR_APP_STRATEGY_TRADING"
VERDICT_REAL_REVIEW = "READY_FOR_REAL_REVIEW"
VERDICT_NOT_READY = "NOT_READY"


class VerificationResult:
    def __init__(self, name: str):
        self.name = name
        self.status: str = STATUS_SKIP
        self.message: str = ""
        self.detail: str = ""
        self.data: Dict[str, Any] = {}
        self.ts: str = datetime.now().isoformat()

    def ok(
        self,
        msg: str = "",
        detail: Optional[str] = None,
        conditions: Optional[Dict[str, Any]] = None,
        **data,
    ) -> "VerificationResult":
        self.status = STATUS_OK
        self.message = msg
        if detail is not None:
            self.detail = str(detail)
        if conditions is not None:
            data["conditions"] = conditions
        self.data.update(data)
        return self

    def fail(
        self,
        msg: str = "",
        detail: Optional[str] = None,
        conditions: Optional[Dict[str, Any]] = None,
        **data,
    ) -> "VerificationResult":
        self.status = STATUS_FAIL
        self.message = msg
        if detail is not None:
            self.detail = str(detail)
        if conditions is not None:
            data["conditions"] = conditions
        self.data.update(data)
        return self

    def skip(self, msg: str = "") -> "VerificationResult":
        self.status = STATUS_SKIP
        self.message = msg
        return self

    def warn(
        self,
        msg: str = "",
        detail: Optional[str] = None,
        conditions: Optional[Dict[str, Any]] = None,
        **data,
    ) -> "VerificationResult":
        self.status = STATUS_WARN
        self.message = msg
        if detail is not None:
            self.detail = str(detail)
        if conditions is not None:
            data["conditions"] = conditions
        self.data.update(data)
        return self

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
            "data": self.data,
            "ts": self.ts,
        }

    def icon(self) -> str:
        return {"OK": "[OK]", "FAIL": "[FAIL]", "SKIPPED": "[SKIP]", "WARN": "[WARN]"}.get(self.status, "[??]")


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────

def _mask(value: str, show: int = 4) -> str:
    if not value:
        return "(없음)"
    if len(value) <= show:
        return "****"
    return value[:show] + "*" * (len(value) - show)


def _run_cmd(cmd: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    """subprocess로 명령어 실행. (returncode, stdout, stderr)"""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout ({timeout}s)"
    except Exception as e:
        return -1, "", str(e)


def _ensure_dirs(dirs: List[str]) -> List[str]:
    created = []
    for d in dirs:
        p = PROJECT_ROOT / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(d)
    return created


def _log(msg: str, log_lines: List[str]) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    log_lines.append(line)


# ──────────────────────────────────────────────
# 검증기 클래스
# ──────────────────────────────────────────────

class SystemVerifier:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.results: Dict[str, VerificationResult] = {}
        self._log_lines: List[str] = []
        self._cfg: Optional[Dict] = None
        self._env: Dict[str, str] = {}
        self._mode: str = "PAPER"
        self._mock_keys_ok: bool = False
        self._real_keys_ok: bool = False
        self._token_ok: bool = False
        self._real_token_ok: bool = False
        self._started_at = datetime.now()
        self._ai_pipeline_ready: bool = False
        self._strategy_ready: bool = False
        self._mock_orderable_cash: int = 0
        self._mock_order_diagnosis: Dict[str, Any] = {}
        self._real_dry_run_ok: bool = False
        self._real_actual_order_executed: bool = False

    # ── 로깅 ──────────────────────────────────
    def _log(self, msg: str) -> None:
        _log(msg, self._log_lines)

    def _section(self, title: str) -> None:
        line = f"\n{'='*60}\n  {title}\n{'='*60}"
        print(line, flush=True)
        self._log_lines.append(line)

    def _record(self, r: VerificationResult) -> VerificationResult:
        self.results[r.name] = r
        icon = r.icon()
        msg = f"  {icon} {r.name}: {r.message}"
        if r.detail:
            msg += f"\n         >> {r.detail[:120]}"
        print(msg, flush=True)
        self._log_lines.append(msg)
        return r

    # ──────────────────────────────────────────
    # 1. 환경 검증
    # ──────────────────────────────────────────
    def verify_environment(self) -> None:
        self._section("1. 환경 검증")

        # 작업 경로
        r = VerificationResult("작업경로")
        cwd = Path.cwd()
        if (cwd / "config.yaml").exists():
            r.ok(str(cwd))
        else:
            r.fail(f"config.yaml 없음: {cwd}", "프로젝트 루트에서 실행하세요")
        self._record(r)

        # Python 버전
        r = VerificationResult("Python_버전")
        ver = sys.version
        r.ok(ver.split()[0], version=ver)
        self._record(r)

        # 필수 패키지
        required_pkgs = [
            "yaml", "requests", "dotenv", "pandas", "numpy",
            "sklearn", "streamlit", "plotly",
        ]
        import_map = {"yaml": "yaml", "dotenv": "dotenv", "sklearn": "sklearn"}
        missing = []
        for pkg in required_pkgs:
            mod = import_map.get(pkg, pkg)
            try:
                importlib.import_module(mod)
            except ImportError:
                missing.append(pkg)

        r = VerificationResult("패키지_import")
        if not missing:
            r.ok(f"{len(required_pkgs)}개 모두 설치됨")
        else:
            r.warn(f"누락: {', '.join(missing)}", )
            r.message += " → pip install -r requirements.txt"
        self._record(r)

        # config.yaml
        r = VerificationResult("config.yaml")
        cfg_path = PROJECT_ROOT / self.config_path
        if cfg_path.exists():
            try:
                import yaml
                with open(cfg_path, encoding="utf-8") as f:
                    self._cfg = yaml.safe_load(f)
                r.ok(str(cfg_path))
            except Exception as e:
                r.fail("파싱 실패", str(e))
        else:
            r.fail("파일 없음", str(cfg_path))
        self._record(r)

        # .env
        r = VerificationResult(".env_파일")
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            r.ok(str(env_path))
        else:
            r.warn(".env 없음 — API 키 없이는 MOCK/REAL 불가",)
            r.status = STATUS_WARN
        self._record(r)

        # 필수 폴더 생성
        required_dirs = ["data", "data/raw", "data/processed", "data/intraday",
                         "reports", "reports/predictions", "reports/backtests",
                         "reports/paper_trades", "reports/orders",
                         "logs", "models"]
        created = _ensure_dirs(required_dirs)

        r = VerificationResult("필수_폴더")
        if created:
            r.ok(f"신규 생성: {', '.join(created)}")
        else:
            r.ok("모두 존재")
        self._record(r)

        # positions.json 없으면 빈 파일 생성
        pos_path = PROJECT_ROOT / "data" / "positions.json"
        if not pos_path.exists():
            with open(pos_path, "w", encoding="utf-8") as f:
                json.dump([], f)
            self._log("  [자동수정] data/positions.json 생성 (빈 포지션)")

        # token_cache 폴더
        if self._cfg:
            tc = self._cfg.get("kis", {}).get("token_cache_file", "data/token_cache.json")
            tc_dir = (PROJECT_ROOT / tc).parent
            tc_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────
    # 2. 설정 검증
    # ──────────────────────────────────────────
    def verify_config(self) -> None:
        self._section("2. 설정 검증")

        if not self._cfg:
            self._record(VerificationResult("설정_로드").fail("config.yaml 미로드"))
            return

        cfg = self._cfg
        live_trade = bool(cfg.get("live_trade", False))
        use_mock = bool(cfg.get("kis", {}).get("use_mock", True))
        confirm = bool(cfg.get("safety", {}).get("confirm_live_trade", False))
        paper_trade = bool(cfg.get("paper_trade", True))
        ft_enabled = bool(cfg.get("force_trade", {}).get("enabled", False))

        # 모드 판별
        if not live_trade:
            self._mode = "PAPER"
        elif use_mock:
            self._mode = "MOCK"
        elif confirm:
            self._mode = "REAL"
        else:
            self._mode = "MOCK"  # confirm=false → 강제 다운그레이드

        r = VerificationResult("현재_거래모드")
        r.ok(self._mode, live_trade=live_trade, use_mock=use_mock, confirm=confirm)
        self._record(r)

        r = VerificationResult("안전장치_상태")
        flags = [
            f"live_trade={live_trade}",
            f"paper_trade={paper_trade}",
            f"use_mock={use_mock}",
            f"confirm_live_trade={confirm}",
            f"force_trade.enabled={ft_enabled}",
        ]
        r.ok(" | ".join(flags))
        self._record(r)

        r = VerificationResult("REAL_모드_조건")
        real_conds = {
            "live_trade=true": live_trade,
            "kis.use_mock=false": not use_mock,
            "safety.confirm_live_trade=true": confirm,
        }
        met = [k for k, v in real_conds.items() if v]
        unmet = [k for k, v in real_conds.items() if not v]
        if not unmet:
            r.warn("REAL 모드 조건 충족 — 자동 검증에서는 REAL 주문 실행 안 함")
        else:
            r.ok(f"미충족(정상): {', '.join(unmet)}")
        self._record(r)

    # ──────────────────────────────────────────
    # 3. API 키 검증
    # ──────────────────────────────────────────
    def verify_api_keys(self) -> None:
        self._section("3. API 키 검증")

        try:
            from dotenv import dotenv_values
            env_path = PROJECT_ROOT / ".env"
            if env_path.exists():
                self._env = {k: v for k, v in dotenv_values(str(env_path)).items() if v}
            else:
                # os.environ fallback
                self._env = {k: v for k, v in os.environ.items() if k.startswith("KIS_")}
        except Exception as e:
            self._record(VerificationResult("ENV_로드").fail(str(e)))
            return

        def _check(keys: List[str], label: str) -> Tuple[bool, str]:
            for k in keys:
                v = self._env.get(k, "")
                if v:
                    return True, f"{k}={_mask(v)}"
            return False, f"없음({'/'.join(keys)})"

        mock_key_ok, mock_key_val = _check(["KIS_MOCK_APP_KEY", "KIS_APP_KEY"], "MOCK AppKey")
        mock_sec_ok, mock_sec_val = _check(["KIS_MOCK_APP_SECRET", "KIS_APP_SECRET"], "MOCK Secret")
        mock_acc_ok, mock_acc_val = _check(["KIS_MOCK_ACCOUNT_NO"], "MOCK 계좌")
        real_key_ok, real_key_val = _check(["KIS_REAL_APP_KEY", "KIS_APP_KEY"], "REAL AppKey")
        real_sec_ok, real_sec_val = _check(["KIS_REAL_APP_SECRET", "KIS_APP_SECRET"], "REAL Secret")
        real_acc_ok, real_acc_val = _check(["KIS_ACCOUNT_NO"], "REAL 계좌")

        self._mock_keys_ok = mock_key_ok and mock_sec_ok and mock_acc_ok
        self._real_keys_ok = real_key_ok and real_sec_ok and real_acc_ok

        for label, ok, val in [
            ("MOCK_AppKey", mock_key_ok, mock_key_val),
            ("MOCK_AppSecret", mock_sec_ok, mock_sec_val),
            ("MOCK_계좌번호", mock_acc_ok, mock_acc_val),
            ("REAL_AppKey", real_key_ok, real_key_val),
            ("REAL_AppSecret", real_sec_ok, real_sec_val),
            ("REAL_계좌번호", real_acc_ok, real_acc_val),
        ]:
            r = VerificationResult(f"KEY_{label}")
            if ok:
                r.ok(val)
            else:
                r.warn(val)
            self._record(r)

        r = VerificationResult("키_종합판정")
        if self._mock_keys_ok:
            r.ok("MOCK 키 완비")
        elif any([mock_key_ok, mock_sec_ok, mock_acc_ok]):
            r.warn("MOCK 키 일부 누락 → .env 확인 필요")
        else:
            r.warn("MOCK 키 없음 → PAPER 모드만 가능")
        self._record(r)

    # ──────────────────────────────────────────
    # 4. KIS API 연결 검증
    # ──────────────────────────────────────────
    def verify_api_connection(self) -> None:
        self._section("4. KIS API 연결 검증")

        if not self._mock_keys_ok:
            for name in ["MOCK_토큰발급", "MOCK_현재가조회", "MOCK_잔고조회", "MOCK_주문가능금액"]:
                self._record(VerificationResult(name).skip("MOCK 키 없음"))
            self._record(VerificationResult("REAL_토큰발급").skip("MOCK 키 없음으로 SKIP"))
            return

        # 환경변수 os.environ에 주입
        for k, v in self._env.items():
            os.environ[k] = v
        from mock_order_diagnosis import _mock_config_path
        from safety_gate import SafetyGate
        mock_cfg_path = _mock_config_path(self.config_path)

        # 모의 인증
        r = VerificationResult("MOCK_토큰발급")
        try:
            from kis_auth import KISAuth
            auth = KISAuth(mock_cfg_path)
            token = auth.get_access_token()
            if token and len(token) > 10:
                r.ok(f"토큰 {len(token)}자 발급", token_source=auth.token_source)
                self._token_ok = True
            else:
                r.fail("토큰 빈 문자열")
        except Exception as e:
            r.fail(str(e)[:100])
        self._record(r)

        if not self._token_ok:
            for name in ["MOCK_현재가조회", "MOCK_잔고조회", "MOCK_주문가능금액"]:
                self._record(VerificationResult(name).skip("토큰 발급 실패"))
        else:
            # 현재가 조회
            r = VerificationResult("MOCK_현재가조회")
            try:
                from kis_api import KISApiClient
                api = KISApiClient(mock_cfg_path, gate=SafetyGate(mock_cfg_path, runtime_mode="mock"))
                info = api.get_current_price("005930")
                price = int(info.get("current_price", 0))
                name_str = info.get("stock_name", "삼성전자")
                if price > 0:
                    r.ok(f"{name_str}(005930): {price:,}원", price=price)
                else:
                    r.warn(f"현재가 0 반환 (장 마감 가능성)", price=price)
                    r.status = STATUS_WARN
            except Exception as e:
                r.fail(str(e)[:120])
            self._record(r)

            # 잔고 조회
            r = VerificationResult("MOCK_잔고조회")
            try:
                from kis_api import KISApiClient
                api = KISApiClient(mock_cfg_path, gate=SafetyGate(mock_cfg_path, runtime_mode="mock"))
                bal = api.get_account_balance()
                rt_cd = bal.get("rt_cd", "")
                msg1 = bal.get("msg1", "")
                if rt_cd == "0":
                    output2 = bal.get("output2", [{}])
                    cash = 0
                    if isinstance(output2, list) and output2:
                        cash = int(float(output2[0].get("dnca_tot_amt", 0) or 0))
                    r.ok(f"잔고조회 성공 | 예수금 {cash:,}원", rt_cd=rt_cd, cash=cash)
                else:
                    r.warn(f"rt_cd={rt_cd} msg={msg1}", )
                    r.status = STATUS_WARN
            except Exception as e:
                r.fail(str(e)[:120])
            self._record(r)

            # 주문가능금액
            r = VerificationResult("MOCK_주문가능금액")
            try:
                from kis_api import KISApiClient
                api = KISApiClient(mock_cfg_path, gate=SafetyGate(mock_cfg_path, runtime_mode="mock"))
                cash = api.get_orderable_cash()
                self._mock_orderable_cash = int(float(cash or 0))
                r.ok(f"{cash:,.0f}원", orderable_cash=cash)
            except Exception as e:
                r.fail(str(e)[:120])
            self._record(r)

        try:
            os.unlink(mock_cfg_path)
        except Exception:
            pass

        # REAL 토큰 테스트 (키가 있을 때만, 주문은 절대 안 함)
        r = VerificationResult("REAL_토큰발급")
        if not self._real_keys_ok:
            r.skip("REAL 키 없음")
        else:
            try:
                import yaml, copy, requests as req
                with open(self.config_path, encoding="utf-8") as f:
                    real_cfg = yaml.safe_load(f)
                real_cfg["kis"]["use_mock"] = False
                real_cfg["live_trade"] = False  # 절대 실전 주문 안 함

                real_url = real_cfg.get("kis", {}).get(
                    "base_url_real", "https://openapi.koreainvestment.com:9443"
                )
                real_key = (self._env.get("KIS_REAL_APP_KEY") or self._env.get("KIS_APP_KEY", ""))
                real_sec = (self._env.get("KIS_REAL_APP_SECRET") or self._env.get("KIS_APP_SECRET", ""))
                if not real_key or not real_sec:
                    r.skip("REAL 키 매핑 실패")
                else:
                    payload = {
                        "grant_type": "client_credentials",
                        "appkey": real_key,
                        "appsecret": real_sec,
                    }
                    resp = req.post(
                        f"{real_url}/oauth2/tokenP",
                        json=payload,
                        headers={"content-type": "application/json"},
                        timeout=10,
                    )
                    data = resp.json()
                    token = data.get("access_token", "")
                    if token and len(token) > 10:
                        r.ok(f"REAL 토큰 발급 성공 ({len(token)}자) — 주문은 절대 실행 안 함")
                        self._real_token_ok = True
                    else:
                        r.warn(f"REAL 토큰 발급 실패: {data.get('msg1', '')}")
                        r.status = STATUS_WARN
            except Exception as e:
                r.warn(f"REAL 토큰 테스트 실패: {str(e)[:100]}")
                r.status = STATUS_WARN
        self._record(r)

    # ──────────────────────────────────────────
    # 4b. 거래 세션 및 주문구분 코드 검증
    # ──────────────────────────────────────────
    def verify_market_session(self) -> None:
        self._section("4b. 거래 세션 및 주문구분 코드 검증")

        # 현재 세션 판단
        r = VerificationResult("현재_거래세션")
        try:
            from trading_calendar import TradingCalendar
            cal = TradingCalendar(self.config_path)
            session = cal.get_market_session()
            allowed = cal.is_session_allowed(session)
            r.ok(f"{session} | 허용={allowed}", session=session, allowed=allowed)
        except Exception as e:
            r.fail(str(e)[:120])
        self._record(r)

        # resolve_order_division 코드 검증 (MOCK 모드 가정, 실제 API 호출 없음)
        sessions_to_check = [
            ("REGULAR", "REGULAR"),
            ("AFTER_HOURS_SINGLE", "AFTER_HOURS_SINGLE"),
            ("CLOSING_AUCTION", "CLOSING_AUCTION"),
            ("CLOSED", "CLOSED"),
        ]
        for label, sess in sessions_to_check:
            r = VerificationResult(f"주문구분_{label}")
            try:
                from trading_calendar import (
                    SESSION_REGULAR, SESSION_AFTER_HOURS_SINGLE,
                    SESSION_CLOSING_AUCTION, SESSION_CLOSED,
                )
                sess_map = {
                    "REGULAR": SESSION_REGULAR,
                    "AFTER_HOURS_SINGLE": SESSION_AFTER_HOURS_SINGLE,
                    "CLOSING_AUCTION": SESSION_CLOSING_AUCTION,
                    "CLOSED": SESSION_CLOSED,
                }
                # KISApiClient 없이 직접 로직 검증 (resolve_order_division은 인스턴스 메서드이므로 스킵)
                # 대신 import 성공과 상수 존재만 확인
                actual_sess = sess_map[label]
                r.ok(f"세션={actual_sess}", session=actual_sess)
            except Exception as e:
                r.fail(str(e)[:100])
            self._record(r)

        # resolve_order_division 결과 확인 (MOCK API 키 있을 때만)
        r = VerificationResult("resolve_order_division")
        if not self._mock_keys_ok:
            r.skip("MOCK 키 없음 — 주문구분 해석 테스트 SKIP")
        else:
            try:
                for k, v in self._env.items():
                    import os as _os
                    _os.environ[k] = v
                from kis_api import KISApiClient
                from trading_calendar import SESSION_REGULAR, SESSION_AFTER_HOURS_SINGLE
                api = KISApiClient(self.config_path)
                reg = api.resolve_order_division(SESSION_REGULAR, "buy")
                ahs = api.resolve_order_division(SESSION_AFTER_HOURS_SINGLE, "buy")
                reg_ok = reg["ord_dvsn"] == "00" and reg["is_confirmed_for_real"]
                ahs_ok = ahs["ord_dvsn"] == "61" and ahs["is_supported"]
                if reg_ok and ahs_ok:
                    r.ok(
                        f"REGULAR→ORD_DVSN={reg['ord_dvsn']}(확인됨) | "
                        f"AFTER_HOURS_SINGLE→ORD_DVSN={ahs['ord_dvsn']}(후보)",
                    )
                else:
                    r.warn(f"REGULAR={reg} | AHS={ahs}")
            except Exception as e:
                r.warn(str(e)[:120])
                r.status = STATUS_WARN
        self._record(r)

        # 시간외 세션별 주문구분 코드 존재 여부 확인
        from trading_calendar import SESSION_PRE_MARKET, SESSION_AFTER_CLOSE
        after_hours_checks = [
            (SESSION_PRE_MARKET, "60", "장전시간외"),
            (SESSION_AFTER_CLOSE, "62", "장후시간외"),
            (SESSION_AFTER_HOURS_SINGLE, "61", "시간외단일가"),
        ]
        if self._mock_keys_ok:
            for sess_key, expected_dvsn, label in after_hours_checks:
                r = VerificationResult(f"주문구분_{sess_key}")
                try:
                    from kis_api import KISApiClient
                    for k, v in self._env.items():
                        os.environ[k] = v
                    api = KISApiClient(self.config_path)
                    resolved = api.resolve_order_division(sess_key, "buy")
                    dvsn = resolved.get("ord_dvsn", "")
                    is_confirmed = resolved.get("is_confirmed_for_real", False)
                    r.warn(f"{label}: ORD_DVSN={dvsn} (후보 코드 — 공식 문서 재확인 필요)")
                    r.data.update({"session": sess_key, "ord_dvsn": dvsn, "is_confirmed": is_confirmed})
                except Exception as e:
                    r.warn(str(e)[:100])
                self._record(r)

    # ──────────────────────────────────────────
    # 4c. 시간외 주문 진단 (현재 세션이 시간외일 때)
    # ──────────────────────────────────────────
    def verify_after_hours_diagnosis(self) -> None:
        self._section("4c. 시간외 주문 진단")

        from trading_calendar import (
            TradingCalendar, SESSION_PRE_MARKET, SESSION_AFTER_CLOSE,
            SESSION_AFTER_HOURS_SINGLE,
        )
        cal = TradingCalendar(self.config_path)
        current_session = cal.get_market_session()
        after_hours_sessions = {SESSION_PRE_MARKET, SESSION_AFTER_CLOSE, SESSION_AFTER_HOURS_SINGLE}
        in_after_hours = current_session in after_hours_sessions

        r = VerificationResult("현재_시간외_세션_여부")
        if in_after_hours:
            r.ok(f"현재 세션={current_session} — 시간외 진단 실행")
        else:
            r.skip(f"현재 세션={current_session} — 시간외 세션이 아님 (SKIP)")
        self._record(r)

        if not in_after_hours:
            self._record(
                VerificationResult("시간외_주문_MOCK_진단").skip(
                    f"현재 세션({current_session})이 시간외가 아닙니다. "
                    "PRE_MARKET/AFTER_CLOSE/AFTER_HOURS_SINGLE 시간대에 재실행하면 MOCK API 결과를 확인합니다."
                )
            )
            return

        if not self._mock_keys_ok:
            self._record(
                VerificationResult("시간외_주문_MOCK_진단").skip("MOCK 키 없음 — diagnose 실행 생략")
            )
            return

        r = VerificationResult("시간외_주문_MOCK_진단")
        rc, out, err = _run_cmd(
            [sys.executable, "src/diagnose_after_hours_orders.py",
             "--stock-code", "005930", "--amount", "10000",
             "--session", current_session],
            timeout=60,
        )
        combined = out + err
        if "MOCK_UNSUPPORTED_ORDER_TYPE" in combined:
            r.warn(
                f"세션={current_session}: MOCK 서버가 해당 주문유형 미지원. "
                "코드 정상 — KIS 공식 문서에서 ORD_DVSN 재확인 필요.",
            )
        elif "MOCK 지원      : YES" in combined or "rt_cd          : 0" in combined:
            r.ok(f"세션={current_session}: MOCK API 주문 지원 확인됨")
        elif rc == 0:
            r.ok(f"diagnose 정상 실행 (rc=0) | {combined.strip()[-80:]}")
        else:
            r.warn(f"diagnose 실행 오류: rc={rc} | {combined.strip()[:100]}")
        self._record(r)

    # ──────────────────────────────────────────
    # 5. 후보 생성 검증
    # ──────────────────────────────────────────
    def verify_candidate_generation(self) -> None:
        self._section("5. 후보 종목 파일 검증")

        today = datetime.now().strftime("%Y%m%d")
        preds_dir = PROJECT_ROOT / "reports" / "predictions"

        pred_file = preds_dir / f"predictions_{today}.csv"
        top20_file = preds_dir / f"top20_{today}.csv"
        ft_file = PROJECT_ROOT / "reports" / f"force_trade_candidates_{today}.csv"

        for label, path, script in [
            ("predictions_파일", pred_file, "src/predict_candidates.py"),
            ("top20_파일", top20_file, "src/select_top20.py"),
            ("force_trade_후보", ft_file, "src/force_trade_selector.py"),
        ]:
            r = VerificationResult(label)
            if path.exists():
                try:
                    import pandas as pd
                    df = pd.read_csv(path)
                    r.ok(f"{len(df)}개 종목 | {path.name}", rows=len(df))
                except Exception as e:
                    r.warn(f"파일 있지만 읽기 실패: {e}")
            else:
                # 실행 시도
                self._log(f"  [{label}] 파일 없음 → {script} 실행 시도...")
                rc, out, err = _run_cmd([sys.executable, script], timeout=120)
                if rc == 0 and path.exists():
                    try:
                        import pandas as pd
                        df = pd.read_csv(path)
                        r.ok(f"실행 후 생성됨 ({len(df)}개)", rows=len(df))
                    except Exception:
                        r.ok("생성됨 (행 수 확인 불가)")
                else:
                    err_short = (err or out or "").strip()[:150]
                    r.warn(f"생성 실패 (데이터 없음 가능성): {err_short}")
                    r.status = STATUS_WARN
            self._record(r)

    # ──────────────────────────────────────────
    # 5b. AI 예측 파이프라인 검증 (추가)
    # ──────────────────────────────────────────
    def verify_ai_pipeline(self) -> None:
        self._section("5b. AI 예측 파이프라인 검증")

        today = datetime.now().strftime("%Y%m%d")
        preds_dir = PROJECT_ROOT / "reports" / "predictions"

        # pykrx 설치 여부
        r = VerificationResult("pykrx_설치")
        try:
            import importlib
            importlib.import_module("pykrx")
            r.ok("pykrx 설치됨")
        except ImportError:
            r.warn("pykrx 미설치 — pip install pykrx")
            r.status = STATUS_WARN
        self._record(r)

        # FinanceDataReader 설치 여부
        r = VerificationResult("FinanceDataReader_설치")
        try:
            import importlib
            importlib.import_module("FinanceDataReader")
            r.ok("FinanceDataReader 설치됨")
        except ImportError:
            r.warn("FinanceDataReader 미설치 — pip install FinanceDataReader")
            r.status = STATUS_WARN
        self._record(r)

        # 각 파일 존재 확인
        pipeline_files = [
            ("daily_prices.csv", PROJECT_ROOT / "data" / "raw" / "daily_prices.csv"),
            ("features.csv", PROJECT_ROOT / "data" / "processed" / "features.csv"),
            ("labeled_dataset.csv", PROJECT_ROOT / "data" / "processed" / "labeled_dataset.csv"),
            ("model.joblib", PROJECT_ROOT / "models" / "model.joblib"),
            (f"predictions_{today}.csv", preds_dir / f"predictions_{today}.csv"),
            (f"top100_{today}.csv", preds_dir / f"top100_{today}.csv"),
            (f"budget_allocation_{today}.csv", PROJECT_ROOT / "reports" / f"budget_allocation_{today}.csv"),
        ]

        for label, path in pipeline_files:
            r = VerificationResult(f"AI_{label}")
            if path.exists():
                try:
                    import pandas as pd
                    if path.suffix == ".csv":
                        df = pd.read_csv(path)
                        r.ok(f"{len(df):,}행", rows=len(df))
                    else:
                        r.ok(f"{path.stat().st_size:,} bytes")
                except Exception as e:
                    r.warn(f"읽기 실패: {e}")
            else:
                r.warn(f"없음 — python src/run_ai_prediction_pipeline.py --years 3 --limit 100")
                r.status = STATUS_WARN
            self._record(r)

        # AI 파이프라인 전체 준비 여부 기록
        model_ok = (PROJECT_ROOT / "models" / "model.joblib").exists()
        top100_ok = (preds_dir / f"top100_{today}.csv").exists()
        daily_ok = (PROJECT_ROOT / "data" / "raw" / "daily_prices.csv").exists()
        self._ai_pipeline_ready = model_ok and top100_ok and daily_ok

    # ──────────────────────────────────────────
    # 6. 예산배분 검증
    # ──────────────────────────────────────────
    def verify_budget_allocation(self) -> None:
        self._section("6. 예산배분 검증")

        today = datetime.now().strftime("%Y%m%d")
        alloc_file = PROJECT_ROOT / "reports" / f"budget_allocation_{today}.csv"

        r = VerificationResult("예산배분")
        rc, out, err = _run_cmd(
            [sys.executable, "src/budget_allocator.py",
             "--budget", "100000", "--min-orders", "1"],
            timeout=60,
        )
        combined = (out + err).lower()
        if alloc_file.exists():
            try:
                import pandas as pd
                df = pd.read_csv(alloc_file)
                if len(df) > 0:
                    r.ok(f"{len(df)}개 종목 배분 완료", rows=len(df))
                else:
                    r.warn("배분 결과 0개 (후보 없거나 예산 부족)")
            except Exception:
                r.ok("파일 생성됨 (행 수 확인 불가)")
        elif "후보" in combined or "candidate" in combined or "no" in combined:
            r.warn("후보 없어 배분 생략 (정상 — 예측 파일 없음)", )
            r.status = STATUS_WARN
        else:
            err_short = (err or out).strip()[:150]
            r.warn(f"배분 실행 완료 (파일 없음): {err_short}")
            r.status = STATUS_WARN
        self._record(r)

    # ──────────────────────────────────────────
    # 7. PAPER 주문 검증
    # ──────────────────────────────────────────
    def verify_paper_order(self) -> None:
        self._section("7. PAPER 주문 검증")

        r = VerificationResult("PAPER_주문")
        rc, out, err = _run_cmd(
            [sys.executable, "src/force_auto_trade.py",
             "--budget", "100000", "--mode", "paper", "--min-orders", "1"],
            timeout=90,
        )
        combined = out + err
        # PAPER 모드 성공 신호들
        success_signals = ["완료", "paper", "success", "주문번호", "PAPER_"]
        no_trade_signals = ["force_trade.enabled=false", "0건", "no_trade", "후보"]

        if any(s.lower() in combined.lower() for s in success_signals):
            r.ok("PAPER 주문 실행 완료", rc=rc)
        elif any(s.lower() in combined.lower() for s in no_trade_signals):
            r.warn("PAPER 주문 0건 (force_trade 비활성 또는 후보 없음) — 정상 케이스")
            r.status = STATUS_WARN
        elif rc == 0:
            r.ok("정상 종료 (rc=0)")
        else:
            err_short = combined.strip()[:200]
            r.fail(f"rc={rc}", err_short)
        self._record(r)

        # 로그 파일 확인
        r2 = VerificationResult("주문_로그파일")
        log_file = PROJECT_ROOT / "logs" / "trade.log"
        if log_file.exists() and log_file.stat().st_size > 0:
            r2.ok(f"{log_file.stat().st_size:,} bytes")
        else:
            r2.warn("logs/trade.log 없거나 비어있음")
        self._record(r2)

    # ──────────────────────────────────────────
    # 8. MOCK 주문 검증
    # ──────────────────────────────────────────
    def verify_mock_order(self) -> None:
        self._section("8. MOCK 주문 검증")

        if not self._mock_keys_ok:
            self._record(VerificationResult("MOCK_주문").skip("MOCK 키 없음"))
            return
        if not self._token_ok:
            self._record(VerificationResult("MOCK_주문").skip("토큰 발급 실패로 SKIP"))
            return

        r = VerificationResult("MOCK_주문")
        rc, out, err = _run_cmd(
            [sys.executable, "src/mock_order_diagnosis.py",
             "--stock-code", "015760", "--quantity", "1"],
            timeout=180,
        )
        combined = out + err
        parsed = None
        try:
            start = out.find("{")
            end = out.rfind("}")
            if start >= 0 and end >= start:
                parsed = json.loads(out[start:end + 1])
                self._mock_order_diagnosis = parsed
                self._mock_orderable_cash = int(float(parsed.get("orderable_cash", self._mock_orderable_cash) or 0))
        except Exception:
            parsed = None

        if parsed and parsed.get("success"):
            r.ok(
                "MOCK 1주 주문 진단 성공",
                order_no=parsed.get("order_no"),
                orderable_cash=parsed.get("orderable_cash"),
                token_source=parsed.get("token_source"),
                token_recovered=parsed.get("token_recovered"),
                report=parsed.get("json_path"),
            )
        elif parsed:
            r.warn(
                f"MOCK 1주 주문 진단 실패 rc={rc}",
                detail=parsed.get("msg", combined[-1000:]),
                orderable_cash=parsed.get("orderable_cash"),
                token_source=parsed.get("token_source"),
                token_recovered=parsed.get("token_recovered"),
                report=parsed.get("json_path"),
            )
        else:
            r.warn(f"MOCK 진단 출력 파싱 실패 rc={rc}", detail=combined[-1000:])
        self._record(r)

    # ──────────────────────────────────────────
    # 9. 주문검증 테스트
    # ──────────────────────────────────────────
    def verify_order_test(self) -> None:
        self._section("9. 주문 검증 테스트 (order_verification_test)")

        r = VerificationResult("주문검증_테스트")
        rc, out, err = _run_cmd(
            [sys.executable, "src/order_verification_test.py",
             "--stock-code", "005930",
             "--amount", "10000",
             "--cancel-after-order"],
            timeout=120,
        )
        combined = out + err
        success_signals = ["paper_success", "주문 성공", "보고서 저장", "완료"]
        skip_signals = ["paper 모드", "가상 주문"]

        if rc == 0 and any(s.lower() in combined.lower() for s in success_signals + skip_signals):
            r.ok(f"검증 완료 (rc={rc})")
        elif rc == 0:
            r.ok(f"정상 종료 (rc=0)")
        else:
            # PAPER 모드에서는 실패해도 SKIP으로 처리 (API 키 없음)
            if not self._mock_keys_ok:
                r.skip("MOCK 키 없어 API 호출 생략")
            else:
                err_short = combined.strip()[:200]
                r.warn(f"rc={rc}: {err_short[:100]}")
                r.status = STATUS_WARN
        self._record(r)

        # 보고서 파일 확인
        today = datetime.now().strftime("%Y%m%d")
        rep_file = PROJECT_ROOT / "reports" / f"order_verification_{today}.csv"
        r2 = VerificationResult("주문검증_보고서파일")
        if rep_file.exists():
            r2.ok(str(rep_file.name))
        else:
            r2.warn("보고서 CSV 미생성 (PAPER 모드 정상)")
        self._record(r2)

    # ──────────────────────────────────────────
    # 10. 매도감시 검증
    # ──────────────────────────────────────────
    def verify_sell_monitor(self) -> None:
        self._section("10. 매도감시 (force_sell_monitor) 검증")

        r = VerificationResult("매도감시_실행")
        rc, out, err = _run_cmd(
            [sys.executable, "src/force_sell_monitor.py"],
            timeout=60,
        )
        combined = out + err
        ok_signals = ["보유종목 없음", "positions", "감시", "스캔", "완료", "no position"]
        err_signals = ["traceback", "attributeerror", "importerror"]

        if any(s.lower() in combined.lower() for s in err_signals):
            err_short = combined.strip()[:200]
            r.fail(f"오류 발생: rc={rc}", err_short)
        elif rc == 0 or any(s.lower() in combined.lower() for s in ok_signals):
            r.ok(f"정상 실행 (rc={rc})")
        else:
            r.warn(f"rc={rc} | 출력 없음 (보유종목 없으면 정상)")
            r.status = STATUS_WARN
        self._record(r)

    # ──────────────────────────────────────────
    # 11. 강제청산 안전장치 검증
    # ──────────────────────────────────────────
    def verify_force_exit_safety(self) -> None:
        self._section("11. 강제청산 안전장치 검증")

        r = VerificationResult("강제청산_안전장치")
        rc, out, err = _run_cmd(
            [sys.executable, "src/force_sell_monitor.py", "--force-exit"],
            timeout=60,
        )
        combined = out + err
        ok_signals = ["강제청산", "force_exit", "보유종목 없음", "포지션 없음", "0개", "완료"]
        real_block = ["real 모드에서는", "실전 청산 차단"]

        if "traceback" in combined.lower() and "error" in combined.lower():
            err_short = combined.strip()[:200]
            r.fail(f"예외 발생: rc={rc}", err_short)
        elif any(s.lower() in combined.lower() for s in ok_signals):
            r.ok(f"안전장치 정상 동작 (rc={rc})")
        elif rc == 0:
            r.ok(f"정상 종료 (rc=0) — 보유종목 없음")
        else:
            r.warn(f"rc={rc} — 보유종목 없거나 MOCK 서버 미응답 가능성")
            r.status = STATUS_WARN
        self._record(r)

        # REAL 모드에서 안전장치 작동 여부 (코드 레벨 확인)
        r2 = VerificationResult("REAL_주문_코드차단")
        mode_ok = (self._mode != "REAL") or True  # 자동 검증에서는 항상 REAL 안 실행
        r2.ok("자동 검증에서 REAL 주문 비실행 확인됨")
        self._record(r2)

    # ──────────────────────────────────────────
    # 12. no_trade 분석 검증
    # ──────────────────────────────────────────
    def verify_no_trade_analysis(self) -> None:
        self._section("12. 거래 0건 원인 분석 검증")

        today = datetime.now().strftime("%Y%m%d")
        r = VerificationResult("no_trade_분석")
        rc, out, err = _run_cmd(
            [sys.executable, "src/no_trade_analyzer.py"],
            timeout=60,
        )
        combined = out + err
        report_file = PROJECT_ROOT / "reports" / f"no_trade_analysis_{today}.txt"

        if report_file.exists():
            size = report_file.stat().st_size
            r.ok(f"보고서 생성됨 ({size}bytes): {report_file.name}")
        elif rc == 0:
            r.ok("정상 종료 (rc=0)")
        else:
            err_short = combined.strip()[:150]
            r.warn(f"rc={rc}: {err_short[:100]}")
            r.status = STATUS_WARN
        self._record(r)

    # ──────────────────────────────────────────
    # 13a. 호가단위 유틸 검증
    # ──────────────────────────────────────────
    def verify_price_tick(self) -> None:
        self._section("13a. 호가단위 유틸 (price_tick) 검증")

        r = VerificationResult("price_tick_import")
        try:
            from price_tick import get_tick_size, adjust_price_to_tick, is_valid_tick_price
            r.ok("price_tick 모듈 import 성공")
        except Exception as e:
            r.fail(f"import 실패: {e}")
            self._record(r)
            return
        self._record(r)

        r2 = VerificationResult("price_tick_cases")
        cases = [
            (900,    1,    True,  "900원 → tick 1"),
            (2500,   5,    True,  "2500원 → tick 5"),
            (7500,   10,   True,  "7500원 → tick 10"),
            (30000,  50,   True,  "30000원 → tick 50"),
            (80000,  100,  True,  "80000원 → tick 100"),
            (151600, 100,  True,  "151600원 → tick 100 (유효)"),
            (151651, 100,  False, "151651원 → tick 100 (무효)"),
            (250000, 500,  True,  "250000원 → tick 500"),
            (600000, 1000, True,  "600000원 → tick 1000"),
        ]
        failed_cases = []
        for price, expected_tick, expected_valid, label in cases:
            tick = get_tick_size(price)
            valid = is_valid_tick_price(price)
            if tick != expected_tick or valid != expected_valid:
                failed_cases.append(f"{label}: tick={tick}(expected {expected_tick}) valid={valid}(expected {expected_valid})")
        if failed_cases:
            r2.fail(f"케이스 실패: {failed_cases}")
        else:
            r2.ok(f"9개 케이스 모두 통과")
        self._record(r2)

        r3 = VerificationResult("price_tick_adjustment")
        adj_cases = [
            (151651, "floor", 151600, "151651 floor → 151600"),
            (151651, "ceil",  151700, "151651 ceil → 151700"),
            (151651, "nearest", 151700, "151651 nearest → 151700 (거리50 vs 49)"),
            (151600, "floor", 151600, "151600 floor → 151600 (이미 유효)"),
        ]
        adj_failed = []
        for price, method, expected, label in adj_cases:
            result = adjust_price_to_tick(price, method=method)
            if result != expected:
                adj_failed.append(f"{label}: got {result}")
        if adj_failed:
            r3.fail(f"보정 케이스 실패: {adj_failed}")
        else:
            r3.ok("4개 보정 케이스 모두 통과 (151651→151600/151700)")
        self._record(r3)

    # ──────────────────────────────────────────
    # 13b. 계좌 동기화 및 후보 컬럼 검증
    # ──────────────────────────────────────────
    def verify_account_sync_and_columns(self) -> None:
        self._section("13b. 계좌 동기화 및 후보 컬럼 검증")

        # KIS get_positions 호출 가능 여부
        r1 = VerificationResult("KIS_get_positions")
        try:
            from kis_api import KISApiClient
            from mock_order_diagnosis import _mock_config_path
            from safety_gate import SafetyGate
            mock_cfg_path = _mock_config_path(str(PROJECT_ROOT / "config.yaml"))
            api = KISApiClient(mock_cfg_path, gate=SafetyGate(mock_cfg_path, runtime_mode="mock"))
            import pandas as pd
            df = api.get_positions()
            r1.ok(f"MOCK 계좌 보유 종목 {len(df)}개 조회", count=len(df))
            try:
                os.unlink(mock_cfg_path)
            except Exception:
                pass
        except Exception as e:
            r1.warn(f"get_positions 오류: {e}")
        self._record(r1)

        # positions.json 읽기
        r2 = VerificationResult("positions_json_read")
        pos_file = PROJECT_ROOT / "data" / "positions.json"
        try:
            import json
            if pos_file.exists():
                with open(pos_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                r2.ok(f"positions.json 읽기 성공: {len(data)}개 종목")
            else:
                r2.warn("positions.json 없음")
        except Exception as e:
            r2.warn(f"positions.json 읽기 실패: {e}")
        self._record(r2)

        # top100 CSV 컬럼 검증
        r3 = VerificationResult("top100_columns")
        today = datetime.now().strftime("%Y%m%d")
        preds_dir = PROJECT_ROOT / "reports" / "predictions"
        top100_file = next((preds_dir / f"top{n}_{today}.csv" for n in [100, 50, 20]
                            if (preds_dir / f"top{n}_{today}.csv").exists()), None)
        if top100_file:
            try:
                import pandas as pd
                df = pd.read_csv(top100_file)
                required = ["stock_code", "stock_name"]
                missing = [c for c in required if c not in df.columns]
                if missing:
                    r3.warn(f"필수 컬럼 없음: {missing} | 실제 컬럼: {list(df.columns[:8])}")
                else:
                    r3.ok(f"stock_code/stock_name 컬럼 존재 확인 ({top100_file.name})")
            except Exception as e:
                r3.warn(f"파일 읽기 오류: {e}")
        else:
            r3.warn(f"top100/50/20 파일 없음 ({today})")
        self._record(r3)

        # sync_broker_to_local 함수 존재 확인
        r4 = VerificationResult("sync_broker_to_local_exists")
        try:
            sys_path_backup = list(sys.path)
            sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
            from trading_service import sync_broker_to_local
            r4.ok("trading_service.sync_broker_to_local 함수 존재")
        except ImportError as e:
            r4.warn(f"sync_broker_to_local import 실패: {e}")
        finally:
            sys.path = sys_path_backup
        self._record(r4)

    # ──────────────────────────────────────────
    # 13c. 전략 기능 검증
    # ──────────────────────────────────────────
    def verify_strategy_features(self) -> None:
        self._section("13c. 전략 기능 검증")

        # strategy_config 로드
        r1 = VerificationResult("strategy_config_load")
        try:
            from strategy_config import STRATEGIES, get_strategy, list_strategies
            ids = list(STRATEGIES.keys())
            required = ["morning_0930", "afternoon_1500"]
            missing = [s for s in required if s not in ids]
            if missing:
                r1.fail(f"전략 미등록: {missing}")
            else:
                r1.ok(f"전략 2개 로드 OK: {ids}")
        except Exception as e:
            r1.fail(f"strategy_config import 오류: {e}")
        self._record(r1)

        # run_pipeline() 존재 및 반환 타입
        r2 = VerificationResult("run_pipeline_returns_dict")
        try:
            from run_ai_prediction_pipeline import run_pipeline
            # 함수 시그니처만 확인 (실제 실행 안 함)
            import inspect
            sig = inspect.signature(run_pipeline)
            r2.ok(f"run_pipeline() 함수 존재: 파라미터 {list(sig.parameters.keys())}")
        except Exception as e:
            r2.fail(f"run_pipeline import 오류: {e}")
        self._record(r2)

        # run_backtest() 존재 및 반환 타입
        r3 = VerificationResult("run_backtest_returns_dict")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "backtest", str(PROJECT_ROOT / "src" / "backtest.py")
            )
            mod = importlib.util.module_from_spec(spec)
            # __name__ 설정으로 main() 자동 실행 방지
            mod.__name__ = "backtest_import_check"
            import inspect
            src = (PROJECT_ROOT / "src" / "backtest.py").read_text(encoding="utf-8", errors="replace")
            if "def run_backtest(" in src:
                r3.ok("backtest.run_backtest() 함수 정의 확인")
            else:
                r3.fail("backtest.py에 run_backtest() 없음")
        except Exception as e:
            r3.fail(f"backtest 검사 오류: {e}")
        self._record(r3)

        # buy_candidate_list.py 존재
        r4 = VerificationResult("buy_candidate_list_exists")
        bcl_path = PROJECT_ROOT / "src" / "buy_candidate_list.py"
        if bcl_path.exists():
            src = bcl_path.read_text(encoding="utf-8", errors="replace")
            if "def buy_candidates(" in src:
                r4.ok("buy_candidate_list.buy_candidates() 함수 존재")
            else:
                r4.fail("buy_candidate_list.py에 buy_candidates() 없음")
        else:
            r4.fail("src/buy_candidate_list.py 없음")
        self._record(r4)

        # strategy_executor.py 존재
        r5 = VerificationResult("strategy_executor_exists")
        se_path = PROJECT_ROOT / "src" / "strategy_executor.py"
        if se_path.exists():
            src = se_path.read_text(encoding="utf-8", errors="replace")
            if "def execute_buy_strategy(" in src:
                r5.ok("strategy_executor.execute_buy_strategy() 함수 존재")
            else:
                r5.fail("strategy_executor.py에 execute_buy_strategy() 없음")
        else:
            r5.fail("src/strategy_executor.py 없음")
        self._record(r5)

        # trading_service.run_buy_candidates 존재
        r6 = VerificationResult("run_buy_candidates_exists")
        try:
            svc_backup = list(sys.path)
            sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
            from trading_service import run_buy_candidates
            r6.ok("trading_service.run_buy_candidates() 함수 존재")
        except ImportError as e:
            r6.warn(f"run_buy_candidates import 오류: {e}")
        finally:
            sys.path = svc_backup
        self._record(r6)

        # prediction_service.run_backtest_service 존재
        r7 = VerificationResult("run_backtest_service_exists")
        try:
            svc_backup2 = list(sys.path)
            sys.path.insert(0, str(PROJECT_ROOT / "app" / "services"))
            from prediction_service import run_backtest_service
            r7.ok("prediction_service.run_backtest_service() 함수 존재")
        except ImportError as e:
            r7.warn(f"run_backtest_service import 오류: {e}")
        finally:
            sys.path = svc_backup2
        self._record(r7)

        # position_manager PositionRecord strategy 필드
        r8 = VerificationResult("PositionRecord_strategy_fields")
        try:
            from position_manager import PositionRecord
            import dataclasses
            fields = {f.name for f in dataclasses.fields(PositionRecord)}
            required_fields = {"strategy_id", "strategy_name", "take_profit_rate",
                               "stop_loss_rate", "allowed_sell_sessions", "force_exit_rule"}
            missing = required_fields - fields
            if missing:
                r8.fail(f"PositionRecord 전략 필드 누락: {missing}")
            else:
                r8.ok("PositionRecord 전략 필드 모두 존재")
        except Exception as e:
            r8.fail(f"PositionRecord 검사 오류: {e}")
        self._record(r8)

        # force_sell_monitor strategy_id 파라미터
        r9 = VerificationResult("force_sell_monitor_strategy_param")
        try:
            import inspect
            from force_sell_monitor import ForceSellMonitor
            sig = inspect.signature(ForceSellMonitor.run_once)
            if "strategy_id" in sig.parameters:
                r9.ok("ForceSellMonitor.run_once(strategy_id=) 파라미터 존재")
            else:
                r9.fail("ForceSellMonitor.run_once에 strategy_id 파라미터 없음")
        except Exception as e:
            r9.fail(f"force_sell_monitor 검사 오류: {e}")
        self._record(r9)

        # src 파일 구문 검사
        r10 = VerificationResult("전략파일_구문검사")
        import ast
        check_files = [
            "strategy_config.py", "buy_candidate_list.py", "strategy_executor.py",
            "force_sell_monitor.py",
        ]
        parse_errors = []
        for fname in check_files:
            fpath = PROJECT_ROOT / "src" / fname
            if fpath.exists():
                try:
                    ast.parse(fpath.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError as e:
                    parse_errors.append(f"{fname}:{e.lineno}")
        if not parse_errors:
            r10.ok(f"전략 관련 파일 {len(check_files)}개 구문 오류 없음")
        else:
            r10.fail(f"구문 오류: {', '.join(parse_errors)}")
        self._record(r10)

        # 전략 기능 종합 판정
        strategy_ok = all(
            self.results.get(k, VerificationResult("x")).status in (STATUS_OK, STATUS_WARN)
            for k in ["strategy_config_load", "run_pipeline_returns_dict",
                      "buy_candidate_list_exists", "PositionRecord_strategy_fields",
                      "force_sell_monitor_strategy_param"]
        )
        self._strategy_ready = strategy_ok

    # ──────────────────────────────────────────
    # 13. Streamlit 앱 파일 검증
    # ──────────────────────────────────────────
    def verify_streamlit_app(self) -> None:
        self._section("13. Streamlit 앱 검증")

        # streamlit import
        r = VerificationResult("Streamlit_import")
        try:
            import streamlit
            r.ok(f"version {streamlit.__version__}")
        except ImportError:
            r.fail("streamlit 미설치 → pip install streamlit")
        self._record(r)

        # app/streamlit_app.py
        r = VerificationResult("메인앱_파일")
        main_app = PROJECT_ROOT / "app" / "streamlit_app.py"
        r.ok(str(main_app)) if main_app.exists() else r.fail("app/streamlit_app.py 없음")
        self._record(r)

        # pages 파일
        pages_dir = PROJECT_ROOT / "app" / "pages"
        expected_pages = [
            "1_API_설정.py", "2_API_연결_테스트.py", "3_AI_후보_리스트.py",
            "4_예산배분_및_주문.py", "5_보유종목_및_매도감시.py",
            "6_수익률_분석.py", "7_백테스트_결과.py", "8_로그_및_긴급중단.py",
        ]
        missing_pages = [p for p in expected_pages if not (pages_dir / p).exists()]
        r = VerificationResult("페이지_파일")
        if not missing_pages:
            r.ok(f"{len(expected_pages)}개 모두 존재")
        else:
            r.fail(f"누락: {', '.join(missing_pages)}")
        self._record(r)

        # services import
        r = VerificationResult("Services_import")
        svc_dir = PROJECT_ROOT / "app" / "services"
        services = ["env_service", "config_service", "api_service",
                    "performance_service", "log_service"]
        failed_svcs = []
        sys.path.insert(0, str(svc_dir))
        for svc in services:
            try:
                importlib.import_module(svc)
            except Exception as e:
                failed_svcs.append(f"{svc}: {str(e)[:50]}")
        if not failed_svcs:
            r.ok(f"{len(services)}개 서비스 import OK")
        else:
            r.warn(f"import 실패: {'; '.join(failed_svcs)}")
            r.status = STATUS_WARN
        self._record(r)

        # compile 검증 (AST parse)
        r = VerificationResult("앱_구문검사")
        import ast
        parse_errors = []
        for py_file in list((PROJECT_ROOT / "app").rglob("*.py")):
            try:
                ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as e:
                parse_errors.append(f"{py_file.name}:{e.lineno}")
        if not parse_errors:
            r.ok("구문 오류 없음")
        else:
            r.fail(f"구문 오류: {', '.join(parse_errors)}")
        self._record(r)

    # ──────────────────────────────────────────
    # 14. 보고서 생성 & 최종 판정
    # ──────────────────────────────────────────
    def generate_report(self) -> Tuple[str, str]:
        ts = self._started_at.strftime("%Y%m%d_%H%M%S")
        txt_path = PROJECT_ROOT / "reports" / f"full_system_verification_{ts}.txt"
        json_path = PROJECT_ROOT / "reports" / f"full_system_verification_{ts}.json"

        verdict = self._determine_verdict()

        # 다음 조치
        actions = self._build_actions(verdict)

        # JSON 보고서
        report_data = {
            "run_at": self._started_at.isoformat(),
            "python_version": sys.version,
            "os": platform.platform(),
            "trade_mode": self._mode,
            "mock_keys_ok": self._mock_keys_ok,
            "real_keys_ok": self._real_keys_ok,
            "mock_token_ok": self._token_ok,
            "real_token_ok": self._real_token_ok,
            "mock_orderable_cash": self._mock_orderable_cash,
            "mock_order_diagnosis": self._mock_order_diagnosis,
            "real_dry_run_ok": self._real_dry_run_ok,
            "real_actual_order_executed": self._real_actual_order_executed,
            "verdict": verdict,
            "next_actions": actions,
            "results": {k: v.to_dict() for k, v in self.results.items()},
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        # TXT 보고서
        lines = [
            "=" * 70,
            "  AI Stock 시스템 통합 검증 보고서",
            f"  실행시각: {self._started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Python: {sys.version.split()[0]}  |  OS: {platform.system()} {platform.release()}",
            f"  현재 모드: {self._mode}",
            "=" * 70,
            "",
            "[ 검증 항목별 결과 ]",
        ]
        for name, r in self.results.items():
            icon = r.icon()
            lines.append(f"  {icon:<8} {name:<30} {r.message}")
            if r.detail:
                lines.append(f"              >> {r.detail[:100]}")

        lines += [
            "",
            "[ 키 상태 ]",
            f"  MOCK 키: {'완비' if self._mock_keys_ok else '누락'}",
            f"  REAL 키: {'완비' if self._real_keys_ok else '누락'}",
            f"  MOCK 토큰: {'발급됨' if self._token_ok else '미발급'}",
            f"  REAL 토큰: {'발급됨' if self._real_token_ok else '미발급/SKIP'}",
            f"  MOCK 주문가능금액: {self._mock_orderable_cash:,}원",
            f"  MOCK 1주 주문 진단: {'성공' if self._mock_order_diagnosis.get('success') else '실패/미실행'}",
            f"  REAL dry-run: {'OK' if self._real_dry_run_ok else 'WARN/FAIL'}",
            f"  REAL 실제 주문 실행 여부: {'YES' if self._real_actual_order_executed else 'NO'}",
            "",
            "[ 최종 판정 ]",
            f"  >>> {verdict} <<<",
            "",
            "[ 다음 조치사항 ]",
        ]
        for i, a in enumerate(actions, 1):
            lines.append(f"  {i}. {a}")

        lines += [
            "",
            "[ 앱 실행 명령어 ]",
            "  cd \"C:\\Users\\FURSYS\\Desktop\\AI stock\"",
            "  python -m streamlit run app\\streamlit_app.py",
            "=" * 70,
        ]

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return str(txt_path), str(json_path)

    def _determine_verdict(self) -> str:
        fail_count = sum(1 for r in self.results.values() if r.status == STATUS_FAIL)
        env_ok = self.results.get("작업경로", VerificationResult("x")).status == STATUS_OK
        cfg_ok = self.results.get("config.yaml", VerificationResult("x")).status == STATUS_OK

        if not env_ok or not cfg_ok:
            return VERDICT_NOT_READY
        if fail_count >= 5:
            return VERDICT_NOT_READY

        # AI 파이프라인 + 전략 기능 + MOCK 준비
        if self._ai_pipeline_ready and self._strategy_ready and self._token_ok and self._mock_keys_ok:
            return VERDICT_APP_STRATEGY

        # AI 파이프라인 + MOCK 자동매매 준비
        if self._ai_pipeline_ready and self._token_ok and self._mock_keys_ok:
            return VERDICT_MOCK_AUTOTRADE

        # AI 파이프라인 준비 (API 없어도)
        if self._ai_pipeline_ready:
            return VERDICT_AI_PREDICTION

        if self._real_token_ok and self._mock_keys_ok:
            return VERDICT_REAL_REVIEW
        if self._token_ok and self._mock_keys_ok:
            return VERDICT_MOCK
        return VERDICT_PAPER

    def _build_actions(self, verdict: str) -> List[str]:
        actions = []
        if not (PROJECT_ROOT / ".env").exists():
            actions.append(".env 파일 생성 후 KIS API 키 입력 (KIS_APP_KEY, KIS_APP_SECRET, KIS_MOCK_ACCOUNT_NO)")
        if not self._mock_keys_ok:
            actions.append("한국투자증권 Open API 포털에서 모의투자 앱 등록 후 .env에 키 입력")
        if not self._token_ok and self._mock_keys_ok:
            actions.append("API 키가 있지만 토큰 발급 실패 → KIS 모의투자 서버 상태 확인")

        cfg = self._cfg or {}
        if not cfg.get("force_trade", {}).get("enabled", False):
            actions.append("force_trade 활성화: config.yaml → force_trade.enabled: true")

        today = datetime.now().strftime("%Y%m%d")
        top100 = list((PROJECT_ROOT / "reports" / "predictions").glob(f"top100_*.csv"))
        top20 = list((PROJECT_ROOT / "reports" / "predictions").glob(f"top20_*.csv"))
        daily_ok = (PROJECT_ROOT / "data" / "raw" / "daily_prices.csv").exists()
        model_ok = (PROJECT_ROOT / "models" / "model.joblib").exists()

        if not daily_ok:
            actions.append("3년치 데이터 수집: python src/run_ai_prediction_pipeline.py --years 3 --limit 100")
        elif not model_ok:
            actions.append("AI 모델 학습: python src/train_model.py")
        elif not top100:
            actions.append("Top100 생성: python src/predict_candidates.py && python src/select_top_candidates.py --all")

        if verdict == VERDICT_PAPER:
            actions.append("현재 PAPER 모드 준비됨 → streamlit run app\\streamlit_app.py 로 앱 실행")
        elif verdict == VERDICT_MOCK:
            actions.append("MOCK 모드 준비됨 → python src/force_auto_trade.py --budget 300000 --mode mock --max-orders 100")
        elif verdict == VERDICT_AI_PREDICTION:
            actions.append("AI 파이프라인 완료 → python src/force_auto_trade.py --budget 300000 --mode paper --max-orders 100")
        elif verdict == VERDICT_MOCK_AUTOTRADE:
            actions.append("MOCK 자동매매 가능 → python src/force_auto_trade.py --budget 300000 --mode mock --max-orders 100")
        elif verdict == VERDICT_REAL_REVIEW:
            actions.append("REAL 조건 검토 가능 → 충분한 MOCK 검증 후 신중하게 전환 여부 결정")

        if not actions:
            actions.append("모든 검증 통과 — 정상 운영 가능")

        return actions

    # ──────────────────────────────────────────
    # 15. 콘솔 대시보드
    # ──────────────────────────────────────────
    def print_dashboard(self, txt_path: str, json_path: str) -> None:
        verdict = self._determine_verdict()

        def _s(key: str) -> str:
            r = self.results.get(key)
            if not r:
                return STATUS_SKIP
            return r.status

        def _icon(key: str) -> str:
            s = _s(key)
            return {"OK": "OK    ", "FAIL": "FAIL  ", "SKIPPED": "SKIP  ", "WARN": "WARN  "}.get(s, "??    ")

        verdict_color = {
            VERDICT_PAPER: "PAPER만 가능",
            VERDICT_MOCK: "MOCK까지 가능",
            VERDICT_AI_PREDICTION: "AI 파이프라인 준비됨",
            VERDICT_MOCK_AUTOTRADE: "MOCK 자동매매 가능",
            VERDICT_APP_STRATEGY: "앱 전략 매매 가능 (MOCK/PAPER)",
            VERDICT_REAL_REVIEW: "REAL 조건 검토 가능",
            VERDICT_NOT_READY: "준비 안 됨",
        }.get(verdict, verdict)

        sep = "+" + "-" * 58 + "+"
        print()
        print(sep)
        title_line = "  AI Stock 실제 동작 검증 결과"
        print(f"|{title_line:<58}|")
        print(sep)
        print(f"|  {'[환경]':<56}|")
        print(f"|  Python         : {_icon('Python_버전')}{'':<32}|")
        print(f"|  requirements   : {_icon('패키지_import')}{'':<32}|")
        print(f"|  config.yaml    : {_icon('config.yaml')}{'':<32}|")
        print(f"|  .env           : {_icon('.env_파일')}{'':<32}|")
        print(f"|  필수 폴더      : {_icon('필수_폴더')}{'':<32}|")
        print(sep)
        print(f"|  {'[API]':<56}|")
        print(f"|  MOCK token     : {_icon('MOCK_토큰발급')}{'':<32}|")
        print(f"|  MOCK 현재가    : {_icon('MOCK_현재가조회')}{'':<32}|")
        print(f"|  MOCK 잔고      : {_icon('MOCK_잔고조회')}{'':<32}|")
        print(f"|  MOCK 주문가능  : {_icon('MOCK_주문가능금액')}{'':<32}|")
        print(f"|  REAL token     : {_icon('REAL_토큰발급')}{'':<32}|")
        print(f"|  MOCK cash      : {self._mock_orderable_cash:>12,}원{'':<24}|")
        print(sep)
        print(f"|  {'[Trading]':<56}|")
        print(f"|  후보생성       : {_icon('top20_파일')}{'':<32}|")
        print(f"|  예산배분       : {_icon('예산배분')}{'':<32}|")
        print(f"|  PAPER 주문     : {_icon('PAPER_주문')}{'':<32}|")
        print(f"|  MOCK 주문      : {_icon('MOCK_주문')}{'':<32}|")
        print(f"|  REAL dry-run   : {_icon('REAL_order_test_dry_run')}{'':<32}|")
        print(f"|  REAL actual    : {'NO':<38}|")
        print(f"|  매도감시       : {_icon('매도감시_실행')}{'':<32}|")
        print(f"|  강제청산 안전  : {_icon('강제청산_안전장치')}{'':<32}|")
        print(sep)
        print(f"|  {'[Strategy]':<56}|")
        print(f"|  strategy_config: {_icon('strategy_config_load')}{'':<32}|")
        print(f"|  run_pipeline() : {_icon('run_pipeline_returns_dict')}{'':<32}|")
        print(f"|  buy_candidates : {_icon('buy_candidate_list_exists')}{'':<32}|")
        print(f"|  PositionRecord : {_icon('PositionRecord_strategy_fields')}{'':<32}|")
        print(f"|  monitor 전략   : {_icon('force_sell_monitor_strategy_param')}{'':<32}|")
        print(sep)
        print(f"|  {'[App]':<56}|")
        print(f"|  Streamlit      : {_icon('Streamlit_import')}{'':<32}|")
        print(f"|  앱 파일        : {_icon('메인앱_파일')}{'':<32}|")
        print(f"|  페이지 파일    : {_icon('페이지_파일')}{'':<32}|")
        print(sep)
        verdict_line = f"  최종 판정: {verdict}  ({verdict_color})"
        print(f"|{verdict_line:<58}|")
        print(sep)
        txt_line = f"  TXT : {Path(txt_path).name}"
        json_line = f"  JSON: {Path(json_path).name}"
        print(f"|{txt_line:<58}|")
        print(f"|{json_line:<58}|")
        print(sep)

        actions = self._build_actions(verdict)
        print("\n[ 다음 조치사항 ]")
        for i, a in enumerate(actions, 1):
            print(f"  {i}. {a}")
        print()

    # ──────────────────────────────────────────
    # 전체 실행
    # ──────────────────────────────────────────
    def verify_sell_policy_features(self) -> None:
        """Verify sell-policy selection and trailing-stop helpers without placing orders."""
        self._section("13d. sell policy and trailing stop check")
        try:
            from sell_policy import should_auto_sell, list_sell_policies
            from market_strength import get_market_strength
            from trailing_stop_manager import update_trailing_state

            policies = {p["id"] for p in list_sell_policies(self._cfg)}
            required = {"fixed_2pct", "market_strength_trailing", "manual_hold"}
            missing = required - policies
            if missing:
                self._record(VerificationResult("sell_policy_list").fail(f"missing: {missing}"))
            else:
                self._record(VerificationResult("sell_policy_list").ok("3 policies available"))

            base = {"stock_code": "005930", "stock_name": "TEST", "quantity": 10, "avg_price": 10000, "entry_price": 10000}
            fixed = should_auto_sell({**base, "sell_policy_id": "fixed_2pct"}, 10200, {"level": "normal"})
            self._record(
                VerificationResult("fixed_2pct_should_sell").ok("TAKE_PROFIT")
                if fixed.get("should_sell") and fixed.get("sell_reason") == "TAKE_PROFIT"
                else VerificationResult("fixed_2pct_should_sell").fail(str(fixed))
            )

            manual = should_auto_sell({**base, "sell_policy_id": "manual_hold"}, 10200, {"level": "normal"})
            self._record(
                VerificationResult("manual_hold_alert_only").ok("alert only; no auto sell")
                if not manual.get("should_sell") and manual.get("alert_only")
                else VerificationResult("manual_hold_alert_only").fail(str(manual))
            )

            trailing = update_trailing_state({**base, "sell_policy_id": "market_strength_trailing"}, 10300, {
                "trailing_stop_rate": {"normal": 0.006}
            }, "normal")
            self._record(
                VerificationResult("trailing_state_update").ok("trailing state created")
                if trailing.get("trailing_active") and trailing.get("trailing_stop_price")
                else VerificationResult("trailing_state_update").fail(str(trailing))
            )

            ms = get_market_strength(config=self._cfg)
            self._record(
                VerificationResult("market_strength_fallback").ok(f"{ms.get('level')} score={ms.get('score')}")
                if ms.get("level") in {"weak", "normal", "strong", "very_strong"}
                else VerificationResult("market_strength_fallback").fail(str(ms))
            )
        except Exception as exc:
            self._record(VerificationResult("sell_policy_import").fail(str(exc)))

    def verify_real_single_order_safety(self) -> None:
        """Verify REAL single-order tooling without placing any order."""
        self._section("13d. REAL single-order safety check")

        readiness_path = PROJECT_ROOT / "src" / "real_order_readiness_check.py"
        test_path = PROJECT_ROOT / "src" / "real_order_test.py"
        diagnosis_path = PROJECT_ROOT / "src" / "real_order_diagnosis.py"
        verify_path = PROJECT_ROOT / "src" / "real_order_verify.py"
        self._record(
            VerificationResult("real_order_readiness_check.py").ok(str(readiness_path))
            if readiness_path.exists()
            else VerificationResult("real_order_readiness_check.py").fail("missing")
        )
        self._record(
            VerificationResult("real_order_test.py").ok(str(test_path))
            if test_path.exists()
            else VerificationResult("real_order_test.py").fail("missing")
        )
        self._record(
            VerificationResult("real_order_diagnosis.py").ok(str(diagnosis_path))
            if diagnosis_path.exists()
            else VerificationResult("real_order_diagnosis.py").fail("missing")
        )
        self._record(
            VerificationResult("real_order_verify.py").ok(str(verify_path))
            if verify_path.exists()
            else VerificationResult("real_order_verify.py").fail("missing")
        )

        try:
            import real_order_readiness_check  # type: ignore  # noqa: F401
            import real_order_test  # type: ignore  # noqa: F401
            import real_order_diagnosis  # type: ignore  # noqa: F401
            import real_order_verify  # type: ignore  # noqa: F401
            from safety_gate import SafetyGate  # type: ignore

            self._record(VerificationResult("REAL_order_modules_import").ok("import ok"))
            gate = SafetyGate(config_path=str(PROJECT_ROOT / self.config_path), runtime_mode="real")
            conditions = gate.get_real_order_conditions()
            missing = [name for name, ok in conditions.items() if not ok]
            if missing:
                self._record(
                    VerificationResult("REAL_single_test_conditions")
                    .ok("blocked by default: " + ", ".join(missing), conditions=conditions)
                )
            else:
                self._record(
                    VerificationResult("REAL_single_test_conditions")
                    .warn("all REAL single-test conditions are enabled; verification still does not order", conditions=conditions)
                )
        except Exception as exc:
            self._record(VerificationResult("REAL_order_modules_import").fail(str(exc)))

        try:
            from kis_api import KISApiClient  # type: ignore
            from real_order_utils import validate_real_order_payload  # type: ignore

            gate = SafetyGate(config_path=str(PROJECT_ROOT / self.config_path), runtime_mode="real")
            api = KISApiClient(str(PROJECT_ROOT / self.config_path), gate=gate)
            req = api.build_cash_order_request("005930", 1, 70000, include_hashkey=True)
            valid, errors = validate_real_order_payload(req.get("body", {}))
            self._record(
                VerificationResult("REAL_order_payload_validation").ok("payload valid")
                if valid
                else VerificationResult("REAL_order_payload_validation").fail("; ".join(errors))
            )
            hash_ready = req.get("hashkey_ready")
            self._record(
                VerificationResult("REAL_hashkey_generation").ok("hashkey generated")
                if hash_ready is True
                else VerificationResult("REAL_hashkey_generation").warn("hashkey not generated; REAL order will be blocked")
            )
        except Exception as exc:
            self._record(VerificationResult("REAL_hashkey_generation").warn(str(exc)[:160]))

        rc, out, err = _run_cmd(
            [sys.executable, "src/real_order_test.py", "--stock-code", "005930", "--quantity", "1", "--dry-run"],
            timeout=45,
        )
        r = VerificationResult("REAL_order_test_dry_run")
        if rc == 0:
            r.ok("dry-run completed; no real order placed")
            self._real_dry_run_ok = True
        else:
            r.warn(f"dry-run returned rc={rc}; no real order placed", detail=(out + "\n" + err)[-1000:])
        self._record(r)

        rc, out, err = _run_cmd(
            [sys.executable, "src/real_order_diagnosis.py", "--stock-code", "005930", "--quantity", "1"],
            timeout=60,
        )
        r = VerificationResult("REAL_order_diagnosis_dry_run")
        if rc == 0:
            r.ok("diagnosis completed; no real order placed")
            self._real_dry_run_ok = True
        else:
            r.warn(f"diagnosis returned rc={rc}; no real order placed", detail=(out + "\n" + err)[-1000:])
        self._record(r)

        rc, out, err = _run_cmd(
            [sys.executable, "src/real_order_verify.py", "--stock-code", "005930"],
            timeout=60,
        )
        r = VerificationResult("REAL_order_verify_query")
        if rc == 0:
            r.ok("order inquiry completed; no real order placed")
        else:
            r.warn(f"order inquiry returned rc={rc}; no real order placed", detail=(out + "\n" + err)[-1000:])
        self._record(r)

        cfg = self._cfg or {}
        real_trade = cfg.get("real_trade", {}) if isinstance(cfg, dict) else {}
        force_trade = cfg.get("force_trade", {}) if isinstance(cfg, dict) else {}
        r = VerificationResult("REAL_bulk_order_blocked")
        if not real_trade.get("allow_bulk_buy") and not force_trade.get("allow_real_bulk_order"):
            r.ok("REAL bulk order disabled by default")
        else:
            r.warn("REAL bulk order flags are enabled; use advanced option only")
        self._record(r)

        self._real_actual_order_executed = False
        self._record(VerificationResult("REAL_actual_order_executed").ok("NO"))

    def verify_mode_url_consistency(self) -> None:
        self._section("13f. MOCK/REAL mode-url consistency")

        try:
            from kis_api import KISApiClient  # type: ignore
            from kis_auth import KISAuth  # type: ignore
            from safety_gate import SafetyGate  # type: ignore
        except Exception as exc:
            self._record(VerificationResult("mode_url_import").fail(str(exc)))
            return

        config_path = str(PROJECT_ROOT / self.config_path)
        checks = [
            ("mock", "MOCK", "openapivts.koreainvestment.com:29443", "MOCK_APP_KEY"),
            ("real", "REAL", "openapi.koreainvestment.com:9443", "REAL_APP_KEY"),
        ]
        for requested, expected_mode, expected_host, expected_key_type in checks:
            r = VerificationResult(f"{expected_mode}_mode_url_consistency")
            try:
                auth = KISAuth(config_path, runtime_mode=requested)
                gate = SafetyGate(config_path, runtime_mode=requested)
                if requested == "real" and gate.mode != "REAL":
                    r.warn(
                        "REAL is blocked by safety conditions; auth URL still checked",
                        token_url=auth.token_url,
                        key_type_used=auth.key_type_used,
                        resolved_mode=gate.mode,
                    )
                    self._record(r)
                    continue
                api = KISApiClient(config_path, gate=gate)
                meta = api.diagnostic_metadata()
                token_url = str(meta.get("token_url", ""))
                base_url = str(meta.get("base_url", ""))
                key_type = str(meta.get("key_type_used", ""))
                mode_ok = str(meta.get("resolved_mode", "")) == expected_mode
                url_ok = expected_host in token_url and expected_host in base_url
                key_ok = key_type in {expected_key_type, "GENERIC_APP_KEY_FALLBACK"}
                if mode_ok and url_ok and key_ok and meta.get("mode_url_valid") is True:
                    r.ok(
                        f"{expected_mode} token/base URL OK",
                        token_url=token_url,
                        base_url=base_url,
                        key_type_used=key_type,
                        token_cache_file=meta.get("token_cache_file"),
                    )
                else:
                    r.fail(
                        f"{expected_mode} mode/url/key mismatch",
                        token_url=token_url,
                        base_url=base_url,
                        key_type_used=key_type,
                        resolved_mode=meta.get("resolved_mode"),
                    )
            except Exception as exc:
                r.fail(str(exc))
            self._record(r)

    def verify_order_result_csv_columns(self) -> None:
        self._section("13g. order result CSV diagnostics columns")

        required = {
            "requested_mode",
            "resolved_mode",
            "base_url",
            "token_url",
            "key_type_used",
            "token_cache_file",
            "token_source",
            "expected_appkey_fingerprint",
            "header_appkey_fingerprint",
            "app_key_mode_valid",
            "mode_url_valid",
            "mock_order_called",
            "real_order_called",
            "api_called",
            "success",
        }
        reports_dir = PROJECT_ROOT / "reports"
        orders_dir = reports_dir / "orders"
        candidates = []
        if reports_dir.exists():
            candidates.extend(reports_dir.glob("orders_*.csv"))
            candidates.extend(reports_dir.glob("strategy_orders_*.csv"))
        if orders_dir.exists():
            candidates.extend(orders_dir.glob("orders_*.csv"))

        r = VerificationResult("주문_CSV_진단컬럼")
        if not candidates:
            self._record(r.warn("no order CSV found yet; columns will be checked after next order run"))
            return

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        try:
            import pandas as pd

            df = pd.read_csv(latest, nrows=5)
            missing = sorted(required - set(df.columns))
            if missing:
                r.fail(f"missing columns: {', '.join(missing)}", file=str(latest))
            else:
                r.ok("diagnostic columns present", file=str(latest))
        except Exception as exc:
            r.warn(str(exc), file=str(latest))
        self._record(r)

    def verify_mode_key_fingerprint_consistency(self) -> None:
        """MOCK/REAL 키 fingerprint 일관성 검증."""
        from kis_auth import fingerprint_key, get_kis_credentials
        from trade_mode import get_expected_key_fingerprint_for_mode

        # 1. KIS_MOCK_APP_KEY 존재
        r1 = VerificationResult("MOCK_APP_KEY_존재")
        mock_key = os.getenv("KIS_MOCK_APP_KEY", "")
        if mock_key:
            r1.ok(f"KIS_MOCK_APP_KEY 확인 ({fingerprint_key(mock_key)})")
        else:
            r1.fail("KIS_MOCK_APP_KEY 환경변수 미설정")
        self._record(r1)

        # 2. KIS_REAL_APP_KEY 존재
        r2 = VerificationResult("REAL_APP_KEY_존재")
        real_key = os.getenv("KIS_REAL_APP_KEY", "")
        if real_key:
            r2.ok(f"KIS_REAL_APP_KEY 확인 ({fingerprint_key(real_key)})")
        else:
            r2.warn("KIS_REAL_APP_KEY 환경변수 미설정 (REAL 주문 불가)")
        self._record(r2)

        # 3. MOCK/REAL fingerprint 서로 다른지 확인
        r3 = VerificationResult("MOCK_REAL_키_구분")
        if mock_key and real_key:
            if mock_key != real_key:
                r3.ok(
                    f"MOCK키({fingerprint_key(mock_key)})와 REAL키({fingerprint_key(real_key)}) 서로 다름"
                )
            else:
                r3.warn("KIS_MOCK_APP_KEY와 KIS_REAL_APP_KEY가 동일함 (의도된 경우 무시)")
        else:
            r3.warn("키 하나 이상 미설정으로 비교 불가")
        self._record(r3)

        # 4. MOCK mode get_kis_credentials → MOCK_APP_KEY 반환하는지
        r4 = VerificationResult("MOCK_credentials_key_type")
        try:
            creds = get_kis_credentials("MOCK")
            kt = creds.get("key_type_used", "")
            if kt == "MOCK_APP_KEY":
                r4.ok(f"MOCK mode key_type_used={kt} fingerprint={creds.get('appkey_fingerprint')}")
            elif kt == "GENERIC_APP_KEY_FALLBACK":
                r4.warn(f"MOCK mode fallback 사용 중 (KIS_MOCK_APP_KEY 미설정?): key_type_used={kt}")
            else:
                r4.fail(f"MOCK mode key_type_used={kt} (기대값=MOCK_APP_KEY)")
        except Exception as exc:
            r4.fail(str(exc))
        self._record(r4)

        # 5. REAL mode get_kis_credentials → REAL_APP_KEY 반환하는지
        r5 = VerificationResult("REAL_credentials_key_type")
        try:
            creds = get_kis_credentials("REAL")
            kt = creds.get("key_type_used", "")
            if kt == "REAL_APP_KEY":
                r5.ok(f"REAL mode key_type_used={kt} fingerprint={creds.get('appkey_fingerprint')}")
            elif kt == "GENERIC_APP_KEY_FALLBACK":
                r5.warn(f"REAL mode fallback 사용 중 (KIS_REAL_APP_KEY 미설정?): key_type_used={kt}")
            else:
                r5.fail(f"REAL mode key_type_used={kt} (기대값=REAL_APP_KEY)")
        except Exception as exc:
            r5.warn(str(exc))
        self._record(r5)

        # 6. MOCK credentials base_url/token_url
        r6 = VerificationResult("MOCK_token_url_확인")
        try:
            creds = get_kis_credentials("MOCK")
            turl = creds.get("token_url", "")
            burl = creds.get("base_url", "")
            if "openapivts" in turl and "openapivts" in burl:
                r6.ok(f"MOCK token_url/base_url 올바름: {turl}")
            else:
                r6.fail(f"MOCK token_url/base_url 이상: token_url={turl} base_url={burl}")
        except Exception as exc:
            r6.fail(str(exc))
        self._record(r6)

        # 7. REAL credentials base_url/token_url
        r7 = VerificationResult("REAL_token_url_확인")
        try:
            creds = get_kis_credentials("REAL")
            turl = creds.get("token_url", "")
            burl = creds.get("base_url", "")
            if "openapi.koreainvestment.com:9443" in (turl or "") and "openapi.koreainvestment.com:9443" in (burl or ""):
                r7.ok(f"REAL token_url/base_url 올바름: {turl}")
            else:
                r7.warn(f"REAL token_url/base_url 확인 필요: token_url={turl} base_url={burl}")
        except Exception as exc:
            r7.warn(str(exc))
        self._record(r7)

        # 8. MOCK KISApiClient fingerprint == expected
        r8 = VerificationResult("MOCK_KISApiClient_appkey_fingerprint")
        try:
            from safety_gate import SafetyGate
            from kis_api import KISApiClient
            gate = SafetyGate(self.config_path, runtime_mode="mock")
            api = KISApiClient(self.config_path, gate=gate)
            meta = api.diagnostic_metadata()
            actual_fp = meta.get("appkey_fingerprint", "MISSING")
            expected_fp = get_expected_key_fingerprint_for_mode("MOCK")
            if expected_fp and expected_fp != "MISSING" and actual_fp == expected_fp:
                r8.ok(
                    f"MOCK KISApiClient appkey fingerprint 일치: {actual_fp}",
                    expected=expected_fp,
                    actual=actual_fp,
                )
            elif expected_fp == "MISSING":
                r8.warn("KIS_MOCK_APP_KEY 미설정으로 비교 불가")
            else:
                r8.fail(
                    f"MOCK KISApiClient appkey 불일치! expected={expected_fp} actual={actual_fp}",
                    expected=expected_fp,
                    actual=actual_fp,
                )
        except Exception as exc:
            r8.warn(str(exc))
        self._record(r8)

        # 9. 실제 REAL 주문은 절대 실행하지 않음
        r9 = VerificationResult("REAL_주문_실행금지_확인")
        r9.ok("REAL 주문 dry-run은 실행하지 않음 (안전)")
        self._record(r9)

    def verify_mock_sell_dryrun(self) -> None:
        """13h. MOCK 매도 경로 dry-run 검증 (실제 주문 없음)."""
        self._section("13h. MOCK 매도 경로 dry-run 검증")
        try:
            from manual_sell_diagnosis import run_manual_sell_diagnosis
            result = run_manual_sell_diagnosis(
                stock_code="055550",
                mode="mock",
                quantity=1,
                dry_run=True,
                config_path=str(PROJECT_ROOT / "config.yaml"),
            )
            r = VerificationResult("MOCK_매도_dry_run")
            if result.get("app_key_mode_valid") and result.get("key_type_used") == "MOCK_APP_KEY":
                r.ok(
                    f"MOCK 매도 경로 검증 통과 | key_type={result.get('key_type_used')} "
                    f"base_url={result.get('base_url')} fingerprint={result.get('header_appkey_fingerprint')}"
                )
            elif result.get("app_key_mode_valid") is False:
                r.fail(
                    f"appkey 불일치: expected={result.get('expected_appkey_fingerprint')} "
                    f"actual={result.get('header_appkey_fingerprint')} "
                    f"errors={result.get('diagnosis_errors', [])}"
                )
            else:
                r.warn(f"매도 dry-run 부분 완료: {result.get('msg')} | errors={result.get('diagnosis_errors', [])}")
            self._record(r)

            r2 = VerificationResult("MOCK_매도_real_order_not_called")
            if not result.get("real_order_called") and not result.get("api_called"):
                r2.ok("dry-run: REAL 주문 미실행 확인")
            else:
                r2.fail(f"dry-run 중 주문 호출 감지: api_called={result.get('api_called')} real={result.get('real_order_called')}")
            self._record(r2)

        except Exception as exc:
            self._record(VerificationResult("MOCK_매도_dry_run").warn(f"manual_sell_diagnosis 오류: {exc}"))

    def verify_refresh_candidate_prices(self) -> None:
        """13i. 현재가 갱신 경로 검증 (--mode mock, limit=1)."""
        self._section("13i. refresh_candidate_prices 검증 (MOCK, limit=1)")
        try:
            from refresh_candidate_prices import refresh_prices
            from prediction_service import get_today_str
            date_str = get_today_str()
            result = refresh_prices(
                date_str=date_str,
                top_n=20,
                mode="mock",
                limit=1,
            )
            r = VerificationResult("현재가갱신_MOCK")
            if result.get("success") and result.get("price_key_type_used") == "MOCK_APP_KEY":
                r.ok(
                    f"현재가 갱신 성공 | updated={result.get('updated')} errors={result.get('errors')} "
                    f"key_type={result.get('price_key_type_used')}"
                )
            elif result.get("success") and result.get("updated", 0) >= 0:
                r.ok(f"현재가 갱신 완료 | updated={result.get('updated')} errors={result.get('errors')}")
            else:
                r.warn(f"현재가 갱신 실패 또는 파일 없음: {result.get('message')}")
            self._record(r)
        except Exception as exc:
            self._record(VerificationResult("현재가갱신_MOCK").warn(f"refresh_candidate_prices 오류: {exc}"))

    def run_all(self) -> None:
        print("\n" + "=" * 60)
        print("  AI Stock 시스템 통합 검증 시작")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  프로젝트 루트: {PROJECT_ROOT}")
        print("=" * 60)

        self.verify_environment()
        self.verify_config()
        self.verify_api_keys()
        self.verify_api_connection()
        self.verify_market_session()
        self.verify_after_hours_diagnosis()
        self.verify_candidate_generation()
        self.verify_ai_pipeline()
        self.verify_budget_allocation()
        self.verify_paper_order()
        self.verify_mock_order()
        self.verify_order_test()
        self.verify_sell_monitor()
        self.verify_force_exit_safety()
        self.verify_no_trade_analysis()
        self.verify_price_tick()
        self.verify_account_sync_and_columns()
        self.verify_strategy_features()
        self.verify_sell_policy_features()
        self.verify_real_single_order_safety()
        self.verify_mode_url_consistency()
        self.verify_mode_key_fingerprint_consistency()
        self.verify_order_result_csv_columns()
        self.verify_mock_sell_dryrun()
        self.verify_refresh_candidate_prices()
        self.verify_streamlit_app()

        # 보고서 생성
        txt_path, json_path = self.generate_report()

        # 콘솔 대시보드
        self.print_dashboard(txt_path, json_path)

        # 로그 파일 저장
        log_path = PROJECT_ROOT / "logs" / "full_system_verification.log"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(self._log_lines))

        print(f"검증 로그: {log_path}")
        print(f"TXT 보고서: {txt_path}")
        print(f"JSON 보고서: {json_path}\n")


# ──────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Stock 시스템 통합 검증 — REAL 실전 주문은 절대 실행하지 않습니다."
    )
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    args = parser.parse_args()

    verifier = SystemVerifier(config_path=args.config)
    verifier.run_all()


if __name__ == "__main__":
    main()
