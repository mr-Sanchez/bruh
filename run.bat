@echo off
REM Start the local web server using the venv if there is one, then open
REM your browser at http://127.0.0.1:8420. Press Ctrl+C in this window to
REM stop the server.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.main
) else (
    python -m app.main
)
