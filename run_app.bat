@echo off
cd /d "C:\Users\FURSYS\Desktop\AI stock"
echo AI Stock 자동매매 대시보드 시작 중...
echo.
echo [주의] 기본 모드는 PAPER(가상 거래)입니다.
echo [주의] 실제 주문을 원하면 API 설정 화면에서 모드를 변경하세요.
echo.

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else (
    echo [경고] .venv가 없습니다. 전역 Python 환경을 사용합니다.
)

streamlit run app\streamlit_app.py --server.port 8501 --server.headless false
pause
