@echo off
REM Build EnglishSpeechRecorder.exe (one file, no console window).
cd /d "%~dp0"
pyinstaller --noconfirm --clean ^
    --name EnglishSpeechRecorder ^
    --onefile ^
    --windowed ^
    --collect-all deepgram ^
    --collect-all sounddevice ^
    app\main.py
echo.
echo Done. The executable is in dist\EnglishSpeechRecorder.exe
echo Copy .env next to the .exe (the API key is never bundled into it).
pause
