@echo off
title World Cup 2026 Dashboard
cd /d "%~dp0"

echo.
echo  ======================================================
echo    FIFA World Cup 2026 Dashboard
echo  ======================================================
echo.
echo  Starting server...  Press Ctrl+C to stop.
echo.

python app.py
if errorlevel 1 (
    echo.
    echo  ERROR: Could not start the app.
    echo  Make sure Python is installed and run:
    echo    pip install -r requirements.txt
    echo.
    pause
)
