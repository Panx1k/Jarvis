@echo off
set "JARVIS_DIR=%~dp0"
cd /d "%JARVIS_DIR%"
echo === Installing J.A.R.V.I.S. ===
set "PY=python"
where py >nul 2>nul && set "PY=py -3.12"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist ".venv" (
    %PY% -m venv .venv || (echo Python 3.10+ not found. Install it: winget install Python.Python.3.12 & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Dependency installation failed & pause & exit /b 1)
".venv\Scripts\python.exe" scripts\download_vosk_model.py
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo Done. Start with run_jarvis.bat (API keys go into .env)
pause
