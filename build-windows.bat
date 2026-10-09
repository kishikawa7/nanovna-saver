@echo off
setlocal
cd /d "%~dp0"

py -3.12 -m uv sync --locked --python 3.12
if errorlevel 1 goto :failed
py -3.12 -m uv run --locked task build-pkg-win
if errorlevel 1 goto :failed

echo Build complete: dist\nanovna-saver.exe
if not defined CI pause
exit /b 0

:failed
echo Build failed. Run setup-windows.bat first and read the error above.
if not defined CI pause
exit /b 1
