@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
echo Downloading data in advance... (5-10 min)
%PY% prefetch.py
pause
