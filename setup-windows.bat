@echo off
setlocal
cd /d "%~dp0"

py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.12 64-bit with the Python launcher first.
    echo See docs\WINDOWS_SETUP_JA.md for instructions.
    goto :failed
)

py -3.12 -m pip install --user uv==0.5.29
if errorlevel 1 goto :failed
py -3.12 -m uv sync --locked --no-dev --python 3.12
if errorlevel 1 goto :failed

echo Setup complete. Double-click run-windows.bat to start NanoVNASaver.
if not defined CI pause
exit /b 0

:failed
echo Setup failed. Read the error above and docs\WINDOWS_SETUP_JA.md.
if not defined CI pause
exit /b 1
