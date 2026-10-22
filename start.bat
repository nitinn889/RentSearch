@echo off
rem Double-click to start Rent Radar. It opens in your browser; keep this window open while you use it.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required: https://www.python.org/downloads/
  pause
  exit /b 1
)
python -m pip install --disable-pip-version-check --quiet -r requirements.txt
python app.py
pause
