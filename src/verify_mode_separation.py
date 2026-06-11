"""Verify MOCK/REAL/PAPER position file separation."""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    results = []
    pass_count = 0

    def check(name, ok, detail=""):
        nonlocal pass_count
        status = "OK" if ok else "FAIL"
        if ok:
            pass_count += 1
        msg = f"[{status}] {name}"
        if detail:
            msg += f" : {detail}"
        results.append(msg)
        print(msg)

    # 1. File existence
    mock_path = PROJECT_ROOT / "data" / "positions_mock.json"
    real_path = PROJECT_ROOT / "data" / "positions_real.json"
    paper_path = PROJECT_ROOT / "data" / "positions_paper.json"

    check("positions_mock.json 존재", mock_path.exists())
    check("positions_real.json 존재", real_path.exists())
    check("positions_paper.json 존재", paper_path.exists())

    # 2. account_mode consistency
    for path, expected_mode, label in [
        (mock_path, "MOCK", "positions_mock.json"),
        (real_path, "REAL", "positions_real.json"),
        (paper_path, "PAPER", "positions_paper.json"),
    ]:
        if not path.exists():
            check(f"{label} account_mode={expected_mode}", False, "파일 없음")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data:
                check(f"{label} account_mode={expected_mode}", True, "빈 파일 (포지션 없음)")
                continue
            wrong = [k for k, v in data.items() if isinstance(v, dict) and v.get("account_mode") and v.get("account_mode") != expected_mode]
            check(f"{label} account_mode={expected_mode}", len(wrong) == 0,
                  f"잘못된 account_mode 항목: {wrong}" if wrong else f"{len(data)}개 항목 OK")
        except Exception as e:
            check(f"{label} account_mode={expected_mode}", False, str(e))

    # 3. Page file uses MOCK/REAL tab names (not "로컬 positions.json" as tab name)
    page_path = PROJECT_ROOT / "app" / "pages" / "5_보유종목_및_매도감시.py"
    if page_path.exists():
        content = page_path.read_text(encoding="utf-8")
        has_old_tab = '"로컬 positions.json"' in content and "st.tabs" in content
        # More specific: check it's not used as a tab label in st.tabs
        import re
        tabs_matches = re.findall(r'st\.tabs\((\[.*?\])\)', content, re.DOTALL)
        old_tab_in_tabs = any("로컬 positions.json" in m for m in tabs_matches)
        check("기본 탭명 '로컬 positions.json' 제거됨", not old_tab_in_tabs,
              "이전 탭명 감지됨" if old_tab_in_tabs else "MOCK/REAL/PAPER 탭 구조 확인됨")
    else:
        check("보유종목 화면 파일 존재", False)

    # 4. sync_broker_positions returns position_path
    sync_path = PROJECT_ROOT / "src" / "sync_broker_positions.py"
    if sync_path.exists():
        content = sync_path.read_text(encoding="utf-8")
        check("sync_broker_positions position_path 반환", "position_path" in content)
    else:
        check("sync_broker_positions.py 존재", False)

    # 5. PositionManager supports mode parameter
    pm_path = PROJECT_ROOT / "src" / "position_manager.py"
    if pm_path.exists():
        content = pm_path.read_text(encoding="utf-8")
        check("PositionManager mode 파라미터 지원", "mode: str" in content or "mode=" in content)
        check("PositionManager.get_position_path 존재", "get_position_path" in content)
    else:
        check("position_manager.py 존재", False)

    total = len(results)
    print(f"\n결과: {pass_count}/{total} 통과")
    return pass_count == total


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
