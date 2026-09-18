@echo off
chcp 65001 >nul
title 상장회사 분석 (이 창을 닫으면 앱이 꺼져요)
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
rem 필요한 프로그램이 없을 때만 설치 (매번 설치하지 않아 빨리 켜짐)
%PY% -c "import streamlit, plotly, openpyxl" >nul 2>nul || %PY% -m pip install -q -r requirements.txt
rem 이미 켜져 있으면 브라우저만 열기
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('localhost',8501)).Close();exit 0}catch{exit 1}" >nul 2>nul && (start "" http://localhost:8501 & exit /b)
echo 상장회사 분석 앱을 켜는 중... 잠시 후 브라우저가 열려요.
echo (이 창을 닫으면 앱이 꺼져요)
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
%PY% -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
