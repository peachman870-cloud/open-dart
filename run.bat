@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
%PY% -m pip install -q -r requirements.txt
echo Starting... browser will open at http://localhost:8501  (close this window to stop)
start "" cmd /c "timeout /t 6 >nul & start http://localhost:8501"
%PY% -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
pause
