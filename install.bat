@echo off
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 (
    python -m pip install -r requirements.txt
    pause
    exit /b
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m pip install -r requirements.txt
    pause
    exit /b
)

echo Python 3 is not installed or was not added to PATH.
echo Download Python from https://www.python.org/downloads/windows/
pause
