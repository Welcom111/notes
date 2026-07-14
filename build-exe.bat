@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto build
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto build
)

echo Python 3 is not installed or was not added to PATH.
echo Python is needed only on the computer where the EXE is built.
pause
exit /b 1

:build
echo Installing build dependencies...
%PYTHON_CMD% -m pip install -r requirements-build.txt
if errorlevel 1 goto failed

echo Building QuickNotes.exe...
%PYTHON_CMD% -m PyInstaller app.py --name QuickNotes --onefile --windowed --clean --noconfirm --collect-submodules keyring.backends
if errorlevel 1 goto failed

echo.
echo Build completed: dist\QuickNotes.exe
pause
exit /b 0

:failed
echo.
echo Build failed. Review the messages above.
pause
exit /b 1
