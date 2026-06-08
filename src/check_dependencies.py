"""의존성 확인 및 자동 설치 스크립트.

실행:
    python src/check_dependencies.py
    python src/check_dependencies.py --install-missing
"""

import argparse
import importlib
import subprocess
import sys


REQUIRED = [
    ("pykrx", "pykrx>=1.0.45"),
    ("FinanceDataReader", "finance-datareader>=0.9.50"),
    ("lightgbm", "lightgbm>=4.0.0"),
    ("pandas", "pandas>=2.0.0"),
    ("numpy", "numpy>=1.24.0"),
    ("sklearn", "scikit-learn>=1.3.0"),
    ("joblib", "joblib>=1.3.0"),
    ("tqdm", "tqdm>=4.66.0"),
    ("yaml", "pyyaml>=6.0.0"),
    ("requests", "requests>=2.31.0"),
    ("dotenv", "python-dotenv>=1.0.0"),
    ("streamlit", "streamlit>=1.30.0"),
]


def check_package(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def install_package(pip_spec: str) -> bool:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_spec, "--quiet"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  설치 실패: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="의존성 확인 및 자동 설치")
    parser.add_argument("--install-missing", action="store_true", help="누락 패키지 자동 설치")
    args = parser.parse_args()

    print("=" * 50)
    print("  AI Stock 의존성 확인")
    print("=" * 50)

    missing = []
    for import_name, pip_spec in REQUIRED:
        ok = check_package(import_name)
        status = "OK  " if ok else "MISS"
        print(f"  [{status}] {import_name:<25} ({pip_spec})")
        if not ok:
            missing.append((import_name, pip_spec))

    print("=" * 50)

    if not missing:
        print("  모든 패키지 설치됨")
        return

    print(f"  누락 패키지 {len(missing)}개:")
    for _, pip_spec in missing:
        print(f"    pip install {pip_spec}")

    if args.install_missing:
        print("\n  자동 설치 시작...")
        failed = []
        for import_name, pip_spec in missing:
            print(f"  설치 중: {pip_spec} ...", end=" ", flush=True)
            ok = install_package(pip_spec)
            if ok:
                print("완료")
            else:
                print("실패")
                failed.append(pip_spec)
        if failed:
            print(f"\n  수동 설치 필요: pip install {' '.join(failed)}")
            sys.exit(1)
        else:
            print("\n  모든 패키지 설치 완료")
    else:
        print("\n  자동 설치하려면: python src/check_dependencies.py --install-missing")
        sys.exit(1)


if __name__ == "__main__":
    main()
