"""AI Stock .env API 키 진단 및 자동 보정 스크립트.

.env 파일의 키 이름·예시값·누락 여부를 진단하고
코드가 인식할 수 있는 표준 키 이름으로 자동 보정합니다.

민감정보(App Key, Secret, 계좌번호)는 절대 평문으로 출력하지 않습니다.

사용법:
    python src/env_diagnosis_and_fix.py
    python src/env_diagnosis_and_fix.py --rerun-verification
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Windows UTF-8 출력 설정 ───────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 경로 설정 ─────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
os.chdir(str(PROJECT_ROOT))

ENV_PATH = PROJECT_ROOT / ".env"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ── 표준 키 정의 ──────────────────────────────────────────────

# 실제로 코드(kis_auth.py, kis_api.py, config.yaml)가 읽는 표준 키
STANDARD_KEYS_MOCK = [
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
    "KIS_MOCK_APP_KEY",
    "KIS_MOCK_APP_SECRET",
    "KIS_MOCK_ACCOUNT_NO",
    "KIS_MOCK_ACCOUNT_PRODUCT_CODE",
]
STANDARD_KEYS_REAL = [
    "KIS_REAL_APP_KEY",
    "KIS_REAL_APP_SECRET",
    "KIS_ACCOUNT_NO",
    "KIS_ACCOUNT_PRODUCT_CODE",
]
STANDARD_KEYS_OTHER = ["KIS_USE_MOCK"]

ALL_STANDARD_KEYS = STANDARD_KEYS_MOCK + STANDARD_KEYS_REAL + STANDARD_KEYS_OTHER

# 비표준 키 → 표준 키 매핑 (사용자가 잘못 입력하기 쉬운 케이스)
TYPO_MAP: Dict[str, str] = {
    "KIS_MOCK_APPKEY": "KIS_MOCK_APP_KEY",
    "KIS_MOCK_APPSECRET": "KIS_MOCK_APP_SECRET",
    "KIS_REAL_APPKEY": "KIS_REAL_APP_KEY",
    "KIS_REAL_APPSECRET": "KIS_REAL_APP_SECRET",
    "KIS_ACCOUNT_NUMBER": "KIS_ACCOUNT_NO",
    "KIS_MOCK_ACCOUNT_NUMBER": "KIS_MOCK_ACCOUNT_NO",
    "APP_KEY": "KIS_APP_KEY",
    "APP_SECRET": "KIS_APP_SECRET",
    # 비표준 Secret 키명
    "KIS_ACCOUNT_APP_SECRET": "KIS_REAL_APP_SECRET",
    "KIS_MOCK_ACCOUNT_APP_SECRET": "KIS_MOCK_APP_SECRET",
    # 기타 변형
    "KIS_APPKEY": "KIS_APP_KEY",
    "KIS_APPSECRET": "KIS_APP_SECRET",
    "KIS_SECRET": "KIS_APP_SECRET",
    "KIS_KEY": "KIS_APP_KEY",
}

# 예시값 목록 (이 값이 들어 있으면 실제 키로 인정 안 함)
PLACEHOLDER_PATTERNS = [
    "your_app_key_here",
    "your_app_secret_here",
    "your_real_or_mock_app_key_here",
    "your_real_or_mock_app_secret_here",
    "your_mock_account_number_here",
    "your_real_account_number_here",
    "여기에_앱키_붙여넣기",
    "여기에_앱시크릿_붙여넣기",
    "모의투자_앱키",
    "모의투자_앱시크릿",
    "실전투자_앱키",
    "실전투자_앱시크릿",
    "한국투자증권에서_발급받은_모의투자_app_key",
    "한국투자증권에서_발급받은_모의투자_app_secret",
    "your_",
    "_here",
    "example",
    "placeholder",
    "insert_here",
    "입력필요",
    "입력_필요",
]


# ── 마스킹 유틸리티 ───────────────────────────────────────────

def _mask(value: str, show_front: int = 4, show_back: int = 4) -> str:
    """민감정보 마스킹. 앞 show_front자 + **** + 뒤 show_back자."""
    if not value:
        return "(empty)"
    n = len(value)
    if n <= show_front:
        return "*" * n
    if n <= show_front + show_back:
        return value[:show_front] + "*" * (n - show_front)
    return value[:show_front] + "*" * (n - show_front - show_back) + value[-show_back:]


def _mask_account(value: str) -> str:
    """계좌번호 마스킹: 앞 4자리만 노출."""
    if not value:
        return "(empty)"
    return value[:4] + "*" * max(0, len(value) - 4)


def _is_placeholder(value: str) -> bool:
    """예시값 여부 판단."""
    if not value:
        return False
    v_lower = value.lower().strip()
    return any(p.lower() in v_lower for p in PLACEHOLDER_PATTERNS)


# ── .env 파서 ─────────────────────────────────────────────────

class EnvParser:
    """안전한 .env 파일 파서."""

    def __init__(self, env_path: Path):
        self.env_path = env_path
        self.raw_lines: List[str] = []
        self.pairs: Dict[str, str] = {}        # key -> value (마지막 값)
        self.duplicates: List[str] = []
        self.parse_warnings: List[str] = []
        self._seen_keys: Dict[str, int] = {}   # key -> 등장 횟수

    def parse(self) -> "EnvParser":
        if not self.env_path.exists():
            return self
        try:
            with open(self.env_path, "r", encoding="utf-8", errors="replace") as f:
                self.raw_lines = f.readlines()
        except Exception as e:
            self.parse_warnings.append(f"파일 읽기 오류: {e}")
            return self

        for i, line in enumerate(self.raw_lines, 1):
            stripped = line.rstrip("\n\r")
            # 빈 줄, 주석
            if not stripped.strip() or stripped.strip().startswith("#"):
                continue
            # KEY=VALUE 파싱
            if "=" not in stripped:
                self.parse_warnings.append(f"L{i}: KEY=VALUE 형식 아님: '{stripped[:40]}'")
                continue
            idx = stripped.index("=")
            key = stripped[:idx].strip()
            value = stripped[idx + 1:].strip()
            # 따옴표 제거
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            # 키 유효성
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                self.parse_warnings.append(f"L{i}: 잘못된 키 이름: '{key}'")
                continue
            # 중복 체크
            if key in self._seen_keys:
                self._seen_keys[key] += 1
                if key not in self.duplicates:
                    self.duplicates.append(key)
            else:
                self._seen_keys[key] = 1
            self.pairs[key] = value

        return self


# ── 키 상태 평가 ──────────────────────────────────────────────

KEY_STATUS_OK = "OK"
KEY_STATUS_MISSING = "MISSING"
KEY_STATUS_PLACEHOLDER = "PLACEHOLDER"
KEY_STATUS_EMPTY = "EMPTY"


def _key_status(key: str, env: Dict[str, str]) -> str:
    v = env.get(key, "")
    if not v:
        return KEY_STATUS_MISSING if key not in env else KEY_STATUS_EMPTY
    if _is_placeholder(v):
        return KEY_STATUS_PLACEHOLDER
    return KEY_STATUS_OK


def _has_real_value(key: str, env: Dict[str, str]) -> bool:
    return _key_status(key, env) == KEY_STATUS_OK


# ── 진단기 ────────────────────────────────────────────────────

class EnvDiagnostic:
    def __init__(self, env_path: Path = ENV_PATH):
        self.env_path = env_path
        self.parser = EnvParser(env_path)
        self.env: Dict[str, str] = {}
        self.fixes_applied: List[str] = []
        self.warnings: List[str] = []
        self.typo_renames: List[Tuple[str, str]] = []   # (old_key, std_key)
        self.ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._backup_path: Optional[Path] = None

    # ── 1. .env 존재 확인 및 생성 ────────────────────────────

    def ensure_env_exists(self) -> None:
        if self.env_path.exists():
            print(f"[OK] .env 파일 존재: {self.env_path}")
            return
        # .env.example 복사
        if ENV_EXAMPLE_PATH.exists():
            shutil.copy(ENV_EXAMPLE_PATH, self.env_path)
            print(f"[FIX] .env.example → .env 복사 완료")
        else:
            # 기본 템플릿 생성
            template = _build_default_template()
            with open(self.env_path, "w", encoding="utf-8") as f:
                f.write(template)
            print(f"[FIX] .env 기본 템플릿 생성 완료")
        print(f"      → {self.env_path}")
        print(f"      App Key, App Secret, 계좌번호를 직접 입력해 주세요.")

    # ── 2. 파싱 ──────────────────────────────────────────────

    def load(self) -> None:
        self.parser.parse()
        self.env = dict(self.parser.pairs)
        if self.parser.parse_warnings:
            for w in self.parser.parse_warnings:
                self.warnings.append(f"파싱경고: {w}")
        if self.parser.duplicates:
            for k in self.parser.duplicates:
                self.warnings.append(f"중복키(마지막값 사용): {k}")

    # ── 3. 비표준 키 탐지 ────────────────────────────────────

    def detect_typos(self) -> None:
        """비표준 키 이름 탐지 후 표준 키로 매핑 정보 수집."""
        for bad_key, std_key in TYPO_MAP.items():
            if bad_key in self.env and bad_key not in ALL_STANDARD_KEYS:
                value = self.env[bad_key]
                if value and not _is_placeholder(value):
                    self.typo_renames.append((bad_key, std_key))
                    print(f"  [탐지] 비표준 키 '{bad_key}' → 표준 '{std_key}' 로 인식")

    # ── 4. 자동 보정 로직 ────────────────────────────────────

    def apply_fixes(self) -> None:
        """표준 키 보정을 env dict에 적용."""
        # 4-a. 비표준 키 값을 표준 키로 복사 (기존 표준 키 없을 때만)
        for bad_key, std_key in self.typo_renames:
            if not _has_real_value(std_key, self.env) and _has_real_value(bad_key, self.env):
                self.env[std_key] = self.env[bad_key]
                self.fixes_applied.append(f"'{bad_key}' 값을 '{std_key}'로 복사")

        # 4-b. KIS_APP_KEY ↔ KIS_MOCK_APP_KEY 상호 보완
        mock_key_ok = _has_real_value("KIS_MOCK_APP_KEY", self.env)
        app_key_ok = _has_real_value("KIS_APP_KEY", self.env)
        if mock_key_ok and not app_key_ok:
            self.env["KIS_APP_KEY"] = self.env["KIS_MOCK_APP_KEY"]
            self.fixes_applied.append("KIS_MOCK_APP_KEY 값을 KIS_APP_KEY로 복사")
        elif app_key_ok and not mock_key_ok:
            self.env["KIS_MOCK_APP_KEY"] = self.env["KIS_APP_KEY"]
            self.fixes_applied.append("KIS_APP_KEY 값을 KIS_MOCK_APP_KEY로 복사")

        # 4-c. KIS_APP_SECRET ↔ KIS_MOCK_APP_SECRET 상호 보완
        mock_sec_ok = _has_real_value("KIS_MOCK_APP_SECRET", self.env)
        app_sec_ok = _has_real_value("KIS_APP_SECRET", self.env)
        if mock_sec_ok and not app_sec_ok:
            self.env["KIS_APP_SECRET"] = self.env["KIS_MOCK_APP_SECRET"]
            self.fixes_applied.append("KIS_MOCK_APP_SECRET 값을 KIS_APP_SECRET로 복사")
        elif app_sec_ok and not mock_sec_ok:
            self.env["KIS_MOCK_APP_SECRET"] = self.env["KIS_APP_SECRET"]
            self.fixes_applied.append("KIS_APP_SECRET 값을 KIS_MOCK_APP_SECRET로 복사")

        # 4-d. REAL 키는 자동 복사 안 하고 경고만
        real_key_ok = _has_real_value("KIS_REAL_APP_KEY", self.env)
        real_sec_ok = _has_real_value("KIS_REAL_APP_SECRET", self.env)
        if not real_key_ok and app_key_ok:
            self.warnings.append(
                "KIS_REAL_APP_KEY 없음. KIS_APP_KEY가 있지만 "
                "모의투자/실전투자 키가 같은지 불확실하여 자동 복사 안 함. "
                "실전 키가 따로 있으면 KIS_REAL_APP_KEY에 직접 입력하세요."
            )
        if not real_sec_ok and app_sec_ok:
            self.warnings.append(
                "KIS_REAL_APP_SECRET 없음. 실전투자 앱 시크릿이 있으면 "
                "KIS_REAL_APP_SECRET에 직접 입력하세요."
            )

        # 4-e. 기본값 추가
        if not self.env.get("KIS_MOCK_ACCOUNT_PRODUCT_CODE"):
            self.env["KIS_MOCK_ACCOUNT_PRODUCT_CODE"] = "01"
            self.fixes_applied.append("KIS_MOCK_ACCOUNT_PRODUCT_CODE=01 추가")
        if not self.env.get("KIS_ACCOUNT_PRODUCT_CODE"):
            self.env["KIS_ACCOUNT_PRODUCT_CODE"] = "01"
            self.fixes_applied.append("KIS_ACCOUNT_PRODUCT_CODE=01 추가")
        if not self.env.get("KIS_USE_MOCK"):
            self.env["KIS_USE_MOCK"] = "true"
            self.fixes_applied.append("KIS_USE_MOCK=true 추가")

    # ── 5. 예시값 경고 ───────────────────────────────────────

    def check_placeholders(self) -> List[str]:
        found = []
        for key, val in self.env.items():
            if _is_placeholder(val):
                found.append(key)
                self.warnings.append(
                    f"예시값 잔존: {key}에 예시값이 들어있습니다. "
                    f"한국투자증권 API 포털에서 발급받은 실제 값을 입력하세요."
                )
        return found

    # ── 6. .env 백업 ─────────────────────────────────────────

    def backup_env(self) -> Optional[Path]:
        if not self.env_path.exists():
            return None
        backup_path = self.env_path.parent / f".env.backup_{self.ts}"
        shutil.copy2(self.env_path, backup_path)
        self._backup_path = backup_path
        print(f"[백업] {backup_path}")
        return backup_path

    # ── 7. .env 저장 ─────────────────────────────────────────

    def save_env(self) -> None:
        """보정된 env를 정리된 형식으로 저장."""

        def _line(key: str, comment: str = "") -> str:
            val = self.env.get(key, "")
            if val and not _is_placeholder(val):
                return f"{key}={val}"
            if comment:
                return f"{key}=\n# {comment}"
            return f"{key}="

        lines = [
            "# ============================================================",
            "# AI stock .env — API 키 파일 (Git에 올리지 마세요)",
            "# ============================================================",
            "",
            "# 한국투자증권 Open API - 공통/모의투자",
            _line("KIS_APP_KEY", "모의투자 App Key 입력 필요 (KIS Developers 포털)"),
            _line("KIS_APP_SECRET", "모의투자 App Secret 입력 필요"),
            _line("KIS_MOCK_APP_KEY", "모의투자 App Key (KIS_APP_KEY와 동일해도 됨)"),
            _line("KIS_MOCK_APP_SECRET", "모의투자 App Secret (KIS_APP_SECRET와 동일해도 됨)"),
            _line("KIS_MOCK_ACCOUNT_NO", "모의투자 계좌번호 입력 필요"),
            f"KIS_MOCK_ACCOUNT_PRODUCT_CODE={self.env.get('KIS_MOCK_ACCOUNT_PRODUCT_CODE', '01')}",
            "",
            "# 한국투자증권 Open API - 실전투자",
            _line("KIS_REAL_APP_KEY", "실전투자 App Key (모의투자와 다른 앱으로 등록한 경우 입력)"),
            _line("KIS_REAL_APP_SECRET", "실전투자 App Secret"),
            _line("KIS_ACCOUNT_NO", "실전투자 계좌번호"),
            f"KIS_ACCOUNT_PRODUCT_CODE={self.env.get('KIS_ACCOUNT_PRODUCT_CODE', '01')}",
            "",
            "# 실행 모드 (기본값: true = 모의투자)",
            f"KIS_USE_MOCK={self.env.get('KIS_USE_MOCK', 'true')}",
        ]

        # 기타 기존 키 (표준 키에 없는 것들)
        extra = {
            k: v for k, v in self.env.items()
            if k not in ALL_STANDARD_KEYS and v
        }
        if extra:
            lines += ["", "# 기타 기존 키"]
            for k, v in extra.items():
                lines.append(f"{k}={v}")

        content = "\n".join(lines) + "\n"
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[저장] .env 정리 완료: {self.env_path}")

    # ── 8. 진단 출력 ─────────────────────────────────────────

    def print_diagnosis(self) -> str:
        """콘솔에 진단 결과 출력. 최종 판정 반환."""
        print()
        print("=" * 58)
        print("  .env 진단 결과")
        print("=" * 58)

        # 경고
        if self.parser.parse_warnings or self.parser.duplicates:
            print()
            print("[파싱 경고]")
            for w in self.parser.parse_warnings:
                print(f"  ! {w}")
            for k in self.parser.duplicates:
                print(f"  ! 중복 키 (마지막 값 사용): {k}")

        # 비표준 키 발견
        if self.typo_renames:
            print()
            print("[비표준 키 탐지]")
            for bad, std in self.typo_renames:
                v = self.env.get(bad, "")
                print(f"  '{bad}' -> '{std}' | 값: {_mask(v)}")

        # MOCK 키 상태
        print()
        print("[MOCK]")
        for key in STANDARD_KEYS_MOCK:
            s = _key_status(key, self.env)
            v = self.env.get(key, "")
            if s == KEY_STATUS_OK:
                if "ACCOUNT_NO" in key:
                    display = _mask_account(v)
                elif "SECRET" in key:
                    display = _mask(v, 4, 4)
                elif "KEY" in key:
                    display = _mask(v, 4, 4)
                else:
                    display = v
                print(f"  {key:<36} : {s} ({display})")
            else:
                print(f"  {key:<36} : {s}")

        # REAL 키 상태
        print()
        print("[REAL]")
        for key in STANDARD_KEYS_REAL:
            s = _key_status(key, self.env)
            v = self.env.get(key, "")
            if s == KEY_STATUS_OK:
                if "ACCOUNT_NO" in key:
                    display = _mask_account(v)
                elif "SECRET" in key:
                    display = _mask(v, 4, 4)
                elif "KEY" in key:
                    display = _mask(v, 4, 4)
                else:
                    display = v
                print(f"  {key:<36} : {s} ({display})")
            else:
                print(f"  {key:<36} : {s}")

        # 자동 수정 내역
        if self.fixes_applied:
            print()
            print("[자동수정]")
            for f in self.fixes_applied:
                print(f"  - {f}")

        # 경고
        if self.warnings:
            print()
            print("[경고]")
            for w in self.warnings:
                print(f"  ! {w}")

        # 최종 판정
        verdict = self._determine_verdict()
        print()
        print("[최종판정]")
        print(f"  >>> {verdict} <<<")
        print()

        # 다음 조치
        actions = self._build_actions(verdict)
        if actions:
            print("[다음 조치사항]")
            for i, a in enumerate(actions, 1):
                print(f"  {i}. {a}")

        print("=" * 58)
        return verdict

    # ── 9. 보고서 저장 ───────────────────────────────────────

    def save_reports(self, verdict: str) -> Tuple[str, str]:
        txt_path = REPORTS_DIR / f"env_diagnosis_{self.ts}.txt"
        json_path = REPORTS_DIR / f"env_diagnosis_{self.ts}.json"

        # 마스킹된 키 상태만 저장
        key_states = {}
        for key in ALL_STANDARD_KEYS:
            s = _key_status(key, self.env)
            v = self.env.get(key, "")
            masked = ""
            if s == KEY_STATUS_OK:
                if "ACCOUNT_NO" in key:
                    masked = _mask_account(v)
                elif "SECRET" in key or "KEY" in key:
                    masked = _mask(v, 4, 4)
                else:
                    masked = v
            key_states[key] = {"status": s, "masked_value": masked}

        data = {
            "run_at": self.ts,
            "env_path": str(self.env_path),
            "backup_path": str(self._backup_path) if self._backup_path else None,
            "parse_warnings": self.parser.parse_warnings,
            "duplicate_keys": self.parser.duplicates,
            "typo_renames": [{"from": b, "to": s} for b, s in self.typo_renames],
            "fixes_applied": self.fixes_applied,
            "warnings": self.warnings,
            "key_states": key_states,
            "verdict": verdict,
            "next_actions": self._build_actions(verdict),
        }

        # JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # TXT
        txt_lines = [
            "=" * 58,
            "  .env API 키 진단 보고서",
            f"  실행시각: {self.ts}",
            "  ※ 민감정보는 마스킹 처리됨",
            "=" * 58,
        ]
        for key, info in key_states.items():
            s = info["status"]
            m = info["masked_value"]
            suffix = f" ({m})" if m else ""
            txt_lines.append(f"  {key:<36} : {s}{suffix}")
        txt_lines += [
            "",
            "[자동수정]",
        ] + [f"  - {f}" for f in self.fixes_applied] + [
            "",
            "[경고]",
        ] + [f"  ! {w}" for w in self.warnings] + [
            "",
            f"[최종판정] {verdict}",
        ]
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        print(f"\n[보고서]")
        print(f"  TXT : {txt_path}")
        print(f"  JSON: {json_path}")
        return str(txt_path), str(json_path)

    # ── 판정 ─────────────────────────────────────────────────

    def _determine_verdict(self) -> str:
        mock_key = _has_real_value("KIS_APP_KEY", self.env) or _has_real_value("KIS_MOCK_APP_KEY", self.env)
        mock_sec = _has_real_value("KIS_APP_SECRET", self.env) or _has_real_value("KIS_MOCK_APP_SECRET", self.env)
        mock_acc = _has_real_value("KIS_MOCK_ACCOUNT_NO", self.env)

        if mock_key and mock_sec and mock_acc:
            return "READY_TO_RETRY_MOCK"
        if not mock_key:
            return "STILL_MISSING_MOCK_KEY"
        if not mock_sec:
            return "STILL_MISSING_MOCK_SECRET"
        if not mock_acc:
            return "STILL_MISSING_MOCK_ACCOUNT"
        return "STILL_MISSING_MOCK_KEY"

    def _build_actions(self, verdict: str) -> List[str]:
        actions = []
        mock_key = _has_real_value("KIS_APP_KEY", self.env) or _has_real_value("KIS_MOCK_APP_KEY", self.env)
        mock_sec = _has_real_value("KIS_APP_SECRET", self.env) or _has_real_value("KIS_MOCK_APP_SECRET", self.env)
        mock_acc = _has_real_value("KIS_MOCK_ACCOUNT_NO", self.env)
        real_key = _has_real_value("KIS_REAL_APP_KEY", self.env)
        real_acc = _has_real_value("KIS_ACCOUNT_NO", self.env)

        if not mock_key:
            actions.append(
                "KIS_APP_KEY 또는 KIS_MOCK_APP_KEY 입력 필요\n"
                "     → https://apiportal.koreainvestment.com 에서 모의투자 앱 등록 후 App Key 복사"
            )
        if not mock_sec:
            actions.append("KIS_APP_SECRET 또는 KIS_MOCK_APP_SECRET 입력 필요")
        if not mock_acc:
            actions.append("KIS_MOCK_ACCOUNT_NO (모의투자 계좌번호) 입력 필요")
        if not real_key:
            actions.append(
                "KIS_REAL_APP_KEY 미입력 (선택사항) — "
                "실전 투자 시 별도 앱 등록 후 입력"
            )
        if not real_acc:
            actions.append(
                "KIS_ACCOUNT_NO (실전 계좌번호) 미입력 (선택사항)"
            )
        if verdict == "READY_TO_RETRY_MOCK":
            actions.append(
                "MOCK 키 완비됨! 재검증 실행:\n"
                "     python src/full_system_verification.py"
            )
        return actions

    # ── 전체 실행 ────────────────────────────────────────────

    def run(self) -> str:
        print()
        print("=" * 58)
        print("  AI Stock .env 진단 및 자동 보정")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 58)

        # 1. .env 존재 확인
        self.ensure_env_exists()

        # 2. 파싱
        self.load()
        print(f"[파싱] .env 읽기 완료 ({len(self.env)}개 키)")

        # 3. 비표준 키 탐지
        self.detect_typos()

        # 4. 예시값 검사
        placeholders = self.check_placeholders()
        if placeholders:
            print(f"[경고] 예시값 잔존 키: {', '.join(placeholders)}")

        # 5. 백업
        backup_path = self.backup_env()

        # 6. 보정 적용
        self.apply_fixes()

        # 7. 저장
        if self.fixes_applied:
            self.save_env()
        else:
            print("[정보] 자동 보정할 항목 없음 — .env 그대로 유지")

        # 8. 진단 출력
        verdict = self.print_diagnosis()

        # 9. 보고서 저장
        txt_path, json_path = self.save_reports(verdict)

        return verdict


# ── 기본 템플릿 ───────────────────────────────────────────────

def _build_default_template() -> str:
    return """\
# ============================================================
# AI stock .env — API 키 파일 (Git에 올리지 마세요)
# ============================================================

# 한국투자증권 Open API - 공통/모의투자
KIS_APP_KEY=
# 위 항목에 모의투자 App Key 입력 필요

KIS_APP_SECRET=
# 위 항목에 모의투자 App Secret 입력 필요

KIS_MOCK_APP_KEY=
# KIS_APP_KEY와 동일해도 됨

KIS_MOCK_APP_SECRET=
# KIS_APP_SECRET와 동일해도 됨

KIS_MOCK_ACCOUNT_NO=
# 위 항목에 모의투자 계좌번호 입력 필요

KIS_MOCK_ACCOUNT_PRODUCT_CODE=01

# 한국투자증권 Open API - 실전투자 (선택)
KIS_REAL_APP_KEY=
KIS_REAL_APP_SECRET=
KIS_ACCOUNT_NO=
KIS_ACCOUNT_PRODUCT_CODE=01

# 실행 모드
KIS_USE_MOCK=true
"""


# ── 진입점 ────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=".env API 키 진단 및 자동 보정 — 민감정보는 절대 평문 출력하지 않음"
    )
    parser.add_argument(
        "--rerun-verification",
        action="store_true",
        help="보정 후 full_system_verification.py 자동 재실행",
    )
    args = parser.parse_args()

    diag = EnvDiagnostic(ENV_PATH)
    verdict = diag.run()

    if args.rerun_verification:
        print()
        print("=" * 58)
        print("  full_system_verification.py 재실행")
        print("=" * 58)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        subprocess.run(
            [sys.executable, "src/full_system_verification.py"],
            cwd=str(PROJECT_ROOT),
            env=env,
        )

    print()
    print("─" * 58)
    print("재검증 명령어:")
    print("  python src/env_diagnosis_and_fix.py --rerun-verification")
    print("─" * 58)


if __name__ == "__main__":
    main()
