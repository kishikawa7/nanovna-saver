@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run setup-windows.bat first.
    if not defined CI pause
    exit /b 1
)

".venv\Scripts\python.exe" -m NanoVNASaver %*
if errorlevel 1 (
    echo NanoVNASaver failed. Read the error above.
    if not defined CI pause
    exit /b 1
)
exit /b 0
