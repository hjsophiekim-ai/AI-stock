@echo off
cd /d "%~dp0"
python -m streamlit run app\streamlit_app.py --server.headless true --server.port 8503 > streamlit_stdout.log 2> streamlit_stderr.log
