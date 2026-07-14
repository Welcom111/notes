@echo off
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 (
    python app.py
    if errorlevel 1 pause
    exit /b
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 app.py
    if errorlevel 1 pause
    exit /b
)

echo Python 3 is not installed or was not added to PATH.
echo Install Python 3.10 or newer, then run install.bat once.
pause
