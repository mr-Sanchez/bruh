@echo off
REM Start the app using the local virtual environment if there is one.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m app.main
) else (
    python -m app.main
)
