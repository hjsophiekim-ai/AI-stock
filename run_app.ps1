Set-Location "C:\Users\FURSYS\Desktop\AI stock"
Write-Host "AI Stock 자동매매 대시보드 시작 중..." -ForegroundColor Cyan
Write-Host "[주의] 기본 모드는 PAPER(가상 거래)입니다." -ForegroundColor Yellow

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "[경고] .venv가 없습니다. 전역 Python 환경을 사용합니다." -ForegroundColor Yellow
}

streamlit run app\streamlit_app.py --server.port 8501
